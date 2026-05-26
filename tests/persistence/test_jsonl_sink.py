"""Tests for :class:`JsonlEventSink`."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.persistence.jsonl_sink import JsonlEventSink


def _read_records(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line
    ]


async def test_append_creates_file_lazily(tmp_path: Path) -> None:
    """No file exists until the first record is written."""
    target = tmp_path / "out" / "telemetry.jsonl"
    sink = JsonlEventSink(target)
    try:
        assert not target.exists()
        await sink.append({"hello": "world"})
        assert target.exists()
    finally:
        await sink.close()

    records = _read_records(target)
    assert records == [{"hello": "world"}]


async def test_append_writes_one_record_per_line(tmp_path: Path) -> None:
    target = tmp_path / "telemetry.jsonl"
    sink = JsonlEventSink(target)
    try:
        await sink.append({"event": "boot", "n": 1})
        await sink.append({"event": "ready", "n": 2})
        await sink.flush()
    finally:
        await sink.close()

    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"event": "boot", "n": 1}
    assert json.loads(lines[1]) == {"event": "ready", "n": 2}


async def test_append_concurrent_is_serialized(tmp_path: Path) -> None:
    """Concurrent appends never produce a partial line."""
    target = tmp_path / "telemetry.jsonl"
    sink = JsonlEventSink(target)
    try:
        await asyncio.gather(
            *(sink.append({"i": i}) for i in range(50))
        )
        await sink.flush()
    finally:
        await sink.close()

    records = _read_records(target)
    assert len(records) == 50
    # Values are present and JSONable; ordering is not guaranteed under concurrency.
    assert sorted(int(r["i"]) for r in records) == list(range(50))


async def test_append_after_close_raises(tmp_path: Path) -> None:
    sink = JsonlEventSink(tmp_path / "x.jsonl")
    await sink.close()
    with pytest.raises(RuntimeError, match="closed"):
        await sink.append({"after": "close"})


async def test_close_is_idempotent(tmp_path: Path) -> None:
    sink = JsonlEventSink(tmp_path / "x.jsonl")
    await sink.close()
    await sink.close()  # second close must be a no-op


async def test_unicode_is_preserved_unmangled(tmp_path: Path) -> None:
    target = tmp_path / "tele.jsonl"
    sink = JsonlEventSink(target)
    try:
        await sink.append({"text": "Schöne Grüße"})
        await sink.flush()
    finally:
        await sink.close()

    raw = target.read_text(encoding="utf-8").strip()
    # ensure_ascii defaults to False; the umlauts should land in the file directly.
    assert "Schöne Grüße" in raw


async def test_non_jsonable_objects_are_stringified(tmp_path: Path) -> None:
    """Sinks fall back to str() for objects json doesn't natively know."""
    from datetime import datetime

    target = tmp_path / "tele.jsonl"
    sink = JsonlEventSink(target)
    try:
        await sink.append({"when": datetime(2026, 1, 2, 3, 4, 5)})
        await sink.flush()
    finally:
        await sink.close()

    records = _read_records(target)
    assert len(records) == 1
    assert "2026-01-02 03:04:05" in str(records[0]["when"])
