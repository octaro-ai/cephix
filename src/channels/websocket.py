"""WebSocket channel based on aiohttp.

The WebSocket channel makes the robot reachable as an independent
process: ``python -m src.app`` boots a robot that exposes a WebSocket
endpoint, and any client (CLI, web UI, automated test) connects to
exchange messages with it.

Wire protocol (JSON, one message per WebSocket frame):

Client -> Server::

    {"type": "input", "text": "hello"}

Server -> Client (on connect)::

    {"type": "welcome", "session_id": "abc12345"}

Server -> Client (kernel response)::

    {
        "type": "output",
        "text": "echo: hello",
        "run_id": "run-...",
        "source": "kernel.echo",
        "payload": {...},
        "timestamp": "2026-..."
    }

Routing semantics: each connection is a session with its own
``session_id``. Every incoming text creates a new ``run_id`` that the
channel maps back to its session. ``RobotOutput`` events are routed
back to the session that started the run; outputs without a known
``run_id`` are silently dropped (no broadcast across sessions).

The channel is constructed with its own configuration only (host,
port, topics, ...); the bus is injected at :meth:`start` time by the
robot.

Iteration 2 ships without authentication or transport encryption: the
channel is bound to localhost by default and is intended for trusted
local use.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from aiohttp import WSMsgType, web

from src.bus.messages import RobotEvent, RobotInput, RobotOutput
from src.bus.ports import BusPort, Subscription

logger = logging.getLogger(__name__)


def _new_run_id() -> str:
    return f"run-ws-{uuid.uuid4().hex[:10]}"


def _new_session_id() -> str:
    return uuid.uuid4().hex[:8]


class WebsocketChannel:
    """aiohttp-based WebSocket bridge between bus and outside world."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        path: str = "/ws",
        principal_template: str = "ws:{session}",
        input_topic: str = "input.message",
        output_topic: str = "output.message",
    ) -> None:
        self._host = host
        self._port = port
        self._path = path
        self._principal_template = principal_template
        self._input_topic = input_topic
        self._output_topic = output_topic

        self._bus: BusPort | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._subscription: Subscription | None = None

        self._sessions: dict[str, web.WebSocketResponse] = {}
        self._run_to_session: dict[str, str] = {}
        self._actual_port: int | None = None

    @property
    def actual_port(self) -> int | None:
        """Bound TCP port after :meth:`start` (useful for ``port=0``)."""
        return self._actual_port

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    async def start(self, bus: BusPort) -> None:
        if self._runner is not None:
            return
        self._bus = bus

        self._subscription = bus.subscribe(self._output_topic, self._handle_output)

        app = web.Application()
        app.router.add_get(self._path, self._handle_ws)

        self._runner = web.AppRunner(app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()

        server = self._site._server  # type: ignore[attr-defined]
        if server is not None and getattr(server, "sockets", None):
            self._actual_port = server.sockets[0].getsockname()[1]
        else:
            self._actual_port = self._port

        logger.info("WebsocketChannel listening on ws://%s:%s%s", self._host, self._actual_port, self._path)

    async def stop(self) -> None:
        for ws in list(self._sessions.values()):
            try:
                await ws.close()
            except Exception:
                logger.debug("error while closing websocket", exc_info=True)
        self._sessions.clear()
        self._run_to_session.clear()

        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        if self._subscription is not None:
            await self._subscription.unsubscribe()
            self._subscription = None
        self._actual_port = None
        self._bus = None

    def _require_bus(self) -> BusPort:
        if self._bus is None:
            raise RuntimeError("WebsocketChannel is not started")
        return self._bus

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)

        session_id = _new_session_id()
        self._sessions[session_id] = ws
        await ws.send_json({"type": "welcome", "session_id": session_id})

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_text_frame(session_id, msg.data)
                elif msg.type == WSMsgType.ERROR:
                    logger.warning(
                        "websocket session %s closed with error: %s",
                        session_id,
                        ws.exception(),
                    )
                    break
        finally:
            self._sessions.pop(session_id, None)
            for run_id, sid in list(self._run_to_session.items()):
                if sid == session_id:
                    self._run_to_session.pop(run_id, None)

        return ws

    async def _handle_text_frame(self, session_id: str, raw: str) -> None:
        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("ignoring non-json frame on session %s", session_id)
            return

        msg_type = data.get("type")
        if msg_type != "input":
            logger.debug("ignoring unsupported message type %r on session %s", msg_type, session_id)
            return

        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            return

        run_id = _new_run_id()
        self._run_to_session[run_id] = session_id

        principal = self._principal_template.format(session=session_id)
        payload: dict[str, Any] = dict(data.get("payload") or {})
        payload.setdefault("session_id", session_id)

        bus = self._require_bus()
        await bus.publish(
            RobotInput(
                topic=self._input_topic,
                principal=principal,
                source="channel.websocket",
                run_id=run_id,
                text=text.strip(),
                payload=payload,
            )
        )

    async def _handle_output(self, event: RobotEvent) -> None:
        if not isinstance(event, RobotOutput):
            return

        session_id = self._run_to_session.get(event.run_id)
        if session_id is None:
            payload_session = event.payload.get("session_id") if isinstance(event.payload, dict) else None
            if isinstance(payload_session, str):
                session_id = payload_session

        if session_id is None:
            return

        ws = self._sessions.get(session_id)
        if ws is None or ws.closed:
            return

        try:
            await ws.send_json(
                {
                    "type": "output",
                    "text": event.text,
                    "run_id": event.run_id,
                    "source": event.source,
                    "payload": dict(event.payload) if isinstance(event.payload, dict) else {},
                    "timestamp": event.timestamp,
                }
            )
        except (ConnectionResetError, asyncio.CancelledError):
            raise
        except Exception:
            logger.exception("failed to send output to session %s", session_id)
