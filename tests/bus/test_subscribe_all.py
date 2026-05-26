"""Tests for ``BusPort.subscribe_all`` on :class:`AsyncioBus`.

The all-subscriber is the bus's wildcard observer: a single
subscription receives every event delivered through ``publish`` and
``publish_broadcast``, regardless of topic. It powers the telemetry
and audit recorders.
"""

from __future__ import annotations

import asyncio

from src.bus import (
    AsyncioBus,
    RobotInput,
    RobotOutput,
)
from src.bus.messages import RobotEvent


def _input(text: str, *, topic: str = "input.demo") -> RobotInput:
    return RobotInput(
        topic=topic,
        principal="user-1",
        source="test",
        run_id="run-1",
        text=text,
    )


def _output(text: str, *, topic: str = "output.demo") -> RobotOutput:
    return RobotOutput(
        topic=topic,
        principal="user-1",
        source="test",
        run_id="run-1",
        text=text,
    )


async def test_subscribe_all_receives_every_publish() -> None:
    """A single all-subscriber sees every routable publish, regardless of topic."""
    bus = AsyncioBus()
    seen: list[tuple[str, str]] = []

    async def handler(event: RobotEvent) -> None:
        text = getattr(event, "text", "")
        seen.append((event.topic, text or ""))

    bus.subscribe_all(handler)

    await bus.start()
    try:
        await bus.publish(_input("hi", topic="input.a"))
        await bus.publish(_output("hello", topic="output.b"))
        await bus.publish(_input("ping", topic="anything.else"))
        await asyncio.sleep(0.02)
    finally:
        await bus.stop()

    assert seen == [
        ("input.a", "hi"),
        ("output.b", "hello"),
        ("anything.else", "ping"),
    ]


async def test_subscribe_all_receives_broadcasts_too() -> None:
    """Broadcasts also reach every all-subscriber."""
    bus = AsyncioBus()
    seen: list[str] = []

    async def handler(event: RobotEvent) -> None:
        seen.append(event.topic)

    bus.subscribe_all(handler)

    await bus.start()
    try:
        await bus.publish(_input("routable", topic="input.x"))
        await bus.publish_broadcast(_input("retained", topic="robot.lifecycle"), retain=True)
        await asyncio.sleep(0.02)
    finally:
        await bus.stop()

    assert seen == ["input.x", "robot.lifecycle"]


async def test_subscribe_all_independent_of_topic_subscribers() -> None:
    """An all-subscriber records traffic even on topics nobody else listens to."""
    bus = AsyncioBus()
    only_all: list[str] = []

    async def handler(event: RobotEvent) -> None:
        only_all.append(event.topic)

    bus.subscribe_all(handler)
    # No regular subscribers anywhere -- the all-subscriber must still see it.

    await bus.start()
    try:
        await bus.publish(_input("nobody-listens", topic="abandoned.topic"))
        await asyncio.sleep(0.02)
    finally:
        await bus.stop()

    assert only_all == ["abandoned.topic"]


async def test_subscribe_all_does_not_double_deliver_to_topic_subscribers() -> None:
    """The all-subscriber is a separate stream, not a fanout that
    re-delivers to topic subscribers."""
    bus = AsyncioBus()
    routable: list[str] = []
    everywhere: list[str] = []

    async def routable_handler(event: RobotEvent) -> None:
        routable.append(event.topic)

    async def all_handler(event: RobotEvent) -> None:
        everywhere.append(event.topic)

    bus.subscribe("input.demo", routable_handler)
    bus.subscribe_all(all_handler)

    await bus.start()
    try:
        await bus.publish(_input("hi", topic="input.demo"))
        await asyncio.sleep(0.02)
    finally:
        await bus.stop()

    # Each subscriber gets exactly one delivery, not two.
    assert routable == ["input.demo"]
    assert everywhere == ["input.demo"]


async def test_subscribe_all_unsubscribe_stops_delivery() -> None:
    bus = AsyncioBus()
    seen: list[str] = []

    async def handler(event: RobotEvent) -> None:
        seen.append(event.topic)

    sub = bus.subscribe_all(handler)

    await bus.start()
    try:
        await bus.publish(_input("first", topic="input.demo"))
        await asyncio.sleep(0.01)
        await sub.unsubscribe()
        await bus.publish(_input("after", topic="input.demo"))
        await asyncio.sleep(0.01)
    finally:
        await bus.stop()

    assert seen == ["input.demo"]


async def test_multiple_all_subscribers_each_get_their_own_copy() -> None:
    bus = AsyncioBus()
    a: list[str] = []
    b: list[str] = []

    async def handler_a(event: RobotEvent) -> None:
        a.append(event.topic)

    async def handler_b(event: RobotEvent) -> None:
        b.append(event.topic)

    bus.subscribe_all(handler_a)
    bus.subscribe_all(handler_b)

    await bus.start()
    try:
        await bus.publish(_input("one", topic="t1"))
        await bus.publish(_input("two", topic="t2"))
        await asyncio.sleep(0.02)
    finally:
        await bus.stop()

    assert a == ["t1", "t2"]
    assert b == ["t1", "t2"]


async def test_subscribe_all_before_start_then_start_consumes_pending() -> None:
    """Registering before start is fine; the consumer task starts on start()."""
    bus = AsyncioBus()
    seen: list[str] = []

    async def handler(event: RobotEvent) -> None:
        seen.append(event.topic)

    bus.subscribe_all(handler)

    await bus.start()
    try:
        await bus.publish(_input("after-start", topic="input.demo"))
        await asyncio.sleep(0.02)
    finally:
        await bus.stop()

    assert seen == ["input.demo"]
