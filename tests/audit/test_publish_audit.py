"""Tests for :meth:`RobotComponent.publish_audit`."""

from __future__ import annotations

import asyncio

from src.audit.note_sink import AuditNoteSink
from src.bus import AUDIT_TOPIC, AsyncioBus, RobotAuditNote
from src.bus.ports import BusPort
from src.components import BusComponent, ComponentCategory


class _Producer(BusComponent):
    """Tiny test component that uses publish_audit on demand."""

    component_name = "test_producer"
    component_category = ComponentCategory.KERNEL

    def __init__(self) -> None:
        self._bus: BusPort | None = None

    async def start(self, bus: BusPort) -> None:
        self._bus = bus

    async def stop(self) -> None:
        self._bus = None

    async def emit(self, action: str, **details: object) -> None:
        assert self._bus is not None
        await self.publish_audit(self._bus, action, details, run_id="run-x")


class _RecordingSink:
    def __init__(self) -> None:
        self.notes: list[RobotAuditNote] = []

    async def append(self, record: object) -> None:  # pragma: no cover -- not used here
        ...

    async def flush(self) -> None:
        ...

    async def close(self) -> None:
        ...


async def test_publish_audit_lands_on_audit_topic() -> None:
    bus = AsyncioBus()
    received: list[RobotAuditNote] = []

    async def handler(event: object) -> None:
        assert isinstance(event, RobotAuditNote)
        received.append(event)

    bus.subscribe(AUDIT_TOPIC, handler)  # type: ignore[arg-type]

    producer = _Producer()
    await bus.start()
    try:
        await producer.start(bus)
        await producer.emit("tool.invoke", tool="grep", argv=["-n", "x"])
        await asyncio.sleep(0.02)
    finally:
        await producer.stop()
        await bus.stop()

    assert len(received) == 1
    note = received[0]
    assert note.topic == AUDIT_TOPIC
    assert note.component == "test_producer"
    assert note.action == "tool.invoke"
    assert note.details == {"tool": "grep", "argv": ["-n", "x"]}
    assert note.source == "test_producer"
    assert note.run_id == "run-x"


async def test_publish_audit_reaches_audit_note_sink_end_to_end() -> None:
    """End-to-end: a producer's note ends up persisted by AuditNoteSink."""
    from collections.abc import Mapping
    from typing import Any

    class _MemorySink:
        def __init__(self) -> None:
            self.records: list[dict[str, Any]] = []

        async def append(self, record: Mapping[str, Any]) -> None:
            self.records.append(dict(record))

        async def flush(self) -> None:
            ...

        async def close(self) -> None:
            ...

    bus = AsyncioBus()
    persistence = _MemorySink()
    note_sink = AuditNoteSink(sink=persistence)
    producer = _Producer()

    await bus.start()
    try:
        await note_sink.start(bus)
        await producer.start(bus)
        await producer.emit("approval.deny", reason="policy")
        await asyncio.sleep(0.02)
    finally:
        await producer.stop()
        await note_sink.stop()
        await bus.stop()

    assert len(persistence.records) == 1
    record = persistence.records[0]
    assert record["component"] == "test_producer"
    assert record["action"] == "approval.deny"
    assert record["details"] == {"reason": "policy"}
