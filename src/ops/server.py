"""Out-of-band control plane: WebSocket maintenance hatch.

The control plane is *not* a bus participant. It runs on its own TCP
port (loopback only) with token authentication and exposes the
sovereign operations from :mod:`src.ops.operations`. This is by
design: when the bus is wedged the control plane must still be
reachable so the operator can inspect and stop the robot. Analog:
IPMI/BMC out-of-band management on a server, or Magic SysRq in the
Linux kernel.

Wire protocol (JSON, one message per frame)::

    Client -> Server (Auth)
    {"type": "auth", "token": "..."}

    Server -> Client
    {"type": "auth.ok"}             or {"type": "auth.fail", "reason": "..."}

    Client -> Server (Operation)
    {"type": "request", "id": "req-xxx", "op": "status"}
    {"type": "request", "id": "req-xxx", "op": "shutdown",
     "params": {"force": false}}

    Server -> Client (Response)
    {"type": "response", "id": "req-xxx", "ok": true,  "result": {...}}
    {"type": "response", "id": "req-xxx", "ok": false, "error": "..."}

Auto-port resolution: the configured ``port`` is tried first, then
each port in ``port_range``, finally port ``0`` (OS picks). The chosen
port is exposed via :attr:`ControlPlane.endpoint` and logged.
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import TYPE_CHECKING, Any

from aiohttp import WSMsgType, web

from src.configuration import CONTROL_PLANE_TOKEN_ENV
from src.ops.operations import UnknownOperation, dispatch

if TYPE_CHECKING:
    from src.robot import ControlPlaneConfig, Robot


logger = logging.getLogger(__name__)


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class ControlPlaneAuthRequired(RuntimeError):
    """Raised when the control plane is asked to start without a token.

    Authentication is mandatory by design: the control plane exposes
    sovereign operations (status, shutdown, ...) and must never be
    reachable without a credential. See :class:`ControlPlane.start`.
    """


class ControlPlane:
    """WebSocket-based control plane bound to a single :class:`Robot`."""

    def __init__(
        self,
        *,
        config: ControlPlaneConfig,
        token: str | None,
        robot: Robot,
    ) -> None:
        self._config = config
        self._token = token
        self._robot = robot

        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._actual_port: int | None = None
        self._sessions: set[web.WebSocketResponse] = set()

    # ---- public ----------------------------------------------------------

    @property
    def actual_port(self) -> int | None:
        return self._actual_port

    @property
    def endpoint(self) -> str | None:
        if self._actual_port is None:
            return None
        return f"ws://{self._config.host}:{self._actual_port}{self._config.path}"

    async def start(self) -> None:
        if self._runner is not None:
            return
        if not self._token:
            raise ControlPlaneAuthRequired(
                "control plane refuses to start without an authentication "
                f"token (set {CONTROL_PLANE_TOKEN_ENV} in the bot-local .env)."
            )
        if self._config.host not in _LOOPBACK_HOSTS:
            logger.warning(
                "control plane bound to non-loopback host %r -- "
                "the maintenance hatch should normally be local-only",
                self._config.host,
            )

        app = web.Application()
        app.router.add_get(self._config.path, self._handle_ws)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._app = app

        bound_port = await self._bind_with_resolution()
        self._actual_port = bound_port
        logger.info(
            "ControlPlane listening on %s (token: %s)",
            self.endpoint,
            "yes" if self._token else "no (insecure!)",
        )

    async def stop(self) -> None:
        for ws in list(self._sessions):
            try:
                await ws.close()
            except Exception:
                logger.debug("error closing control-plane session", exc_info=True)
        self._sessions.clear()

        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self._app = None
        self._actual_port = None

    # ---- port resolution --------------------------------------------------

    async def _bind_with_resolution(self) -> int:
        """Try ``port``, then ``port_range``, then ``0`` as last resort."""
        assert self._runner is not None
        candidates: list[int] = []
        if self._config.port:
            candidates.append(self._config.port)
        low, high = self._config.port_range
        for p in range(low, high + 1):
            if p != self._config.port:
                candidates.append(p)
        candidates.append(0)  # OS picks; final fallback

        last_error: OSError | None = None
        for port in candidates:
            try:
                site = web.TCPSite(self._runner, self._config.host, port)
                await site.start()
            except OSError as exc:
                last_error = exc
                continue
            self._site = site
            server = site._server  # type: ignore[attr-defined]
            if server is not None and getattr(server, "sockets", None):
                bound = server.sockets[0].getsockname()[1]
            else:
                bound = port
            if bound != self._config.port:
                logger.info(
                    "control plane port %d unavailable, bound %d instead",
                    self._config.port,
                    bound,
                )
            return bound

        raise RuntimeError(
            f"control plane could not bind on host {self._config.host!r}: "
            f"{last_error}"
        )

    # ---- handlers ---------------------------------------------------------

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        self._sessions.add(ws)

        authed = False
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    if msg.type == WSMsgType.ERROR:
                        logger.warning(
                            "control plane websocket error: %s",
                            ws.exception(),
                        )
                    break

                try:
                    frame: dict[str, Any] = json.loads(msg.data)
                except json.JSONDecodeError:
                    await ws.send_json({"type": "error", "error": "invalid json"})
                    continue

                kind = frame.get("type")
                if not authed:
                    authed = await self._handle_auth(ws, frame)
                    if not authed:
                        # auth.fail already sent; drop the connection.
                        await ws.close()
                        break
                    continue

                if kind == "request":
                    await self._handle_request(ws, frame)
                else:
                    await ws.send_json(
                        {"type": "error", "error": f"unsupported frame type: {kind}"}
                    )
        finally:
            self._sessions.discard(ws)

        return ws

    async def _handle_auth(
        self,
        ws: web.WebSocketResponse,
        frame: dict[str, Any],
    ) -> bool:
        if not self._token:
            # Defense-in-depth: ``start()`` already refuses to bind
            # in this state, so reaching this branch implies an
            # internal bug. Reject every connection rather than
            # degrading to open access.
            await ws.send_json(
                {"type": "auth.fail", "reason": "control plane not configured"}
            )
            return False
        if frame.get("type") != "auth":
            await ws.send_json({"type": "auth.fail", "reason": "auth required"})
            return False
        provided = str(frame.get("token") or "")
        if not _constant_time_eq(provided, self._token):
            await ws.send_json({"type": "auth.fail", "reason": "invalid token"})
            return False
        await ws.send_json({"type": "auth.ok"})
        return True

    async def _handle_request(
        self,
        ws: web.WebSocketResponse,
        frame: dict[str, Any],
    ) -> None:
        req_id = str(frame.get("id") or "")
        op = str(frame.get("op") or "")
        params = frame.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        try:
            result = await dispatch(self._robot, op, params)
        except UnknownOperation as exc:
            await ws.send_json(
                {"type": "response", "id": req_id, "ok": False, "error": str(exc)}
            )
            return
        except TypeError as exc:
            await ws.send_json(
                {
                    "type": "response",
                    "id": req_id,
                    "ok": False,
                    "error": f"bad params for {op}: {exc}",
                }
            )
            return
        except Exception as exc:
            logger.exception("control plane op %r failed", op)
            await ws.send_json(
                {
                    "type": "response",
                    "id": req_id,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return

        await ws.send_json(
            {"type": "response", "id": req_id, "ok": True, "result": result}
        )


def _constant_time_eq(a: str, b: str) -> bool:
    """Compare two strings in constant time to resist timing attacks."""
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
