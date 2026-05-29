"""Tests for :class:`AuditNoteSink`."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from src.audit.note_sink import AuditNoteSink
from src.bus import (
    AUDIT_TOPIC,
    AsyncioBus,
    RobotAuditNote,
    RobotInput,
)


class _MemoryProvider:
    """Minimal in-memory :class:`EventStreamProviderPort` for tests."""

    def __init__(self) -> None:
        self.records: dict[str, list[dict[str, Any]]] = {}
        self.flushes: dict[str | None, int] = {}

    async def append(self, channel: str, record: Mapping[str, Any]) -> None:
        self.records.setdefault(channel, []).append(dict(record))

    async def flush(self, channel: str | None = None) -> None:
        self.flushes[channel] = self.flushes.get(channel, 0) + 1


def _note(action: str, *, component: str = "kernel", **details: Any) -> RobotAuditNote:
    return RobotAuditNote(
        topic=AUDIT_TOPIC,
        principal="system",
        source=component,
        run_id="run-1",
        component=component,
        action=action,
        details=details,
    )


async def test_sink_persists_audit_notes() -> None:
    bus = AsyncioBus()
    provider = _MemoryProvider()
    component = AuditNoteSink(provider=provider)

    await bus.start()
    try:
        await component.start(bus)
        await bus.publish(_note("tool.invoke", tool="grep"))
        await bus.publish(_note("approval.deny", reason="policy"))
        await asyncio.sleep(0.02)
    finally:
        await component.stop()
        await bus.stop()

    records = provider.records.get("audit", [])
    assert [r["action"] for r in records] == ["tool.invoke", "approval.deny"]
    for record in records:
        assert record["event_type"] == "RobotAuditNote"
        assert record["topic"] == AUDIT_TOPIC


async def test_sink_ignores_non_audit_events_on_audit_topic(
    caplog: Any,
) -> None:
    """If something else lands on AUDIT_TOPIC, it's ignored with a warning."""
    bus = AsyncioBus()
    provider = _MemoryProvider()
    component = AuditNoteSink(provider=provider)

    await bus.start()
    try:
        await component.start(bus)
        with caplog.at_level(logging.WARNING):
            await bus.publish(
                RobotInput(
                    topic=AUDIT_TOPIC,
                    principal="user-1",
                    source="malicious",
                    run_id="run-1",
                    message="not an audit note",
                )
            )
            await asyncio.sleep(0.02)
    finally:
        await component.stop()
        await bus.stop()

    assert provider.records.get("audit", []) == []
    assert any(
        "non-audit event" in rec.message for rec in caplog.records
    )


async def test_sink_only_listens_on_audit_topic() -> None:
    """Notes must travel on AUDIT_TOPIC; events on other topics are not
    in the audit's scope."""
    bus = AsyncioBus()
    provider = _MemoryProvider()
    component = AuditNoteSink(provider=provider)

    await bus.start()
    try:
        await component.start(bus)

        # An event of *type* RobotAuditNote, but published on some other
        # topic, will not reach the AuditNoteSink because it subscribes
        # by topic. This protects the audit log from cross-topic leakage.
        misrouted = RobotAuditNote(
            topic="some.other.topic",
            principal="system",
            source="kernel",
            run_id="run-1",
            component="kernel",
            action="should.not.appear",
            details={},
        )
        await bus.publish(misrouted)
        await asyncio.sleep(0.02)
    finally:
        await component.stop()
        await bus.stop()

    assert provider.records.get("audit", []) == []


async def test_sink_writes_to_custom_channel() -> None:
    bus = AsyncioBus()
    provider = _MemoryProvider()
    component = AuditNoteSink(provider=provider, channel="narrative")

    await bus.start()
    try:
        await component.start(bus)
        await bus.publish(_note("relocated"))
        await asyncio.sleep(0.02)
    finally:
        await component.stop()
        await bus.stop()

    assert "narrative" in provider.records
    assert "audit" not in provider.records


async def test_sink_drain_flushes_configured_channel() -> None:
    bus = AsyncioBus()
    provider = _MemoryProvider()
    component = AuditNoteSink(provider=provider, channel="audit")

    await bus.start()
    try:
        await component.start(bus)
        await component.drain()
    finally:
        await component.stop()
        await bus.stop()

    assert provider.flushes.get("audit", 0) >= 1


async def test_sink_stop_unsubscribes() -> None:
    bus = AsyncioBus()
    provider = _MemoryProvider()
    component = AuditNoteSink(provider=provider)

    await bus.start()
    try:
        await component.start(bus)
        await component.stop()

        await bus.publish(_note("after.stop"))
        await asyncio.sleep(0.02)
    finally:
        await bus.stop()

    assert provider.records.get("audit", []) == []
