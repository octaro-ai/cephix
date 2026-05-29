"""Tests for the RobotComponent mixin and ComponentCategory enum."""

from __future__ import annotations

import asyncio

import pytest

from src.actor.echo import EchoActor
from src.bus.asyncio_bus import AsyncioBus
from src.bus.messages import ErrorInfo
from src.channels.websocket import WebsocketChannel
from src.components import (
    INSTANCE_ID_LENGTH,
    BusComponent,
    ComponentCategory,
    ComponentHealth,
    RobotComponent,
)
from src.kernel.base import BaseKernel


def test_component_category_is_string_enum() -> None:
    assert ComponentCategory.BUS.value == "bus"
    assert ComponentCategory.KERNEL.value == "kernel"
    assert ComponentCategory.ACTOR.value == "actor"
    assert ComponentCategory.CHANNEL.value == "channel"


def test_robot_component_has_generic_lifecycle_hooks() -> None:
    assert hasattr(RobotComponent, "start")
    assert hasattr(RobotComponent, "stop")
    assert hasattr(RobotComponent, "drain")


def test_bus_component_is_robot_component_specialization() -> None:
    assert issubclass(BusComponent, RobotComponent)


def test_asyncio_bus_carries_metadata() -> None:
    assert AsyncioBus.component_name == "asyncio"
    assert AsyncioBus.component_category is ComponentCategory.BUS
    assert AsyncioBus.component_description


def test_base_kernel_carries_metadata() -> None:
    assert BaseKernel.component_name == "base"
    assert BaseKernel.component_category is ComponentCategory.KERNEL
    assert BaseKernel.component_description


def test_echo_actor_carries_metadata() -> None:
    assert EchoActor.component_name == "echo"
    assert EchoActor.component_category is ComponentCategory.ACTOR
    assert "echo" in EchoActor.component_description.lower()


def test_websocket_channel_carries_metadata() -> None:
    assert WebsocketChannel.component_name == "websocket"
    assert WebsocketChannel.component_category is ComponentCategory.CHANNEL
    assert WebsocketChannel.component_description


def test_robot_component_has_no_wizard_attribute() -> None:
    """The component contract is runtime-only; UI hints live in onboarding."""
    assert not hasattr(RobotComponent, "component_wizard_fields")


def test_builtin_components_declare_wizard_allowlists() -> None:
    """Plumbing parameters (topics, paths, principal templates) stay hidden."""
    from src.onboarding import WIZARD_ALLOWLIST

    assert WIZARD_ALLOWLIST[AsyncioBus] == ()
    assert WIZARD_ALLOWLIST[BaseKernel] == (
        "input_topic",
        "output_topic",
        "actor_timeout",
    )
    assert WIZARD_ALLOWLIST[EchoActor] == ("prefix",)
    assert WIZARD_ALLOWLIST[WebsocketChannel] == ("host", "port")


def test_external_components_without_registration_default_to_ask_all() -> None:
    """A class absent from WIZARD_ALLOWLIST means 'ask for every parameter'."""
    from src.onboarding import WIZARD_ALLOWLIST

    class External(RobotComponent):
        component_name = "external-test"
        component_category = ComponentCategory.KERNEL

    assert External not in WIZARD_ALLOWLIST


# ---------------------------------------------------------------------------
# Per-instance identity
# ---------------------------------------------------------------------------


class _Plain(RobotComponent):
    component_name = "plain-id-test"
    component_category = ComponentCategory.KERNEL


def test_instance_id_is_short_hex_of_documented_length() -> None:
    """The id must fit on a single log line and be hex-only."""
    component = _Plain()
    instance_id = component.instance_id

    assert len(instance_id) == INSTANCE_ID_LENGTH
    assert INSTANCE_ID_LENGTH == 12  # contract: documented value
    assert all(ch in "0123456789abcdef" for ch in instance_id)


def test_instance_id_is_stable_for_the_lifetime_of_an_instance() -> None:
    """A repeat read returns the same id; the field is write-once."""
    component = _Plain()
    first = component.instance_id
    second = component.instance_id

    assert first == second


def test_two_instances_of_the_same_class_get_distinct_ids() -> None:
    """Otherwise the operator could not tell two BaseKernels apart."""
    one = _Plain()
    two = _Plain()

    assert one.instance_id != two.instance_id


def test_publish_audit_carries_instance_id_as_source_id() -> None:
    """Audit notes record which *instance* of the component spoke."""
    import asyncio

    from src.bus.messages import AUDIT_TOPIC, RobotAuditNote, RobotEvent

    async def run() -> None:
        bus = AsyncioBus()
        await bus.start()
        captured: list[RobotAuditNote] = []

        async def handler(event: RobotEvent) -> None:
            if isinstance(event, RobotAuditNote):
                captured.append(event)

        sub = bus.subscribe(AUDIT_TOPIC, handler)
        try:
            component = _Plain()
            await component.publish_audit(bus, action="probe")
            await asyncio.sleep(0)
        finally:
            await sub.unsubscribe()
            await bus.stop()
        assert len(captured) == 1
        note = captured[0]
        assert note.topic == AUDIT_TOPIC
        assert note.source == "plain-id-test"
        assert note.source_id == component.instance_id

    asyncio.run(run())


