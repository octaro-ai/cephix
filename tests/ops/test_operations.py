"""Tests for the sovereign operations.

These are pure async functions over a :class:`Robot` -- they never
touch HTTP or sockets. The control-plane server tests cover the wire
plumbing separately.
"""

from __future__ import annotations

from src.bus import AsyncioBus
from src.kernel import EchoKernel
from src.ops.operations import (
    UnknownOperation,
    component_list,
    dispatch,
    shutdown,
    status,
)
from src.robot import ControlPlaneConfig, Robot, RobotIdentity


def _robot(*, robot_id: str = "x", robot_name: str | None = "X") -> Robot:
    """Build a Robot with the control plane disabled (the test default)."""
    return Robot(
        identity=RobotIdentity(id=robot_id, name=robot_name),
        components=[AsyncioBus(), EchoKernel()],
        control_plane_config=ControlPlaneConfig(enabled=False),
        shutdown_grace=0.0,
    )


async def test_status_offline_phase() -> None:
    robot = _robot()
    snap = await status(robot)
    assert snap["phase"] == "offline"
    assert snap["bus"]["attached"] is False
    assert snap["bus"]["running"] is False
    assert snap["identity"]["id"] == "x"
    assert snap["identity"]["name"] == "X"
    assert snap["uptime_s"] is None


async def test_status_after_serving() -> None:
    robot = _robot()
    await robot.start()
    try:
        snap = await status(robot)
        assert snap["phase"] == "serving"
        assert snap["bus"]["attached"] is True
        assert snap["bus"]["running"] is True
        assert snap["uptime_s"] is not None
        assert snap["uptime_s"] >= 0.0
        categories = [c["category"] for c in snap["components"]]
        assert categories == ["bus", "kernel"]
    finally:
        await robot.stop()


async def test_component_list_returns_manifest() -> None:
    robot = _robot()
    await robot.start()
    try:
        result = await component_list(robot)
        assert [c["category"] for c in result] == ["bus", "kernel"]
        assert all(isinstance(c["type"], str) and c["type"] for c in result)
    finally:
        await robot.stop()


async def test_shutdown_invokes_robot_request_shutdown() -> None:
    received: list[bool] = []

    robot = _robot()

    async def fake_request_shutdown(*, force: bool = False) -> None:
        received.append(force)

    # Replace the bridge so we don't actually tear the robot down here.
    robot.request_shutdown = fake_request_shutdown  # type: ignore[method-assign]
    result = await shutdown(robot, force=False)
    assert result == {"ok": True, "force": False}
    assert received == [False]


async def test_dispatch_routes_to_registered_handler() -> None:
    robot = _robot()
    snap = await dispatch(robot, "status")
    assert snap["phase"] == "offline"


async def test_dispatch_unknown_op_raises() -> None:
    robot = _robot()
    try:
        await dispatch(robot, "no.such.op")
    except UnknownOperation as exc:
        assert exc.op == "no.such.op"
    else:
        raise AssertionError("expected UnknownOperation")
