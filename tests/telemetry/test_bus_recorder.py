"""Tests for :class:`BusRecorder`."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from src.bus import (
    AUDIT_TOPIC,
    AsyncioBus,
    RobotAuditNote,
    RobotInput,
    RobotOutput,
)
from src.telemetry.bus_recorder import BusRecorder


class _MemoryProvider:
    """Minimal in-memory :class:`EventStreamProviderPort` for tests.

    Stores every appended record keyed by channel, plus per-channel
    flush counters. Satisfies the runtime-checkable protocol by
    duck-typing on ``append`` and ``flush``.
    """

    def __init__(self) -> None:
        self.records: dict[str, list[dict[str, Any]]] = {}
        self.flushes: dict[str | None, int] = {}

    async def append(self, channel: str, record: Mapping[str, Any]) -> None:
        self.records.setdefault(channel, []).append(dict(record))

    async def flush(self, channel: str | None = None) -> None:
        self.flushes[channel] = self.flushes.get(channel, 0) + 1


def _input(text: str, *, topic: str = "input.demo") -> RobotInput:
    return RobotInput(
        topic=topic,
        principal="user-1",
        source="test",
        run_id="run-1",
        message=text,
    )


def _records(provider: _MemoryProvider, channel: str = "telemetry") -> list[dict]:
    """Drop the recorder's own self-announced ComponentLifecycle so
    tests assert on the records they care about."""
    return [
        r
        for r in provider.records.get(channel, [])
        if r["event_type"] != "ComponentLifecycle"
    ]


async def test_recorder_persists_every_routable_publish() -> None:
    bus = AsyncioBus()
    provider = _MemoryProvider()
    recorder = BusRecorder(provider=provider)

    await bus.start()
    try:
        await recorder.start(bus)
        await bus.publish(_input("first", topic="input.a"))
        await bus.publish(
            RobotOutput(
                topic="output.a",
                principal="user-1",
                source="kernel",
                run_id="run-1",
                message="echo: first",
            )
        )
        await asyncio.sleep(0.02)
    finally:
        await recorder.stop()
        await bus.stop()

    data = _records(provider)
    topics = [r["topic"] for r in data]
    assert topics == ["input.a", "output.a"]
    types = [r["event_type"] for r in data]
    assert types == ["RobotInput", "RobotOutput"]


async def test_recorder_persists_broadcasts_too() -> None:
    bus = AsyncioBus()
    provider = _MemoryProvider()
    recorder = BusRecorder(provider=provider)

    await bus.start()
    try:
        await recorder.start(bus)
        await bus.publish_broadcast(
            _input("retained", topic="robot.lifecycle"), retain=True
        )
        await asyncio.sleep(0.02)
    finally:
        await recorder.stop()
        await bus.stop()

    data = _records(provider)
    assert len(data) == 1
    assert data[0]["topic"] == "robot.lifecycle"


async def test_recorder_picks_up_audit_notes() -> None:
    """A telemetry recorder also sees curated audit notes -- audit and
    telemetry are not mutually exclusive."""
    bus = AsyncioBus()
    provider = _MemoryProvider()
    recorder = BusRecorder(provider=provider)

    await bus.start()
    try:
        await recorder.start(bus)
        await bus.publish(
            RobotAuditNote(
                topic=AUDIT_TOPIC,
                principal="system",
                source="kernel",
                run_id="run-1",
                component="kernel",
                action="approval.deny",
                details={"reason": "policy"},
            )
        )
        await asyncio.sleep(0.02)
    finally:
        await recorder.stop()
        await bus.stop()

    data = _records(provider)
    assert len(data) == 1
    record = data[0]
    assert record["topic"] == AUDIT_TOPIC
    assert record["event_type"] == "RobotAuditNote"
    assert record["action"] == "approval.deny"


async def test_recorder_writes_to_custom_channel() -> None:
    bus = AsyncioBus()
    provider = _MemoryProvider()
    recorder = BusRecorder(provider=provider, channel="raw-events")

    await bus.start()
    try:
        await recorder.start(bus)
        await bus.publish(_input("payload"))
        await asyncio.sleep(0.02)
    finally:
        await recorder.stop()
        await bus.stop()

    assert "raw-events" in provider.records
    assert "telemetry" not in provider.records


async def test_recorder_stop_flushes_configured_channel() -> None:
    """``stop()`` template-method drives ``_drain`` (which flushes the
    provider's channel) before ``_stop``."""
    bus = AsyncioBus()
    provider = _MemoryProvider()
    recorder = BusRecorder(provider=provider, channel="telemetry")

    await bus.start()
    try:
        await recorder.start(bus)
    finally:
        await recorder.stop()
        await bus.stop()

    assert provider.flushes.get("telemetry", 0) >= 1


async def test_recorder_scopes_channel_with_robot_run_id_from_retained_lifecycle() -> None:
    """When a retained ``RobotLifecycle.boot`` is present, the
    recorder prefixes its channel with ``<robot_run_id>/`` and all
    subsequent appends land on that scoped channel. The retained
    boot anchor itself is also written under the scoped channel."""
    from src.bus import LIFECYCLE_TOPIC, RobotLifecycle

    bus = AsyncioBus()
    provider = _MemoryProvider()
    recorder = BusRecorder(provider=provider)

    await bus.start()
    try:
        # Pre-populate the retained slot the way the robot would.
        boot = RobotLifecycle(
            topic=LIFECYCLE_TOPIC,
            principal="robot:test",
            source="robot",
            run_id="run-deadbeef",
            phase="boot",
            robot_id="alpha",
            robot_run_id="run-deadbeef",
        )
        await bus.publish_broadcast(boot, retain=True)

        await recorder.start(bus)
        await bus.publish(_input("hello"))
        await asyncio.sleep(0.02)
    finally:
        await recorder.stop()
        await bus.stop()

    # Nothing landed on the bare "telemetry" channel...
    assert "telemetry" not in provider.records
    # ...but on the run-scoped one.
    scoped = "run-deadbeef/telemetry"
    assert scoped in provider.records
    types = [r["event_type"] for r in provider.records[scoped]]
    # Recorder writes: retained boot anchor + recorder's own
    # lifecycle "ready" + the live RobotInput. Order matters --
    # boot first, then ready, then input.
    assert types[0] == "RobotLifecycle"
    assert "RobotInput" in types


async def test_recorder_without_retained_lifecycle_keeps_bare_channel() -> None:
    """No retained boot event (e.g. a bare integration test that
    starts the recorder before any robot publishes) leaves the
    channel name unscoped -- backward-compatible with existing tests."""
    bus = AsyncioBus()
    provider = _MemoryProvider()
    recorder = BusRecorder(provider=provider)

    await bus.start()
    try:
        await recorder.start(bus)
        await bus.publish(_input("hello"))
        await asyncio.sleep(0.02)
    finally:
        await recorder.stop()
        await bus.stop()

    assert "telemetry" in provider.records
    # No run-scoped sibling.
    assert not any(c.startswith("run-") for c in provider.records)


async def test_recorder_stop_unsubscribes() -> None:
    bus = AsyncioBus()
    provider = _MemoryProvider()
    recorder = BusRecorder(provider=provider)

    await bus.start()
    try:
        await recorder.start(bus)
        await recorder.stop()

        await bus.publish(_input("after-stop", topic="input.demo"))
        await asyncio.sleep(0.02)
    finally:
        await bus.stop()

    # The subscription was torn down before the publish, so the
    # input event never reaches the recorder.
    assert all(
        r["event_type"] == "ComponentLifecycle"
        for r in provider.records.get("telemetry", [])
    )
