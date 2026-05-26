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
