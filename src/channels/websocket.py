"""WebSocket channel based on aiohttp.

The WebSocket channel makes the robot reachable as an independent
process: ``python -m src.app`` boots a robot that exposes a WebSocket
endpoint, and any client (CLI, web UI, automated test) connects to
exchange messages with it.

Wire protocol (JSON, one message per WebSocket frame):

Client -> Server::

    {"type": "input", "text": "hello"}

Server -> Client (on connect)::

    {
        "type": "welcome",
        "session_id": "abc12345",
        "robot": {"id": "dreamgirl", "name": "Dreamgirl"}
    }

The ``robot`` block is sourced from the retained
:class:`RobotReady` broadcast on the ``robot.lifecycle`` topic which
the channel subscribes to during :meth:`start`. If the owning robot
runs without identity, the block is omitted.

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

from src.bus.messages import (
    LIFECYCLE_TOPIC,
    RobotEvent,
    RobotInput,
    RobotOutput,
    RobotReady,
    RobotShutdown,
)
from src.bus.ports import BusPort, Subscription
from src.channels.ports import ChannelPort
from src.components import ComponentCategory

logger = logging.getLogger(__name__)


def _new_run_id() -> str:
    return f"run-ws-{uuid.uuid4().hex[:10]}"


def _new_session_id() -> str:
    return uuid.uuid4().hex[:8]


class WebsocketChannel(ChannelPort):
    """aiohttp-based WebSocket bridge between bus and outside world."""

    component_type = "websocket"
    component_category = ComponentCategory.CHANNEL
    component_description = "WebSocket bridge over aiohttp. JSON frames, session-based routing."
    component_wizard_fields = ("host", "port")

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
        self._output_subscription: Subscription | None = None
        self._lifecycle_subscription: Subscription | None = None

        self._sessions: dict[str, web.WebSocketResponse] = {}
        self._run_to_session: dict[str, str] = {}
        self._actual_port: int | None = None

        self._robot_id: str | None = None
        self._robot_name: str | None = None
        self._shutting_down = False

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
        self._shutting_down = False

        self._bootstrap_identity_from_retained(bus)
        self._lifecycle_subscription = bus.subscribe_broadcast(
            LIFECYCLE_TOPIC,
            self._handle_lifecycle,
        )
        self._output_subscription = bus.subscribe(
            self._output_topic,
            self._handle_output,
        )

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
        self._shutting_down = True
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
        if self._output_subscription is not None:
            await self._output_subscription.unsubscribe()
            self._output_subscription = None
        if self._lifecycle_subscription is not None:
            await self._lifecycle_subscription.unsubscribe()
            self._lifecycle_subscription = None
        self._actual_port = None
        self._bus = None
        self._robot_id = None
        self._robot_name = None

    def _bootstrap_identity_from_retained(self, bus: BusPort) -> None:
        """Read identity synchronously from the bus's retained lifecycle event.

        Lets the channel's first welcome frame carry the correct
        identity even if the consumer task for the broadcast
        subscription hasn't run yet.
        """
        retained = bus.retained(LIFECYCLE_TOPIC)
        if isinstance(retained, RobotReady):
            self._robot_id = retained.robot_id
            self._robot_name = retained.robot_name

    async def _handle_lifecycle(self, event: RobotEvent) -> None:
        """Lifecycle hook: pick up identity from retained ``RobotReady``.

        Shutdown is handled by :meth:`drain` which is invoked
        directly by the robot after ``RobotShutdown`` is broadcast --
        not from this subscription, to avoid racing the bus consumer
        task against the drain coroutine.
        """
        if isinstance(event, RobotReady):
            self._robot_id = event.robot_id
            self._robot_name = event.robot_name

    async def drain(self) -> None:
        """Notify connected clients and close their sessions.

        Called by the robot during the shutdown grace window. We
        send a ``shutdown`` JSON frame to every open session, then
        close the WebSocket. ``stop()`` (called afterwards by the
        robot) only takes care of the listening socket and the bus
        subscriptions.

        Pulls the ``RobotShutdown`` from the bus retained slot to
        forward ``reason`` and ``grace_seconds`` to clients -- they
        are useful for UI feedback ("server is restarting in 5s").
        """
        if self._shutting_down:
            return
        self._shutting_down = True

        bus = self._bus
        reason = ""
        grace_seconds = 0.0
        if bus is not None:
            retained = bus.retained(LIFECYCLE_TOPIC)
            if isinstance(retained, RobotShutdown):
                reason = retained.reason
                grace_seconds = retained.grace_seconds

        for session_id, ws in list(self._sessions.items()):
            try:
                await ws.send_json(
                    {
                        "type": "shutdown",
                        "reason": reason,
                        "grace_seconds": grace_seconds,
                    }
                )
            except Exception:
                logger.debug(
                    "error notifying session %s of shutdown",
                    session_id,
                    exc_info=True,
                )
            try:
                await ws.close()
            except Exception:
                logger.debug(
                    "error closing session %s on shutdown",
                    session_id,
                    exc_info=True,
                )

        self._sessions.clear()
        self._run_to_session.clear()

    def _require_bus(self) -> BusPort:
        if self._bus is None:
            raise RuntimeError("WebsocketChannel is not started")
        return self._bus

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)

        session_id = _new_session_id()
        self._sessions[session_id] = ws
        welcome: dict[str, Any] = {"type": "welcome", "session_id": session_id}
        if self._robot_id or self._robot_name:
            robot_block: dict[str, Any] = {}
            if self._robot_id:
                robot_block["id"] = self._robot_id
            if self._robot_name:
                robot_block["name"] = self._robot_name
            welcome["robot"] = robot_block
        await ws.send_json(welcome)

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
