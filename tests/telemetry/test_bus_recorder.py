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


class _MemorySink:
    """Minimal in-memory :class:`EventSink` for tests."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.flushed = 0
        self.closed = False

    async def append(self, record: Mapping[str, Any]) -> None:
        self.records.append(dict(record))

    async def flush(self) -> None:
        self.flushed += 1

    async def close(self) -> None:
        self.closed = True


def _input(text: str, *, topic: str = "input.demo") -> RobotInput:
    return RobotInput(
        topic=topic,
        principal="user-1",
        source="test",
        run_id="run-1",
        text=text,
    )


async def test_recorder_persists_every_routable_publish() -> None:
    bus = AsyncioBus()
    sink = _MemorySink()
    recorder = BusRecorder(sink=sink)

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
                text="echo: first",
            )
        )
        await asyncio.sleep(0.02)
    finally:
        await recorder.stop()
        await bus.stop()

    topics = [r["topic"] for r in sink.records]
    assert topics == ["input.a", "output.a"]
    types = [r["event_type"] for r in sink.records]
    assert types == ["RobotInput", "RobotOutput"]


async def test_recorder_persists_broadcasts_too() -> None:
    bus = AsyncioBus()
    sink = _MemorySink()
    recorder = BusRecorder(sink=sink)

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

    assert len(sink.records) == 1
    assert sink.records[0]["topic"] == "robot.lifecycle"


async def test_recorder_picks_up_audit_notes() -> None:
    """A telemetry recorder also sees curated audit notes -- audit and
    telemetry are not mutually exclusive."""
    bus = AsyncioBus()
    sink = _MemorySink()
    recorder = BusRecorder(sink=sink)

    await bus.start()
    try:
        await recorder.start(bus)
        await bus.publish(
            RobotAuditNote(
                topic=AUDIT_TOPIC,
                principal="system",
                source="kernel",
                run_id="run-1",
                actor="kernel",
                action="approval.deny",
                details={"reason": "policy"},
            )
        )
        await asyncio.sleep(0.02)
    finally:
        await recorder.stop()
        await bus.stop()

    assert len(sink.records) == 1
    record = sink.records[0]
    assert record["topic"] == AUDIT_TOPIC
    assert record["event_type"] == "RobotAuditNote"
    assert record["action"] == "approval.deny"


async def test_recorder_drain_flushes_sink() -> None:
    bus = AsyncioBus()
    sink = _MemorySink()
    recorder = BusRecorder(sink=sink)

    await bus.start()
    try:
        await recorder.start(bus)
        await recorder.drain()
    finally:
        await recorder.stop()
        await bus.stop()

    assert sink.flushed >= 1


async def test_recorder_stop_closes_sink_and_unsubscribes() -> None:
    bus = AsyncioBus()
    sink = _MemorySink()
    recorder = BusRecorder(sink=sink)

    await bus.start()
    try:
        await recorder.start(bus)
        await recorder.stop()

        await bus.publish(_input("after-stop", topic="input.demo"))
        await asyncio.sleep(0.02)
    finally:
        await bus.stop()

    assert sink.closed is True
    assert sink.records == []  # subscription was torn down before the publish
