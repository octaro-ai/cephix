"""Tests for the RobotComponent mixin and ComponentCategory enum."""

from __future__ import annotations

from src.bus.asyncio_bus import AsyncioBus
from src.channels.websocket import WebsocketChannel
from src.components import BusComponent, ComponentCategory, RobotComponent
from src.kernel.echo import EchoKernel


def test_component_category_is_string_enum() -> None:
    assert ComponentCategory.BUS.value == "bus"
    assert ComponentCategory.KERNEL.value == "kernel"
    assert ComponentCategory.CHANNEL.value == "channel"


def test_robot_component_has_generic_lifecycle_hooks() -> None:
    assert hasattr(RobotComponent, "start")
    assert hasattr(RobotComponent, "stop")
    assert hasattr(RobotComponent, "drain")


def test_bus_component_is_robot_component_specialization() -> None:
    assert issubclass(BusComponent, RobotComponent)


def test_asyncio_bus_carries_metadata() -> None:
    assert AsyncioBus.component_type == "asyncio"
    assert AsyncioBus.component_category is ComponentCategory.BUS
    assert AsyncioBus.component_description


def test_echo_kernel_carries_metadata() -> None:
    assert EchoKernel.component_type == "echo"
    assert EchoKernel.component_category is ComponentCategory.KERNEL
    assert "echo" in EchoKernel.component_description.lower()


def test_websocket_channel_carries_metadata() -> None:
    assert WebsocketChannel.component_type == "websocket"
    assert WebsocketChannel.component_category is ComponentCategory.CHANNEL
    assert WebsocketChannel.component_description


def test_wizard_fields_default_to_none() -> None:
    """External components without an opt-in keep the safe fallback."""

    class External(RobotComponent):
        component_type = "external-test"
        component_category = ComponentCategory.KERNEL

    assert External.component_wizard_fields is None


def test_builtin_components_declare_wizard_allowlists() -> None:
    """Plumbing parameters (topics, paths, principal templates) stay hidden."""
    assert AsyncioBus.component_wizard_fields == ()
    assert EchoKernel.component_wizard_fields == ("prefix",)
    assert WebsocketChannel.component_wizard_fields == ("host", "port")
