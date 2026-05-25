"""Tests for :class:`AsyncioBus`."""

from __future__ import annotations

import asyncio

import pytest

from src.bus import (
    AsyncioBus,
    RobotInput,
    RobotOutput,
    RobotRequest,
    RobotResponse,
)


def _input(text: str, *, topic: str = "input.demo", run_id: str = "run-1") -> RobotInput:
    return RobotInput(
        topic=topic,
        principal="user-1",
        source="test",
        run_id=run_id,
        text=text,
    )


async def test_publish_delivers_to_matching_subscriber() -> None:
    bus = AsyncioBus()
    received: list[RobotInput] = []

    async def handler(event: RobotInput) -> None:  # type: ignore[arg-type]
        received.append(event)

    bus.subscribe("input.demo", handler)  # type: ignore[arg-type]

    await bus.start()
    try:
        await bus.publish(_input("hi"))
        await asyncio.sleep(0.01)
    finally:
        await bus.stop()

    assert len(received) == 1
    assert received[0].text == "hi"


async def test_publish_ignores_other_topics() -> None:
    bus = AsyncioBus()
    received: list[RobotOutput] = []

    async def handler(event: RobotOutput) -> None:  # type: ignore[arg-type]
        received.append(event)

    bus.subscribe("output.demo", handler)  # type: ignore[arg-type]

    await bus.start()
    try:
        await bus.publish(_input("hi", topic="input.demo"))
        await asyncio.sleep(0.01)
    finally:
        await bus.stop()

    assert received == []


async def test_two_subscribers_each_get_their_own_copy() -> None:
    bus = AsyncioBus()
    a: list[str] = []
    b: list[str] = []

    async def handler_a(event: RobotInput) -> None:  # type: ignore[arg-type]
        a.append(event.text or "")

    async def handler_b(event: RobotInput) -> None:  # type: ignore[arg-type]
        b.append(event.text or "")

    bus.subscribe("input.demo", handler_a)  # type: ignore[arg-type]
    bus.subscribe("input.demo", handler_b)  # type: ignore[arg-type]

    await bus.start()
    try:
        await bus.publish(_input("one"))
        await bus.publish(_input("two"))
        await asyncio.sleep(0.02)
    finally:
        await bus.stop()

    assert a == ["one", "two"]
    assert b == ["one", "two"]


async def test_per_subscriber_fifo_isolated_from_slow_consumer() -> None:
    bus = AsyncioBus()
    fast: list[str] = []
    slow_started: list[str] = []

    async def fast_handler(event: RobotInput) -> None:  # type: ignore[arg-type]
        fast.append(event.text or "")

    async def slow_handler(event: RobotInput) -> None:  # type: ignore[arg-type]
        slow_started.append(event.text or "")
        await asyncio.sleep(0.05)

    bus.subscribe("input.demo", fast_handler)  # type: ignore[arg-type]
    bus.subscribe("input.demo", slow_handler)  # type: ignore[arg-type]

    await bus.start()
    try:
        await bus.publish(_input("a"))
        await bus.publish(_input("b"))
        await bus.publish(_input("c"))
        # The fast handler should drain quickly even while slow_handler is busy.
        await asyncio.sleep(0.02)
        assert fast == ["a", "b", "c"]
        # The slow handler has only started the first event so far.
        assert slow_started == ["a"]
    finally:
        await bus.stop()


async def test_request_response_correlation() -> None:
    bus = AsyncioBus()

    async def echo(event: RobotRequest) -> None:  # type: ignore[arg-type]
        await bus.publish(
            RobotResponse(
                topic=f"response.{event.action}",
                principal=event.principal,
                source="echo",
                run_id=event.run_id,
                correlation_id=event.correlation_id,
                ok=True,
                payload={"echo": event.payload},
            )
        )

    bus.subscribe("request.echo", echo)  # type: ignore[arg-type]

    await bus.start()
    try:
        request = RobotRequest(
            topic="request.echo",
            principal="user-1",
            source="test",
            run_id="run-1",
            correlation_id="corr-1",
            action="echo",
            payload={"text": "ping"},
        )
        response = await bus.request(request, timeout=1.0)
    finally:
        await bus.stop()

    assert response.ok
    assert response.correlation_id == "corr-1"
    assert response.payload == {"echo": {"text": "ping"}}


async def test_request_times_out_into_failed_response() -> None:
    bus = AsyncioBus()

    await bus.start()
    try:
        request = RobotRequest(
            topic="request.nobody",
            principal="user-1",
            source="test",
            run_id="run-1",
            correlation_id="corr-x",
            action="nobody.home",
        )
        response = await bus.request(request, timeout=0.05)
    finally:
        await bus.stop()

    assert not response.ok
    assert response.correlation_id == "corr-x"
    assert response.error is not None and "timeout" in response.error


async def test_handler_exception_does_not_break_consumer() -> None:
    bus = AsyncioBus()
    seen: list[str] = []

    async def boom_then_continue(event: RobotInput) -> None:  # type: ignore[arg-type]
        if event.text == "boom":
            raise RuntimeError("intentional")
        seen.append(event.text or "")

    bus.subscribe("input.demo", boom_then_continue)  # type: ignore[arg-type]

    await bus.start()
    try:
        await bus.publish(_input("boom"))
        await bus.publish(_input("after"))
        await asyncio.sleep(0.02)
    finally:
        await bus.stop()

    assert seen == ["after"]


