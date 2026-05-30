"""Sovereign operations of the control plane.

Each operation is a plain async function that takes a :class:`Robot`
and returns a JSON-serialisable result. The control plane's WebSocket
layer only translates wire frames into these calls -- the operations
themselves are not HTTP-aware and unit-test without any server setup.

These are deliberately the *sovereign* operations: they bypass the
bus and act on the python objects the robot holds. That is the whole
reason for the out-of-band control plane -- if the bus is wedged we
can still inspect and stop things via these calls.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.robot import Robot


logger = logging.getLogger(__name__)


async def status(robot: Robot) -> dict[str, Any]:
    """Snapshot of the robot's current state.

    Always works -- even with the bus down, even mid-shutdown.
    Returns enough information for an operator to triage what is
    wrong (which phase the robot is in, whether the bus is up, what
    the manifest looks like).
    """
    started_at = robot.started_at
    uptime_s: float | None = None
    if started_at is not None:
        uptime_s = (datetime.now(UTC) - started_at).total_seconds()

    bus = robot.bus
    bus_running: bool = False
    if bus is not None:
        # is_running is implementation-specific (AsyncioBus exposes
        # it). Degrade gracefully if a bus port doesn't have it.
        bus_running = bool(getattr(bus, "is_running", False))

    return {
        "identity": {
            "id": robot.identity.id,
            "name": robot.identity.name,
            "label": robot.identity.label,
        },
        "robot_run_id": robot.robot_run_id,
        "phase": robot.phase.value,
        "started_at": started_at.isoformat() if started_at else None,
        "uptime_s": uptime_s,
        "bus": {
            "attached": bus is not None,
            "running": bus_running,
        },
        "components": [
            {
                "category": c.category,
                "name": c.name,
                "description": c.description,
            }
            for c in robot.component_manifest
        ],
    }


async def component_list(robot: Robot) -> list[dict[str, Any]]:
    """List of components composed into this robot, in boot order."""
    return [
        {
            "category": c.category,
            "name": c.name,
            "description": c.description,
        }
        for c in robot.component_manifest
    ]


async def shutdown(
    robot: Robot,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Trigger a shutdown.

    ``force=False`` walks the regular lifecycle (``RobotLifecycle``
    ``shutdown`` broadcast, per-component grace, drain+stop in
    reverse). ``force`` is meant for the case where the regular path
    is wedged -- the robot teardown then proceeds without grace.
    """
    logger.info(
        "shutdown requested over control plane (force=%s, %s)",
        force,
        robot.identity.label,
    )
    await robot.request_shutdown(force=force)
    return {"ok": True, "force": force}


# Operation registry: maps wire op-names to handlers. Keep it in one
# place so the WebSocket layer and tests share the same dispatch.

OPERATIONS: dict[str, Any] = {
    "status": status,
    "component.list": component_list,
    "shutdown": shutdown,
}


async def dispatch(
    robot: Robot,
    op: str,
    params: dict[str, Any] | None = None,
) -> Any:
    """Dispatch a wire op-name to the matching handler."""
    handler = OPERATIONS.get(op)
    if handler is None:
        raise UnknownOperation(op)
    params = params or {}
    return await handler(robot, **params)


class UnknownOperation(Exception):
    """Raised when the wire layer asks for an op we don't know."""

    def __init__(self, op: str) -> None:
        super().__init__(f"unknown control-plane operation: {op}")
        self.op = op
