"""Tests for :class:`PersistenceProvider` and :class:`JsonlPersistenceProvider`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.persistence import (
    EventSink,
    JsonlEventSink,
    JsonlPersistenceProvider,
    PersistenceProvider,
)


def test_jsonl_provider_satisfies_persistence_provider_protocol(
    tmp_path: Path,
) -> None:
    provider = JsonlPersistenceProvider(tmp_path)
    assert isinstance(provider, PersistenceProvider)


def test_open_returns_eventsink(tmp_path: Path) -> None:
    provider = JsonlPersistenceProvider(tmp_path)
    sink = provider.open("telemetry")
    assert isinstance(sink, EventSink)
    assert isinstance(sink, JsonlEventSink)


def test_open_maps_channel_to_workspace_relative_path(tmp_path: Path) -> None:
    provider = JsonlPersistenceProvider(tmp_path)
    assert provider.path_for("telemetry") == tmp_path / "telemetry.jsonl"
    assert provider.path_for("audit") == tmp_path / "audit.jsonl"


def test_open_returns_same_sink_for_repeated_channel(tmp_path: Path) -> None:
    """A second open() of the same channel must return the same sink
    instance, so two consumers writing to the same channel don't end
    up with two file handles racing on the same path."""
    provider = JsonlPersistenceProvider(tmp_path)
    a = provider.open("telemetry")
    b = provider.open("telemetry")
    assert a is b


def test_open_returns_distinct_sinks_for_distinct_channels(
    tmp_path: Path,
) -> None:
    provider = JsonlPersistenceProvider(tmp_path)
    tele = provider.open("telemetry")
    audit = provider.open("audit")
    assert tele is not audit


async def test_provider_round_trip_writes_to_expected_paths(
    tmp_path: Path,
) -> None:
    """End-to-end: appending through a provider-issued sink lands on
    the path the provider derived from the channel name."""
    provider = JsonlPersistenceProvider(tmp_path)
    tele = provider.open("telemetry")
    audit = provider.open("audit")
    try:
        await tele.append({"event_type": "RobotInput"})
        await audit.append({"event_type": "RobotAuditNote"})
        await tele.flush()
        await audit.flush()
    finally:
        await tele.close()
        await audit.close()

    tele_path = tmp_path / "telemetry.jsonl"
    audit_path = tmp_path / "audit.jsonl"
    assert tele_path.exists()
    assert audit_path.exists()

    tele_records = [
        json.loads(line)
        for line in tele_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    audit_records = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert tele_records == [{"event_type": "RobotInput"}]
    assert audit_records == [{"event_type": "RobotAuditNote"}]


def test_open_supports_nested_channel_names(tmp_path: Path) -> None:
    """Channel names may use slashes to organize sub-folders."""
    provider = JsonlPersistenceProvider(tmp_path)
    expected = tmp_path / "runs" / "2026-05-25.jsonl"
    assert provider.path_for("runs/2026-05-25") == expected


def test_open_rejects_empty_channel(tmp_path: Path) -> None:
    provider = JsonlPersistenceProvider(tmp_path)
    with pytest.raises(ValueError, match="non-empty"):
        provider.open("")


def test_open_rejects_absolute_channel(tmp_path: Path) -> None:
    """Absolute names would escape the persistence root; reject them."""
    provider = JsonlPersistenceProvider(tmp_path)
    with pytest.raises(ValueError, match="relative"):
        provider.open("/etc/passwd")


def test_custom_suffix_is_respected(tmp_path: Path) -> None:
    provider = JsonlPersistenceProvider(tmp_path, suffix=".ndjson")
    assert provider.path_for("telemetry") == tmp_path / "telemetry.ndjson"


# ---------------------------------------------------------------------------
# BusComponent lifecycle (ready / shutdown + health_check)
# ---------------------------------------------------------------------------


def test_jsonl_provider_is_bus_component(tmp_path: Path) -> None:
    from src.components import BusComponent, ComponentCategory

    provider = JsonlPersistenceProvider(tmp_path)
    assert isinstance(provider, BusComponent)
    assert provider.component_category is ComponentCategory.PERSISTENCE
    assert provider.component_name == "jsonl"


async def test_jsonl_provider_announces_ready_and_shutdown(tmp_path: Path) -> None:
    """Provider self-announces on its lifecycle topic, like every
    other BusComponent."""
    from src.bus.asyncio_bus import AsyncioBus
    from src.bus.messages import ComponentLifecycle, component_lifecycle_topic

    provider = JsonlPersistenceProvider(tmp_path)
    bus = AsyncioBus()
    await bus.start()
    try:
        await provider.start(bus)
        retained = bus.retained(component_lifecycle_topic("jsonl"))
        assert isinstance(retained, ComponentLifecycle)
        assert retained.phase == "ready"

        await provider.stop()
        retained = bus.retained(component_lifecycle_topic("jsonl"))
        assert isinstance(retained, ComponentLifecycle)
        assert retained.phase == "shutdown"
    finally:
        await bus.stop()


async def test_jsonl_provider_stop_closes_issued_sinks(tmp_path: Path) -> None:
    """stop() must close every sink the provider handed out, so a
    consumer that forgets to ``close()`` doesn't leak file handles."""
    from src.bus.asyncio_bus import AsyncioBus

    provider = JsonlPersistenceProvider(tmp_path)
    bus = AsyncioBus()
    await bus.start()
    try:
        await provider.start(bus)
        sink = provider.open("telemetry")
        await sink.append({"event_type": "RobotInput"})
        await provider.stop()
        # After stop(), further appends on the issued sink must fail
        # (sink is closed). This is the JsonlEventSink contract.
        with pytest.raises(Exception):
            await sink.append({"event_type": "x"})
    finally:
        await bus.stop()


async def test_jsonl_provider_health_check_ok_for_writable_root(
    tmp_path: Path,
) -> None:
    provider = JsonlPersistenceProvider(tmp_path)
    h = await provider.health_check()
    assert h.status == "ok"
    assert h.metadata["root"] == str(tmp_path)
