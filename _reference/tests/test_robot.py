from __future__ import annotations

import unittest

from src.control import InMemoryPairingRegistry
from src.domain import ReplyTarget
from src.robot import DigitalRobot


class _StubKernel:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.robot_id = str(kwargs["robot_id"])
        self.default_output_target = kwargs["default_output_target"]

    def handle_event(self, event: object) -> None:
        return None


class _StubControlPlane:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def get_public_info(self) -> dict[str, object]:
        return {"robot_id": self.kwargs["robot_id"]}

    def get_status(self) -> dict[str, object]:
        return {"registered_channels": self.kwargs["registered_channels_provider"]()}

    def list_pairings(self) -> list[dict[str, object]]:
        return []

    def approve_pairing(self, device_id: str) -> dict[str, object]:
        return {"device_id": device_id, "approved": True, "granted_scopes": ["chat"]}


class _StubFirmware:
    def get_base_guidance(self) -> dict[str, str]:
        return {"AGENTS.md": "test"}


class DigitalRobotTests(unittest.TestCase):
    def test_constructor_builds_kernel_and_control_plane_via_hooks(self) -> None:
        default_output = ReplyTarget(channel="ws", recipient_id="client-1")

        robot = DigitalRobot(
            robot_id="robot-1",
            default_output_target=default_output,
            message_delivery=object(),
            tool_executor=object(),
            context_assembler=object(),
            planner=object(),
            memory=object(),
            telemetry=object(),
            bus=object(),
            firmware=_StubFirmware(),
            pairings=InMemoryPairingRegistry(),
            registered_channels_provider=lambda: ["ws"],
            memory_backend_name="memory",
            tool_execution_backend_name="tools",
            channels=[object()],
            _kernel_factory=_StubKernel,
            _control_plane_factory=_StubControlPlane,
        )

        self.assertIsInstance(robot.kernel, _StubKernel)
        self.assertIsInstance(robot.control_plane, _StubControlPlane)
        self.assertEqual("robot-1", robot.kernel.robot_id)
        self.assertIs(robot.runtime.kernel, robot.kernel)
        self.assertEqual(["ws"], robot.control_plane.get_status()["registered_channels"])


if __name__ == "__main__":
    unittest.main()
