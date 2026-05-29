"""Tests for :func:`wire_commands` over a real :class:`AsyncioBus`."""

from __future__ import annotations

import asyncio

import pytest

from src.bus import (
    AsyncioBus,
    CommandRequest,
    CommandResponse,
    command_request_topic,
    command_response_topic,
)
from src.command import CommandSpec, wire_commands
from src.components import ComponentCategory, RobotComponent


class _CommandComponent(RobotComponent):
    component_name = "demo"
    component_category = ComponentCategory.UTILITY

    provides_commands = (
        CommandSpec(action="demo.session.new", handler="cmd_new"),
        CommandSpec(action="demo.session.fail", handler="cmd_fail"),
        CommandSpec(
            action="demo.mail.send", handler="cmd_send", discriminator="gmail"
        ),
    )

    def __init__(self) -> None:
        self.calls: list[CommandRequest] = []

    async def cmd_new(self, request: CommandRequest) -> dict:
        self.calls.append(request)
        return {"session_id": "sess-1"}

    async def cmd_fail(self, request: CommandRequest) -> dict:
        raise RuntimeError("boom")

    async def cmd_send(self, request: CommandRequest) -> dict:
        return {"account": request.target}


def _request(action: str, *, target=None, correlation_id="cmd-1") -> CommandRequest:
    return CommandRequest(
        topic=command_request_topic(action, target),
        principal="user-1",
        source="channel.test",
        run_id="run-1",
        correlation_id=correlation_id,
        action=action,
        target=target,
    )


async def _collect_response(
    bus: AsyncioBus, action: str, target=None
) -> list[CommandResponse]:
    responses: list[CommandResponse] = []

    async def handler(event) -> None:
        if isinstance(event, CommandResponse):
            responses.append(event)

    bus.subscribe(command_response_topic(action, target), handler)
    return responses


async def test_handler_runs_and_publishes_ok_response() -> None:
    bus = AsyncioBus()
    component = _CommandComponent()
    responses = await _collect_response(bus, "demo.session.new")

    await bus.start()
    try:
        subs = wire_commands(component, bus)
        assert len(subs) == 3
        await bus.publish(_request("demo.session.new"))
        await asyncio.sleep(0.02)
    finally:
        await bus.stop()

    assert len(component.calls) == 1
    assert len(responses) == 1
    resp = responses[0]
    assert resp.status == "ok"
    assert resp.action == "demo.session.new"
    assert resp.correlation_id == "cmd-1"
    assert resp.payload == {"session_id": "sess-1"}
    assert resp.source == "demo"
    assert resp.source_id == component.instance_id


async def test_handler_exception_becomes_error_response() -> None:
    bus = AsyncioBus()
    component = _CommandComponent()
    responses = await _collect_response(bus, "demo.session.fail")

    await bus.start()
    try:
        wire_commands(component, bus)
        await bus.publish(_request("demo.session.fail"))
        await asyncio.sleep(0.02)
    finally:
        await bus.stop()

    assert len(responses) == 1
    resp = responses[0]
    assert resp.status == "error"
    assert resp.error is not None
    assert resp.error.code == "command.handler_failed"
    assert "boom" in resp.error.message


async def test_discriminator_routes_to_suffixed_topic() -> None:
    bus = AsyncioBus()
    component = _CommandComponent()
    responses = await _collect_response(bus, "demo.mail.send", target="gmail")

    await bus.start()
    try:
        wire_commands(component, bus)
        await bus.publish(_request("demo.mail.send", target="gmail"))
        await asyncio.sleep(0.02)
    finally:
        await bus.stop()

    assert len(responses) == 1
    assert responses[0].payload == {"account": "gmail"}
    assert responses[0].target == "gmail"


async def test_consumer_ignores_non_command_events() -> None:
    bus = AsyncioBus()
    component = _CommandComponent()

    await bus.start()
    try:
        wire_commands(component, bus)
        # A RobotInput on the request topic must not trigger the handler.
        from src.bus import RobotInput

        await bus.publish(
            RobotInput(
                topic=command_request_topic("demo.session.new"),
                principal="p",
                source="s",
                run_id="r",
                message="not a command",
            )
        )
        await asyncio.sleep(0.02)
    finally:
        await bus.stop()

    assert component.calls == []


def test_wire_commands_rejects_missing_handler() -> None:
    class _Bad(RobotComponent):
        component_name = "bad"
        component_category = ComponentCategory.UTILITY
        provides_commands = (
            CommandSpec(action="bad.x", handler="does_not_exist"),
        )

    bus = AsyncioBus()
    with pytest.raises(AttributeError):
        wire_commands(_Bad(), bus)
