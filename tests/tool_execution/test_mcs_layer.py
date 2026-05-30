"""Tests for :class:`MCSToolExecutionLayer`.

Two angles:

- **Driver hosting**: the layer aggregates ``MCSToolDriver``
  instances, indexes them by tool name, exposes them through the
  cephix ``ToolExecutionLayerPort``, and translates the MCS
  ``Tool`` shape into ``ToolDescriptor``.
- **Bus dispatch**: a ``ComponentRequest`` on ``tool.invoke``
  triggers ``execute_tool`` on the right driver (off the loop via
  ``asyncio.to_thread``) and the layer replies with a
  ``ComponentResponse`` correlated on ``correlation_id``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import src.bus  # noqa: F401

from mcs.driver.core import (
    DriverBinding,
    DriverMeta,
    MCSToolDriver,
    Tool,
    ToolParameter,
)

from src.bus.asyncio_bus import AsyncioBus
from src.bus.messages import ComponentRequest, ComponentResponse, RobotEvent
from src.components import ComponentCategory
from mcs.driver.mailbox import MailboxToolDriver

from src.tool_execution.mcs_layer import MCSToolExecutionLayer
from src.tool_execution.ports import ToolDescriptor


class _FakeDriver(MCSToolDriver):
    """Tiny in-process driver for isolation tests.

    Records every ``execute_tool`` call so a test can assert
    routing behaviour without poking at real driver internals.
    """

    meta = DriverMeta(
        id="test.fake.v1",
        name="Fake Test Driver",
        version="0.0.1",
        bindings=(DriverBinding(capability="fake", adapter="*", spec_format="Custom"),),
        supported_llms=None,
        capabilities=(),
    )

    def __init__(self, tool_name: str = "fake.ping") -> None:
        self._tool_name = tool_name
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name=self._tool_name,
                title="ping",
                description="Echo the arguments back.",
                parameters=[
                    ToolParameter(
                        name="echo",
                        description="value to return",
                        required=True,
                        schema={"type": "string"},
                    )
                ],
            )
        ]

    def execute_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        self.calls.append((tool_name, dict(arguments)))
        if tool_name != self._tool_name:
            raise ValueError(f"unknown tool: {tool_name}")
        return {"echoed": arguments.get("echo")}


# ---- identity + descriptor translation --------------------------------------


def test_metadata() -> None:
    assert MCSToolExecutionLayer.component_name == "tool-execution"
    assert (
        MCSToolExecutionLayer.component_category
        is ComponentCategory.BUS_PROVIDER
    )


def test_default_drivers_register_mailbox_fetch_unread() -> None:
    layer = MCSToolExecutionLayer()
    names = [t.name for t in layer.list_tools()]
    assert "mailbox.fetch_unread" in names


def test_list_tools_emits_tool_descriptors() -> None:
    layer = MCSToolExecutionLayer(tool_drivers=[_FakeDriver()])
    tools = layer.list_tools()
    assert len(tools) == 1
    descriptor = tools[0]
    assert isinstance(descriptor, ToolDescriptor)
    assert descriptor.name == "fake.ping"
    assert descriptor.title == "ping"
    schema = descriptor.parameters
    assert schema["type"] == "object"
    assert "echo" in schema["properties"]
    assert schema["required"] == ["echo"]


def test_rejects_non_mcs_driver() -> None:
    with pytest.raises(TypeError):
        MCSToolExecutionLayer(tool_drivers=[object()])  # type: ignore[list-item]


# ---- direct off-bus invocation ----------------------------------------------


async def test_invoke_tool_routes_to_owning_driver() -> None:
    driver = _FakeDriver()
    layer = MCSToolExecutionLayer(tool_drivers=[driver])
    result = await layer.invoke_tool("fake.ping", {"echo": "hi"})
    assert result.success
    assert result.result == {"echoed": "hi"}
    assert driver.calls == [("fake.ping", {"echo": "hi"})]


async def test_invoke_tool_unknown_name_raises() -> None:
    layer = MCSToolExecutionLayer(tool_drivers=[_FakeDriver()])
    with pytest.raises(KeyError):
        await layer.invoke_tool("nope", {})


async def test_invoke_tool_wraps_driver_exceptions_as_failure() -> None:
    class _Boom(MCSToolDriver):
        meta = _FakeDriver.meta

        def list_tools(self) -> list[Tool]:
            return [Tool(name="boom", title="boom")]

        def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
            raise RuntimeError("kaboom")

    layer = MCSToolExecutionLayer(tool_drivers=[_Boom()])
    result = await layer.invoke_tool("boom", {})
    assert not result.success
    assert "kaboom" in (result.error or "")


# ---- bus dispatch -----------------------------------------------------------


async def test_bus_request_round_trip() -> None:
    """End-to-end: ComponentRequest on ``tool.invoke`` -> ComponentResponse."""
    bus = AsyncioBus()
    await bus.start()
    received: list[RobotEvent] = []
    bus.subscribe("tool.invoke", lambda event: received.append(event) or asyncio.sleep(0))

    driver = _FakeDriver()
    layer = MCSToolExecutionLayer(tool_drivers=[driver])
    await layer.start(bus)
    try:
        request = ComponentRequest(
            topic="tool.invoke",
            principal="test",
            source="test",
            run_id="r-1",
            correlation_id="corr-1",
            action="fake.ping",
            payload={"echo": "pong"},
        )
        response = await bus.request(request, timeout=2.0)
    finally:
        await layer.stop()
        await bus.stop()

    assert isinstance(response, ComponentResponse)
    assert response.correlation_id == "corr-1"
    assert response.status == "ok"
    assert response.payload == {"echoed": "pong"}
    assert driver.calls == [("fake.ping", {"echo": "pong"})]


async def test_bus_request_unknown_tool_replies_error() -> None:
    bus = AsyncioBus()
    await bus.start()
    layer = MCSToolExecutionLayer(tool_drivers=[_FakeDriver()])
    await layer.start(bus)
    try:
        request = ComponentRequest(
            topic="tool.invoke",
            principal="test",
            source="test",
            run_id="r-2",
            correlation_id="corr-2",
            action="never-registered",
            payload={},
        )
        response = await bus.request(request, timeout=2.0)
    finally:
        await layer.stop()
        await bus.stop()

    assert response.status == "error"
    assert response.error is not None
    assert response.error.code == "tool.unknown"


async def test_bus_dispatch_uses_mailbox_default_driver() -> None:
    """Smoke: a request for ``mailbox.fetch_unread`` lands a dict
    with ``messages`` from the default ``MailboxToolDriver``."""
    bus = AsyncioBus()
    await bus.start()
    layer = MCSToolExecutionLayer()  # default drivers
    await layer.start(bus)
    try:
        request = ComponentRequest(
            topic="tool.invoke",
            principal="test",
            source="test",
            run_id="r-3",
            correlation_id="corr-3",
            action="mailbox.fetch_unread",
            payload={"limit": 2},
        )
        response = await bus.request(request, timeout=2.0)
    finally:
        await layer.stop()
        await bus.stop()

    assert response.status == "ok"
    assert response.payload["mailbox_id"] == "stub-mailbox"
    assert len(response.payload["messages"]) == 2


def test_default_driver_list_includes_mailbox_tooldriver() -> None:
    """The default constructor wires exactly the MailboxToolDriver."""
    layer = MCSToolExecutionLayer()
    assert any(isinstance(d, MailboxToolDriver) for d in layer._drivers)