async def test_unsubscribe_stops_delivery() -> None:
    bus = AsyncioBus()
    received: list[str] = []

    async def handler(event: RobotInput) -> None:  # type: ignore[arg-type]
        received.append(event.text or "")

    sub = bus.subscribe("input.demo", handler)  # type: ignore[arg-type]

    await bus.start()
    try:
        await bus.publish(_input("a"))
        await asyncio.sleep(0.01)
        await sub.unsubscribe()
        await bus.publish(_input("b"))
        await asyncio.sleep(0.01)
    finally:
        await bus.stop()

    assert received == ["a"]


async def test_publish_before_start_raises() -> None:
    bus = AsyncioBus()

    with pytest.raises(RuntimeError, match="not running"):
        await bus.publish(_input("hi"))


async def test_start_is_idempotent() -> None:
    bus = AsyncioBus()
    await bus.start()
    await bus.start()
    assert bus.is_running
    await bus.stop()
    assert not bus.is_running


def _audit_event(text: str = "boot") -> RobotInput:
    return RobotInput(
        topic="robot.lifecycle",
        principal="robot:test",
        source="robot.system",
        run_id="boot-aaaa",
        text=text,
    )


async def test_publish_broadcast_delivers_to_all_broadcast_subscribers() -> None:
    bus = AsyncioBus()
    seen_a: list[str] = []
    seen_b: list[str] = []

    async def handler_a(event: RobotInput) -> None:  # type: ignore[arg-type]
        seen_a.append(event.text or "")

    async def handler_b(event: RobotInput) -> None:  # type: ignore[arg-type]
        seen_b.append(event.text or "")

    bus.subscribe_broadcast("robot.lifecycle", handler_a)  # type: ignore[arg-type]
    bus.subscribe_broadcast("robot.lifecycle", handler_b)  # type: ignore[arg-type]

    await bus.start()
    try:
        await bus.publish_broadcast(_audit_event("hello"))
        await asyncio.sleep(0.01)
    finally:
        await bus.stop()

    assert seen_a == ["hello"]
    assert seen_b == ["hello"]


async def test_publish_broadcast_does_not_reach_routable_subscribers() -> None:
    """Routable and broadcast subscriptions live in separate buckets."""
    bus = AsyncioBus()
    routable: list[str] = []
    broadcast: list[str] = []

    async def routable_handler(event: RobotInput) -> None:  # type: ignore[arg-type]
        routable.append(event.text or "")

    async def broadcast_handler(event: RobotInput) -> None:  # type: ignore[arg-type]
        broadcast.append(event.text or "")

    bus.subscribe("robot.lifecycle", routable_handler)  # type: ignore[arg-type]
    bus.subscribe_broadcast("robot.lifecycle", broadcast_handler)  # type: ignore[arg-type]

    await bus.start()
    try:
        await bus.publish_broadcast(_audit_event("only-broadcast"))
        await asyncio.sleep(0.01)
    finally:
        await bus.stop()

    assert routable == []
    assert broadcast == ["only-broadcast"]


async def test_retained_broadcast_delivered_to_late_subscriber() -> None:
    """A new broadcast subscriber receives the latest retained event."""
    bus = AsyncioBus()
    early: list[str] = []
    late: list[str] = []

    async def early_handler(event: RobotInput) -> None:  # type: ignore[arg-type]
        early.append(event.text or "")

    async def late_handler(event: RobotInput) -> None:  # type: ignore[arg-type]
        late.append(event.text or "")

    bus.subscribe_broadcast("robot.lifecycle", early_handler)  # type: ignore[arg-type]

    await bus.start()
    try:
        await bus.publish_broadcast(_audit_event("snapshot"), retain=True)
        await asyncio.sleep(0.01)

        # late subscriber arrives after the publish_broadcast has happened
        bus.subscribe_broadcast("robot.lifecycle", late_handler)  # type: ignore[arg-type]
        await asyncio.sleep(0.01)
    finally:
        await bus.stop()

    assert early == ["snapshot"]
    assert late == ["snapshot"]


async def test_retained_lookup_returns_last_event_synchronously() -> None:
    bus = AsyncioBus()
    await bus.start()
    try:
        assert bus.retained("robot.lifecycle") is None
        first = _audit_event("first")
        await bus.publish_broadcast(first, retain=True)
        assert bus.retained("robot.lifecycle") is first

        second = _audit_event("second")
        await bus.publish_broadcast(second, retain=True)
        assert bus.retained("robot.lifecycle") is second
    finally:
        await bus.stop()


async def test_broadcast_publish_without_retain_is_not_remembered() -> None:
    bus = AsyncioBus()
    received: list[str] = []

    async def handler(event: RobotInput) -> None:  # type: ignore[arg-type]
        received.append(event.text or "")

    await bus.start()
    try:
        await bus.publish_broadcast(_audit_event("ephemeral"))  # no retain
        bus.subscribe_broadcast("robot.lifecycle", handler)  # type: ignore[arg-type]
        await asyncio.sleep(0.01)
    finally:
        await bus.stop()

    # Subscriber arrived after the broadcast and nothing was retained
    assert received == []
