from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.control import RobotControlPlane
from src.domain import ReplyTarget
from src.ports import (
    BusPort,
    ContextAssemblerPort,
    EventSourcePort,
    FirmwarePort,
    HeartbeatPort,
    MemoryPort,
    MessageDeliveryPort,
    PairingRegistryPort,
    PlannerPort,
    TelemetryPort,
)
from src.tools.ports import ToolExecutionPort
from src.runtime import DigitalRobotKernel, RuntimeEventLoop


class DigitalRobot:
    def __init__(
        self,
        *,
        robot_id: str,
        robot_name: str | None = None,
        default_output_target: ReplyTarget | None,
        message_delivery: MessageDeliveryPort,
        tool_executor: ToolExecutionPort,
        context_assembler: ContextAssemblerPort,
        planner: PlannerPort,
        memory: MemoryPort,
        telemetry: TelemetryPort,
        bus: BusPort,
        firmware: FirmwarePort,
        pairings: PairingRegistryPort,
        registered_channels_provider: Callable[[], list[str]],
        memory_backend_name: str,
        tool_execution_backend_name: str,
        event_source: EventSourcePort | None = None,
        heartbeat: HeartbeatPort | None = None,
        channels: list[object] | None = None,
        _kernel_factory: Callable[..., DigitalRobotKernel] = DigitalRobotKernel,
        _control_plane_factory: Callable[..., RobotControlPlane] = RobotControlPlane,
    ) -> None:
        self.robot_id = robot_id
        self.robot_name = robot_name or robot_id
        self.bus = bus
        self.channels = channels or []
        self._onboarded = True
        self._onboarding_status_provider: Callable[[], dict[str, Any]] = lambda: {}
        self._onboarding_handler: Callable[[dict[str, Any]], dict[str, Any]] = lambda payload: {"onboarded": True}

        self.kernel = _kernel_factory(
            robot_id=robot_id,
            default_output_target=default_output_target,
            message_delivery=message_delivery,
            tool_executor=tool_executor,
            context_assembler=context_assembler,
            planner=planner,
            memory=memory,
            telemetry=telemetry,
            bus=bus,
        )
        self.control_plane = _control_plane_factory(
            robot_id=self.kernel.robot_id,
            robot_name=self.robot_name,
            firmware=firmware,
            pairings=pairings,
            registered_channels_provider=registered_channels_provider,
            default_output_target_provider=lambda: self.kernel.default_output_target,
            onboarded_provider=lambda: self._onboarded,
            onboarding_status_provider=lambda: self._onboarding_status_provider(),
            onboarding_handler=lambda payload: self._onboarding_handler(payload),
            memory_backend_name=memory_backend_name,
            tool_execution_backend_name=tool_execution_backend_name,
        )
        self.runtime = RuntimeEventLoop(
            self.kernel,
            event_source,
            heartbeat,
            heartbeat_enabled=lambda: self._onboarded,
        )

    @property
    def onboarded(self) -> bool:
        return self._onboarded

    def set_onboarded(self, onboarded: bool) -> None:
        self._onboarded = onboarded

    def set_onboarding_status_provider(self, provider: Callable[[], dict[str, Any]]) -> None:
        self._onboarding_status_provider = provider

    def set_onboarding_handler(self, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self._onboarding_handler = handler
