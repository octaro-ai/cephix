"""Tests for :class:`EchoKernel`."""

from __future__ import annotations

import asyncio

import pytest

from src.bus import AsyncioBus, RobotEvent, RobotInput, RobotOutput
from src.kernel import EchoKernel


async def test_echo_kernel_emits_output_for_input() -> None:
    bus = AsyncioBus()
    outputs: list[RobotOutput] = []

    async def collect(event: RobotEvent) -> None:
        if isinstance(event, RobotOutput):
            outputs.append(event)

    bus.subscribe("output.message", collect)

    await bus.start()
    try:
        kernel = EchoKernel()
        await kernel.start(bus)
        try:
            await bus.publish(
                RobotInput(
                    topic="input.message",
                    principal="user-1",
                    source="channel.test",
                    run_id="run-1",
                    text="hello",
                    payload={"session_id": "abc"},
                )
            )
            await asyncio.sleep(0.02)
        finally:
            await kernel.stop()
    finally:
        await bus.stop()

    assert len(outputs) == 1
    assert outputs[0].text == "echo: hello"
    assert outputs[0].topic == "output.message"
    assert outputs[0].source == "kernel.echo"
    assert outputs[0].run_id == "run-1"
    assert outputs[0].principal == "user-1"
    assert outputs[0].payload == {"session_id": "abc"}


async def test_echo_kernel_ignores_non_input_events() -> None:
    bus = AsyncioBus()
    outputs: list[RobotOutput] = []

    async def collect(event: RobotEvent) -> None:
        if isinstance(event, RobotOutput):
            outputs.append(event)

    bus.subscribe("output.message", collect)

    await bus.start()
    try:
        kernel = EchoKernel()
        await kernel.start(bus)
        try:
            await bus.publish(
                RobotOutput(
                    topic="input.message",
                    principal="user-1",
                    source="other",
                    run_id="run-1",
                    text="ignored",
                )
            )
            await asyncio.sleep(0.02)
        finally:
            await kernel.stop()
    finally:
        await bus.stop()

    assert outputs == []


async def test_echo_kernel_unsubscribes_on_stop() -> None:
    bus = AsyncioBus()
    outputs: list[RobotOutput] = []

    async def collect(event: RobotEvent) -> None:
        if isinstance(event, RobotOutput):
            outputs.append(event)

    bus.subscribe("output.message", collect)

    kernel = EchoKernel()

    await bus.start()
    try:
        await kernel.start(bus)
        await bus.publish(
            RobotInput(
                topic="input.message",
                principal="user-1",
                source="channel.test",
                run_id="run-1",
                text="first",
            )
        )
        await asyncio.sleep(0.02)
        await kernel.stop()
        await bus.publish(
            RobotInput(
                topic="input.message",
                principal="user-1",
                source="channel.test",
                run_id="run-2",
                text="after-stop",
            )
        )
        await asyncio.sleep(0.02)
    finally:
        await bus.stop()

    texts = [out.text for out in outputs]
    assert texts == ["echo: first"]


async def test_echo_kernel_constructed_without_bus() -> None:
    """Configuration-only construction; no bus needed until start."""
    kernel = EchoKernel(input_topic="x.in", output_topic="x.out", prefix="X: ")
    assert isinstance(kernel, EchoKernel)


async def test_echo_kernel_can_be_re_attached_to_a_different_bus() -> None:
    kernel = EchoKernel()

    bus_a = AsyncioBus()
    await bus_a.start()
    try:
        await kernel.start(bus_a)
        await kernel.stop()
    finally:
        await bus_a.stop()

    bus_b = AsyncioBus()
    outputs: list[RobotOutput] = []

    async def collect(event: RobotEvent) -> None:
        if isinstance(event, RobotOutput):
            outputs.append(event)

    bus_b.subscribe("output.message", collect)

    await bus_b.start()
    try:
        await kernel.start(bus_b)
        try:
            await bus_b.publish(
                RobotInput(
                    topic="input.message",
                    principal="user",
                    source="t",
                    run_id="r",
                    text="hi",
                )
            )
            await asyncio.sleep(0.02)
        finally:
            await kernel.stop()
    finally:
        await bus_b.stop()

    assert [out.text for out in outputs] == ["echo: hi"]


async def test_echo_kernel_handler_raises_when_not_started() -> None:
    """A direct handler call without a connected bus must fail loudly."""
    kernel = EchoKernel()
    event = RobotInput(
        topic="input.message",
        principal="user",
        source="t",
        run_id="r",
        text="x",
    )
    with pytest.raises(RuntimeError, match="not started"):
        await kernel._handle_input(event)