# ---------------------------------------------------------------------------
# ComponentHealth invariant
# ---------------------------------------------------------------------------


def test_component_health_defaults_to_ok() -> None:
    h = ComponentHealth()
    assert h.status == "ok"
    assert h.error is None
    assert h.metadata == {}


def test_component_health_ok_must_not_carry_error() -> None:
    with pytest.raises(ValueError, match="status='ok' must not"):
        ComponentHealth(error=ErrorInfo(code="boom"))


def test_component_health_warn_requires_error() -> None:
    with pytest.raises(ValueError, match="status='warn' requires"):
        ComponentHealth(status="warn")


def test_component_health_error_requires_error() -> None:
    with pytest.raises(ValueError, match="status='error' requires"):
        ComponentHealth(status="error")


def test_component_health_warn_with_metadata() -> None:
    h = ComponentHealth(
        status="warn",
        error=ErrorInfo(code="rate_limit_warning", message="3 retries used"),
        metadata={"model": "gpt-5", "retries": 3},
    )
    assert h.status == "warn"
    assert h.metadata["retries"] == 3
    assert h.error is not None
    assert h.error.code == "rate_limit_warning"


# ---------------------------------------------------------------------------
# health_check default hook
# ---------------------------------------------------------------------------


async def test_robot_component_default_health_check_is_ok() -> None:
    """A component that does not override health_check is assumed healthy."""

    class _PlainHealth(RobotComponent):
        component_name = "plain-test"
        component_category = ComponentCategory.KERNEL

    h = await _PlainHealth().health_check()
    assert h.status == "ok"
    assert h.error is None
    assert h.metadata == {}


async def test_component_health_check_can_be_overridden() -> None:
    """A component reporting warn must surface a structured ErrorInfo."""

    class _Degraded(RobotComponent):
        component_name = "degraded-test"
        component_category = ComponentCategory.ACTOR

        async def health_check(self) -> ComponentHealth:
            return ComponentHealth(
                status="warn",
                error=ErrorInfo(code="cache_only", message="upstream unavailable"),
                metadata={"model": "gpt-5"},
            )

    h = await _Degraded().health_check()
    assert h.status == "warn"
    assert h.error is not None
    assert h.error.code == "cache_only"
    assert h.metadata == {"model": "gpt-5"}


# ---------------------------------------------------------------------------
# component_info() + self-announced lifecycle
# ---------------------------------------------------------------------------


def test_component_info_without_commands_has_empty_metadata() -> None:
    class _Plain(RobotComponent):
        component_name = "plain-info"
        component_category = ComponentCategory.ACTOR

    info = _Plain().component_info()
    assert info.name == "plain-info"
    assert info.category == "actor"
    assert info.metadata == {}


def test_component_info_serializes_provides_commands() -> None:
    from src.command import CommandSpec

    class _WithCmd(RobotComponent):
        component_name = "cmd-info"
        component_category = ComponentCategory.KERNEL
        provides_commands = (
            CommandSpec(action="x.y.z", handler="h", label="Z"),
        )

    c = _WithCmd()
    entries = c.component_info().metadata["provides_commands"]
    assert entries[0]["action"] == "x.y.z"
    assert entries[0]["owner_component"] == "cmd-info"
    assert entries[0]["owner_instance_id"] == c.instance_id


class _AnnouncingComponent(BusComponent):
    component_name = "announcer"
    component_category = ComponentCategory.BUS_UTILITY

    async def start(self, bus: AsyncioBus) -> None:  # type: ignore[override]
        await self.announce_lifecycle(bus, "ready")

    async def stop(self) -> None:
        pass


async def test_bus_component_self_announces_ready_retained() -> None:
    from src.bus.messages import ComponentLifecycle, component_lifecycle_topic

    bus = AsyncioBus()
    seen: list[ComponentLifecycle] = []

    async def handler(event) -> None:
        if isinstance(event, ComponentLifecycle):
            seen.append(event)

    await bus.start()
    try:
        bus.subscribe_all(handler)
        comp = _AnnouncingComponent()
        await comp.start(bus)
        await comp.announce_lifecycle(bus, "shutdown")
        await asyncio.sleep(0.02)

        # Retained slot carries the latest phase for late subscribers.
        retained = bus.retained(component_lifecycle_topic("announcer"))
        assert isinstance(retained, ComponentLifecycle)
        assert retained.phase == "shutdown"
    finally:
        await bus.stop()

    phases = [e.phase for e in seen]
    assert "ready" in phases
    assert "shutdown" in phases
