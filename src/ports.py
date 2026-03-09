from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


from src.domain import ControlRequest, ExecutionContext, OutboundMessage, Plan, PlanningContext, ReplyTarget, RobotEvent


# ---------------------------------------------------------------------------
# Channel Ports – individual channel capabilities
# ---------------------------------------------------------------------------


class ChannelIngressPort(Protocol):
    """Drains inbound events from a single channel (e.g. Telegram, WebSocket)."""

    def drain_events(self) -> list[RobotEvent]:
        ...


class ChannelEgressPort(Protocol):
    """Sends a message through a single channel (one physical connector)."""

    def send(self, target: ReplyTarget, message: OutboundMessage) -> None:
        ...


# ---------------------------------------------------------------------------
# Composite / routing ports used by the kernel and runtime
# ---------------------------------------------------------------------------


class EventSourcePort(Protocol):
    def collect_new_events(self) -> list[RobotEvent]:
        ...


class MessageDeliveryPort(Protocol):
    """Routes an outbound message to the correct channel (e.g. ChannelHub)."""

    def send(self, target: ReplyTarget, message: OutboundMessage) -> None:
        ...


# ---------------------------------------------------------------------------
# Control plane ports
# ---------------------------------------------------------------------------


class ControlRequestSourcePort(Protocol):
    def drain_control_requests(self) -> list[ControlRequest]:
        ...


class ControlResponsePort(Protocol):
    def send_control_payload(self, recipient_id: str, payload: dict[str, Any]) -> None:
        ...


class ControlPlanePort(Protocol):
    def get_public_info(self) -> dict[str, Any]:
        ...

    def get_status(self) -> dict[str, Any]:
        ...

    def get_onboarding_status(self) -> dict[str, Any]:
        ...

    def onboard(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def list_pairings(self) -> list[dict[str, Any]]:
        ...

    def approve_pairing(self, device_id: str) -> dict[str, Any]:
        ...


# ---------------------------------------------------------------------------
# Runtime-checkable channel feature ports (used by RobotService via isinstance)
# ---------------------------------------------------------------------------


@runtime_checkable
class ChannelLifecyclePort(Protocol):
    async def start(self) -> None:
        ...

    async def stop(self) -> None:
        ...


@runtime_checkable
class ChannelControlPort(Protocol):
    def drain_control_requests(self) -> list[ControlRequest]:
        ...

    def send_control_payload(self, recipient_id: str, payload: dict[str, Any]) -> None:
        ...


@runtime_checkable
class ChannelInfoPort(Protocol):
    def set_public_info(self, info: dict[str, Any]) -> None:
        ...


# ---------------------------------------------------------------------------
# Firmware, context and heartbeat ports
# ---------------------------------------------------------------------------


class FirmwarePort(Protocol):
    def get_base_guidance(self) -> dict[str, str]:
        ...

    def get_event_instruction(self, event_type: str) -> str:
        ...


class MemoryDocumentPort(Protocol):
    def get_documents(self, event: RobotEvent, user_id: str) -> dict[str, str]:
        ...


class ContextAssemblerPort(Protocol):
    def assemble(self, event: RobotEvent, user_id: str) -> PlanningContext:
        ...


class HeartbeatPort(Protocol):
    def build_idle_event(self) -> RobotEvent | None:
        ...


# ---------------------------------------------------------------------------
# Memory and planner ports
# ---------------------------------------------------------------------------


class MemoryPort(Protocol):
    def build_context(self, user_id: str, conversation_id: str | None) -> dict[str, Any]:
        ...

    def remember_interaction(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        user_text: str,
        robot_text: str,
    ) -> None:
        ...

    def remember_fact(self, user_id: str, kind: str, content: str, score: float = 1.0) -> None:
        ...

    def infer_and_store_preferences(self, user_id: str, user_text: str, robot_text: str) -> None:
        ...


class PlannerPort(Protocol):
    def create_initial_plan(
        self,
        ctx: ExecutionContext,
        event: RobotEvent,
        planning_context: PlanningContext,
    ) -> Plan:
        ...

    def revise_plan_after_tool(
        self,
        ctx: ExecutionContext,
        event: RobotEvent,
        previous_plan: Plan,
        results: dict[str, Any],
        planning_context: PlanningContext,
    ) -> Plan:
        ...


# ---------------------------------------------------------------------------
# Telemetry and bus ports
# ---------------------------------------------------------------------------


class TelemetryPort(Protocol):
    def emit(
        self,
        *,
        ctx: ExecutionContext,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
    ) -> None:
        ...


class BusPort(Protocol):
    def publish(self, msg_type: str, name: str, payload: dict[str, Any]) -> None:
        ...


# ---------------------------------------------------------------------------
# Pairing registry port
# ---------------------------------------------------------------------------


class PairingRegistryPort(Protocol):
    def get_approved_scopes(self, device_id: str) -> frozenset[str]:
        ...

    def queue_pairing(
        self,
        *,
        device_id: str,
        remote_addr: str,
        requested_scopes: set[str],
    ) -> Any:
        ...

    def list_pairings(self) -> list[dict[str, Any]]:
        ...

    def approve_pairing(self, device_id: str) -> dict[str, Any]:
        ...
