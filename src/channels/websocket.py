"""WebSocket channel based on aiohttp.

The WebSocket channel makes the robot reachable as an independent
process: ``python -m src.app`` boots a robot that exposes a WebSocket
endpoint, and any client (CLI, web UI, automated test) connects to
exchange messages with it.

Wire protocol (JSON, one message per WebSocket frame):

Client -> Server::

    {"type": "input", "message": "hello"}

Server -> Client (on connect)::

    {
        "type": "welcome",
        "session_id": "abc12345",
        "robot": {"id": "dreamgirl", "name": "Dreamgirl"}
    }

The ``robot`` block is sourced from the retained
:class:`RobotLifecycle` (``phase="ready"`` or ``"boot"``) broadcast
on the ``robot.lifecycle`` topic which the channel subscribes to
during :meth:`start`. If the owning robot runs without identity,
the block is omitted.

Server -> Client (kernel response, success)::

    {
        "type": "output",
        "status": "ok",
        "message": "echo: hello",
        "run_id": "run-...",
        "source": "kernel.base",
        "payload": {...},
        "timestamp": "2026-..."
    }

Server -> Client (kernel response, failure)::

    {
        "type": "output",
        "status": "error",
        "message": "Sorry, the actor timed out.",
        "error": {"code": "timeout",
                  "message": "actor base timed out after 30s",
                  "details": {...}},
        "run_id": "run-...",
        "source": "kernel.base",
        "payload": {},
        "timestamp": "2026-..."
    }

The ``status`` field discriminates between sysout-like and
syserr-like deliveries: clients should render ``status="error"``
distinctly (red banner, retry affordance, ...). The optional
``error`` block mirrors the bus-side :class:`ErrorInfo`.

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

Auto-port resolution: ``port`` is the preferred port. If ``port_range``
is set (``[low, high]``) and the preferred port is busy, the channel
walks the range; ``port=0`` as a final fallback lets the OS pick any
free port. The bound port is exposed via :attr:`actual_port` and
logged at :meth:`start` time. Same mechanic as the ControlPlane, so
multiple bots co-exist on the same host without conflicting on a
fixed port.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Sequence
from typing import Any

from aiohttp import WSMsgType, web

from src.bus.messages import (
    INPUT_TOPIC,
    LIFECYCLE_TOPIC,
    OUTPUT_TOPIC,
    RobotEvent,
    RobotInput,
    RobotLifecycle,
    RobotOutput,
)
from src.bus.ports import BusPort, Subscription
from src.channels.ports import ChannelPort
from src.components import ComponentCategory

logger = logging.getLogger(__name__)


def _new_run_id() -> str:
    return f"run-ws-{uuid.uuid4().hex[:10]}"


def _new_session_id() -> str:
    return uuid.uuid4().hex[:8]


def _normalise_port_range(
    raw: Sequence[int] | None,
) -> tuple[int, int] | None:
    """Validate and tuple-ise an optional ``port_range`` argument.

    Accepts ``None`` (no range), or any 2-element sequence of ints with
    ``low <= high``. Raises :class:`ValueError` for malformed input so
    a config error surfaces at construction time rather than during
    the boot phase.
    """
    if raw is None:
        return None
    items = list(raw)
    if len(items) != 2:
        raise ValueError(
            f"port_range must be a 2-element sequence [low, high], "
            f"got {items!r}"
        )
    low, high = int(items[0]), int(items[1])
    if low < 0 or high < 0:
        raise ValueError(
            f"port_range values must be non-negative, got [{low}, {high}]"
        )
    if low > high:
        raise ValueError(
            f"port_range: low must be <= high, got [{low}, {high}]"
        )
    return low, high


class WebsocketChannel(ChannelPort):
    """aiohttp-based WebSocket bridge between bus and outside world."""

    component_name = "websocket"
    component_category = ComponentCategory.CHANNEL
    component_description = "WebSocket bridge over aiohttp. JSON frames, session-based routing."

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        port_range: Sequence[int] | None = None,
        path: str = "/ws",
        principal_template: str = "ws:{session}",
        input_topic: str = INPUT_TOPIC,
        output_topic: str = OUTPUT_TOPIC,
    ) -> None:
        self._host = host
        self._port = port
        self._port_range: tuple[int, int] | None = _normalise_port_range(
            port_range
        )
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

        self._actual_port = await self._bind_with_resolution()

        logger.info(
            "WebsocketChannel listening on ws://%s:%s%s",
            self._host,
            self._actual_port,
            self._path,
        )

    async def _bind_with_resolution(self) -> int:
        """Try ``port``, then ``port_range``, then ``0`` as last resort.

        If no ``port_range`` is configured, only the preferred ``port``
        is tried -- a conflict surfaces as the ``OSError`` from
        :meth:`aiohttp.web.TCPSite.start` so an unconfigured fixed-port
        deployment fails loudly. With a range configured, conflicts are
        resolved silently in favour of the next free port (with a log
        line at INFO so the actual binding stays observable).
        """
        assert self._runner is not None

        if self._port_range is None:
            # Single-shot bind: let the OSError propagate so a fixed
            # deployment fails loudly on a port conflict.
            site = web.TCPSite(self._runner, self._host, self._port)
            await site.start()
            self._site = site
            return self._extract_bound_port(site, self._port)

        candidates: list[int] = [self._port]
        low, high = self._port_range
        for p in range(low, high + 1):
            if p != self._port:
                candidates.append(p)
        candidates.append(0)  # OS picks; final fallback

        last_error: OSError | None = None
        for port in candidates:
            try:
                site = web.TCPSite(self._runner, self._host, port)
                await site.start()
            except OSError as exc:
                last_error = exc
                continue
            self._site = site
            bound = self._extract_bound_port(site, port)
            if bound != self._port:
                logger.info(
                    "WebsocketChannel port %d unavailable, bound %d instead",
                    self._port,
                    bound,
                )
            return bound

        raise RuntimeError(
            f"WebsocketChannel could not bind on host {self._host!r}: "
            f"{last_error}"
        )

    @staticmethod
    def _extract_bound_port(site: web.TCPSite, fallback: int) -> int:
        """Read the actually-bound port off the aiohttp site."""
        server = site._server  # type: ignore[attr-defined]
        if server is not None and getattr(server, "sockets", None):
            return int(server.sockets[0].getsockname()[1])
        return fallback

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
        if isinstance(retained, RobotLifecycle) and retained.phase in ("boot", "ready"):
            self._robot_id = retained.robot_id
            self._robot_name = retained.robot_name

    async def _handle_lifecycle(self, event: RobotEvent) -> None:
        """Lifecycle hook: pick up identity from retained boot/ready events.

        Shutdown is handled by :meth:`drain` which is invoked
        directly by the robot after the lifecycle ``shutdown`` event
        is broadcast -- not from this subscription, to avoid racing
        the bus consumer task against the drain coroutine.
        """
        if isinstance(event, RobotLifecycle) and event.phase in ("boot", "ready"):
            self._robot_id = event.robot_id
            self._robot_name = event.robot_name

    async def drain(self) -> None:
        """Notify connected clients and close their sessions.

        Called by the robot during the shutdown grace window. We
        send a ``shutdown`` JSON frame to every open session, then
        close the WebSocket. ``stop()`` (called afterwards by the
        robot) only takes care of the listening socket and the bus
        subscriptions.

        Pulls the lifecycle ``shutdown`` event from the bus retained
        slot to forward ``message`` to clients -- useful for UI
        feedback. The grace window is robot-internal policy and not
        relayed to clients; their meaningful signal is the close of
        the WebSocket.
        """
        if self._shutting_down:
            return
        self._shutting_down = True

        bus = self._bus
        message = ""
        if bus is not None:
            retained = bus.retained(LIFECYCLE_TOPIC)
            if isinstance(retained, RobotLifecycle) and retained.phase == "shutdown":
                message = retained.message

        for session_id, ws in list(self._sessions.items()):
            try:
                await ws.send_json(
                    {
                        "type": "shutdown",
                        "message": message,
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

        # Accept ``message`` (canonical) and ``text`` (legacy).
        message = data.get("message")
        if not isinstance(message, str):
            message = data.get("text")
        if not isinstance(message, str) or not message.strip():
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
                source_id=self.instance_id,
                run_id=run_id,
                message=message.strip(),
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

        frame: dict[str, Any] = {
            "type": "output",
            "status": event.status,
            "message": event.message,
            "run_id": event.run_id,
            "source": event.source,
            "payload": dict(event.payload) if isinstance(event.payload, dict) else {},
            "timestamp": event.timestamp,
        }
        if event.error is not None:
            frame["error"] = {
                "code": event.error.code,
                "message": event.error.message,
                "details": dict(event.error.details),
            }

        try:
            await ws.send_json(frame)
        except (ConnectionResetError, asyncio.CancelledError):
            raise
        except Exception:
            logger.exception("failed to send output to session %s", session_id)
