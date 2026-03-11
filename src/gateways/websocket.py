from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
import secrets
from typing import Any
from uuid import uuid4

from aiohttp import WSMsgType, web

from src.domain import ControlRequest, OutboundMessage, ReplyTarget, RobotEvent
from src.ports import PairingRegistryPort
from src.telemetry import WideEvent
from src.utils import new_id

_SCOPE_CHAT = "chat"
_SCOPE_TELEMETRY = "telemetry"
_SCOPE_ADMIN = "admin"
_DEFAULT_SCOPES = frozenset({_SCOPE_CHAT})
_ALLOWED_SCOPES = frozenset({_SCOPE_CHAT, _SCOPE_TELEMETRY, _SCOPE_ADMIN})
_LOOPBACK_BINDS = frozenset({"127.0.0.1", "::1", "localhost"})


@dataclass
class _ClientSession:
    client_id: str
    ws: web.WebSocketResponse
    remote_addr: str
    device_id: str | None = None
    sender_id: str = ""
    authenticated: bool = False
    granted_scopes: frozenset[str] = frozenset()
    telemetry_enabled: bool = False


class WebSocketChannel:
    def __init__(
        self,
        *,
        channel_id: str = "ws",
        bind: str = "127.0.0.1",
        port: int = 8765,
        access_token: str = "",
        admin_token: str = "",
        auto_approve_loopback: bool = True,
        pairings: PairingRegistryPort,
    ) -> None:
        self.channel_id = channel_id
        self.bind = bind
        self.port = port
        self.access_token = access_token
        self.admin_token = admin_token
        self.auto_approve_loopback = auto_approve_loopback
        self.pairings = pairings
        self._incoming_events: list[RobotEvent] = []
        self._incoming_control_requests: list[ControlRequest] = []
        self._public_info: dict[str, Any] = {}
        self._app = web.Application()
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_get("/ws", self._handle_ws)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._clients: dict[str, _ClientSession] = {}
        self.bound_port = port
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_public_info(self, info: dict[str, Any]) -> None:
        self._public_info = dict(info)

    def update_auth_config(
        self,
        *,
        access_token: str,
        admin_token: str,
        auto_approve_loopback: bool,
    ) -> None:
        self.access_token = access_token
        self.admin_token = admin_token
        self.auto_approve_loopback = auto_approve_loopback

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self.bind, port=self.port)
        await self._site.start()
        if self._site._server is not None and self._site._server.sockets:
            self.bound_port = self._site._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for session in list(self._clients.values()):
            if not session.ws.closed:
                await session.ws.close()
        self._clients.clear()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    def drain_events(self) -> list[RobotEvent]:
        events = list(self._incoming_events)
        self._incoming_events.clear()
        return events

    def drain_control_requests(self) -> list[ControlRequest]:
        requests = list(self._incoming_control_requests)
        self._incoming_control_requests.clear()
        return requests

    def send(self, target: ReplyTarget, message: OutboundMessage) -> None:
        payload = {"type": "response", "content": message.text, "metadata": {"channel": self.channel_id}}
        self._schedule_send(target.recipient_id, payload)

    def send_chunk(self, target: ReplyTarget, token: str) -> None:
        payload = {"type": "response_chunk", "content": token}
        self._schedule_send(target.recipient_id, payload)

    def send_chunk_clear(self, target: ReplyTarget) -> None:
        payload = {"type": "response_chunk_clear"}
        self._schedule_send(target.recipient_id, payload)

    def send_control_payload(self, recipient_id: str, payload: dict[str, Any]) -> None:
        self._schedule_send(recipient_id, payload)

    def append(self, event: WideEvent) -> None:
        payload = {"type": "telemetry", "event": asdict(event)}
        for session in list(self._clients.values()):
            if session.telemetry_enabled and _SCOPE_TELEMETRY in session.granted_scopes:
                self._schedule_send(session.client_id, payload)

    def _schedule_send(self, client_id: str, payload: dict[str, Any]) -> None:
        session = self._clients.get(client_id)
        if session is None:
            return
        loop = self._loop
        if loop is None:
            return
        # Use call_soon_threadsafe so this works from both the event loop
        # thread and executor threads (e.g. when the kernel streams tokens).
        loop.call_soon_threadsafe(loop.create_task, self._send_json(session.ws, payload))

    @staticmethod
    async def _send_json(ws: web.WebSocketResponse, payload: dict[str, Any]) -> None:
        if not ws.closed:
            await ws.send_json(payload)

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "channel": self.channel_id,
                "clients": len(self._clients),
                "bind": self.bind,
                "port": self.port,
                "auth": {
                    "access_token_configured": bool(self.access_token),
                    "admin_token_configured": bool(self.admin_token),
                    "auto_approve_loopback": self.auto_approve_loopback,
                    "pending_pairings": len(self.pairings.list_pairings()),
                },
            }
        )

    async def _handle_ws(self, request: web.Request) -> web.StreamResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        client_id = uuid4().hex[:12]
        session = _ClientSession(client_id=client_id, ws=ws, remote_addr=request.remote or "")
        self._clients[client_id] = session
        await ws.send_json(
            {
                "type": "auth_required",
                "client_id": client_id,
                "channel": self.channel_id,
                "server": self._public_server_info(),
                "required": ["auth.hello"],
            }
        )

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_text(session, msg.data)
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            self._clients.pop(client_id, None)

        return ws

    async def _handle_text(self, session: _ClientSession, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await self._send_json(session.ws, {"type": "error", "content": "Invalid JSON"})
            return

        msg_type = data.get("type", "message")

        if msg_type == "ping":
            await self._send_json(
                session.ws,
                {
                    "type": "info",
                    "client_id": session.client_id,
                    "channel": self.channel_id,
                    "authenticated": session.authenticated,
                    "granted_scopes": sorted(session.granted_scopes),
                    "server": self._public_server_info(),
                },
            )
            return

        if msg_type == "auth.hello":
            await self._handle_auth_hello(session, data)
            return

        if msg_type == "subscribe_telemetry":
            if not session.authenticated or _SCOPE_TELEMETRY not in session.granted_scopes:
                await self._send_json(session.ws, {"type": "error", "content": "Telemetry scope required."})
                return
            session.telemetry_enabled = bool(data.get("enabled", True))
            await self._send_json(
                session.ws,
                {"type": "ack", "content": "telemetry_subscription_updated", "enabled": session.telemetry_enabled},
            )
            return

        if not session.authenticated:
            await self._send_json(session.ws, {"type": "error", "content": "Authenticate first."})
            return

        if msg_type.startswith("admin."):
            await self._handle_admin_message(session, data)
            return

        if msg_type == "session.new":
            await self._handle_session_new(session)
            return

        if msg_type == "session.list":
            await self._handle_session_list(session, data)
            return

        if msg_type != "message":
            await self._send_json(session.ws, {"type": "error", "content": f"Unknown message type: {msg_type}"})
            return

        if _SCOPE_CHAT not in session.granted_scopes:
            await self._send_json(session.ws, {"type": "error", "content": "Chat scope required."})
            return
        if self._public_info.get("onboarding_required"):
            await self._send_json(
                session.ws,
                {
                    "type": "error",
                    "content": "Robot onboarding required. Connect with admin scope and complete onboarding first.",
                },
            )
            return

        sender_id = str(data.get("sender_id", "owner"))
        session.sender_id = sender_id
        conversation_id = data.get("conversation_id") or f"ws-{session.client_id}"
        content = str(data.get("content", ""))

        event = RobotEvent(
            event_id=new_id("evt"),
            event_type="message.received",
            source_channel=self.channel_id,
            sender_id=sender_id,
            sender_name=str(data.get("sender_name") or sender_id),
            conversation_id=str(conversation_id),
            text=content,
            metadata=dict(data.get("metadata") or {}),
            reply_target=ReplyTarget(
                channel=self.channel_id,
                recipient_id=session.client_id,
                conversation_id=str(conversation_id),
                mode="reply",
            ),
        )
        self._incoming_events.append(event)
        await self._send_json(session.ws, {"type": "ack", "content": "message_queued", "event_id": event.event_id})

    async def _handle_session_new(self, session: _ClientSession) -> None:
        conversation_id = new_id("conv")
        await self._send_json(
            session.ws,
            {"type": "session.new", "conversation_id": conversation_id},
        )

    async def _handle_session_list(self, session: _ClientSession, data: dict[str, Any]) -> None:
        self._incoming_control_requests.append(
            ControlRequest(
                request_id=new_id("ctrl"),
                source_channel=self.channel_id,
                recipient_id=session.client_id,
                request_type="session.list",
                payload={},
            )
        )

    async def _handle_auth_hello(self, session: _ClientSession, data: dict[str, Any]) -> None:
        requested_scopes = self._normalize_scopes(data.get("requested_scopes"))
        device_id = str(data.get("device_id") or "").strip()
        if not device_id:
            await self._send_json(session.ws, {"type": "error", "content": "device_id is required."})
            return

        session.device_id = device_id
        access_token = str(data.get("token") or "")
        admin_token = str(data.get("admin_token") or "")
        approved_scopes = set(self.pairings.get_approved_scopes(device_id))
        granted_scopes: set[str] = set()

        if _SCOPE_ADMIN in requested_scopes:
            if not self._token_matches(admin_token, self.admin_token):
                await self._send_json(session.ws, {"type": "error", "content": "Invalid admin token."})
                return
            granted_scopes.update({_SCOPE_ADMIN, _SCOPE_CHAT, _SCOPE_TELEMETRY})

        is_loopback = self._is_loopback_remote(session.remote_addr)
        needs_access = bool(requested_scopes - {_SCOPE_ADMIN})
        has_access_token = bool(self.access_token) and self._token_matches(access_token, self.access_token)

        if needs_access:
            if is_loopback and self.auto_approve_loopback:
                granted_scopes.add(_SCOPE_CHAT)
                if has_access_token:
                    granted_scopes.add(_SCOPE_TELEMETRY)
            else:
                if not has_access_token:
                    await self._send_json(session.ws, {"type": "error", "content": "Invalid access token."})
                    return
                granted_scopes.update(approved_scopes & set(requested_scopes))
                missing_scopes = set(requested_scopes) - granted_scopes - {_SCOPE_ADMIN}
                if missing_scopes:
                    pending = self.pairings.queue_pairing(
                        device_id=device_id,
                        remote_addr=session.remote_addr,
                        requested_scopes=missing_scopes,
                    )
                    await self._send_json(
                        session.ws,
                        {
                            "type": "auth.pairing_required",
                            "device_id": device_id,
                            "pairing_id": pending.pairing_id,
                            "pairing_code": pending.pairing_code,
                            "requested_scopes": sorted(missing_scopes),
                        },
                    )
                    return

        session.authenticated = True
        session.granted_scopes = frozenset(granted_scopes)
        await self._send_json(
            session.ws,
            {
                "type": "auth.ok",
                "client_id": session.client_id,
                "device_id": device_id,
                "granted_scopes": sorted(session.granted_scopes),
                "server": self._public_server_info(),
            },
        )

    async def _handle_admin_message(self, session: _ClientSession, data: dict[str, Any]) -> None:
        if _SCOPE_ADMIN not in session.granted_scopes:
            await self._send_json(session.ws, {"type": "error", "content": "Admin scope required."})
            return

        msg_type = str(data.get("type") or "")
        allowed_types = {
            "admin.status",
            "admin.onboarding.status",
            "admin.onboarding.apply",
            "admin.pairing.list",
            "admin.pairing.approve",
        }
        if msg_type not in allowed_types:
            await self._send_json(session.ws, {"type": "error", "content": f"Unknown admin message type: {msg_type}"})
            return

        self._incoming_control_requests.append(
            ControlRequest(
                request_id=new_id("ctrl"),
                source_channel=self.channel_id,
                recipient_id=session.client_id,
                request_type=msg_type,
                payload={key: value for key, value in data.items() if key != "type"},
            )
        )

    @staticmethod
    def _normalize_scopes(raw_scopes: Any) -> frozenset[str]:
        if not raw_scopes:
            return _DEFAULT_SCOPES
        normalized = {str(scope) for scope in raw_scopes if str(scope) in _ALLOWED_SCOPES}
        return frozenset(normalized or _DEFAULT_SCOPES)

    @staticmethod
    def _token_matches(candidate: str, expected: str) -> bool:
        if not expected:
            return candidate == ""
        return secrets.compare_digest(candidate, expected)

    @staticmethod
    def _is_loopback_remote(remote_addr: str) -> bool:
        return remote_addr in _LOOPBACK_BINDS

    def _public_server_info(self) -> dict[str, Any]:
        return {
            **self._public_info,
            "channel": self.channel_id,
            "auth": {
                "access_token_configured": bool(self.access_token),
                "admin_token_configured": bool(self.admin_token),
                "auto_approve_loopback": self.auto_approve_loopback,
            },
        }
