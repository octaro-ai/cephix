"""Tests for the RobotComponent mixin and ComponentCategory enum."""

from __future__ import annotations

from src.actor.echo import EchoActor
from src.bus.asyncio_bus import AsyncioBus
from src.channels.websocket import WebsocketChannel
from src.components import BusComponent, ComponentCategory, RobotComponent
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
