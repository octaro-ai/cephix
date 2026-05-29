"""Tests for the codec layer.

Codecs are pure libraries -- no lifecycle, no DI -- so the tests stay
narrow: encode a record, get back the canonical line.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from src.persistence.codec import JsonlCodec, RecordCodec


def test_jsonl_codec_satisfies_protocol() -> None:
    codec = JsonlCodec()
    assert isinstance(codec, RecordCodec)
    assert codec.extension == ".jsonl"


def test_jsonl_codec_encodes_record_as_compact_line() -> None:
    codec = JsonlCodec()
    line = codec.encode_line({"action": "tool.invoke", "tool": "weather"})
    # No trailing newline -- the storage layer adds it.
    assert "\n" not in line
    assert json.loads(line) == {"action": "tool.invoke", "tool": "weather"}


def test_jsonl_codec_preserves_non_ascii_by_default() -> None:
    line = JsonlCodec().encode_line({"text": "Grüße aus Köln 🌳"})
    assert "Grüße" in line
    assert "Köln" in line
    assert "🌳" in line


def test_jsonl_codec_can_force_ascii_when_required() -> None:
    line = JsonlCodec(ensure_ascii=True).encode_line({"text": "café"})
    assert "café" not in line
    assert json.loads(line) == {"text": "café"}


def test_jsonl_codec_falls_back_to_string_for_rich_types() -> None:
    record = {
        "id": uuid.UUID("12345678-1234-5678-1234-567812345678"),
        "ts": datetime(2026, 5, 30, 10, 0, 0, tzinfo=UTC),
    }
    line = JsonlCodec().encode_line(record)
    parsed = json.loads(line)
    assert parsed["id"] == "12345678-1234-5678-1234-567812345678"
    assert parsed["ts"].startswith("2026-05-30")
