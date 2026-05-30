"""Tests for :class:`FilesystemEventStreamProvider` (PROVIDER, level 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.components import ComponentCategory
from src.persistence import (
    EventStreamProviderPort,
    FilesystemConnection,
    FilesystemEventStreamProvider,
    LocalFSAdapter,
)


def _build_provider(root: Path) -> FilesystemEventStreamProvider:
    # ``directory=""`` keeps channels directly under ``root`` so the
    # tests can assert on flat ``<root>/<channel>.jsonl`` paths. The
    # production default (``directory="logs"``) is exercised through
    # the builder tests.
    connection = FilesystemConnection(adapter=LocalFSAdapter(), root=root)
    return FilesystemEventStreamProvider(connection=connection, directory="")


def test_provider_metadata_marks_it_as_provider_level_2() -> None:
    assert (
        FilesystemEventStreamProvider.component_category
        is ComponentCategory.PROVIDER
    )
    assert FilesystemEventStreamProvider.component_name == "filesystem-events"


def test_provider_satisfies_port_protocol(tmp_path: Path) -> None:
    provider = _build_provider(tmp_path)
    assert isinstance(provider, EventStreamProviderPort)


def test_constructor_rejects_non_connection(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        FilesystemEventStreamProvider(connection=object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_append_writes_record_to_channel_file(tmp_path: Path) -> None:
    provider = _build_provider(tmp_path)
    await provider.start()
    try:
        await provider.append("telemetry", {"a": 1})
        await provider.append("telemetry", {"a": 2})
        await provider.flush("telemetry")
    finally:
        await provider.stop()
    lines = (tmp_path / "telemetry.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [{"a": 1}, {"a": 2}]


@pytest.mark.asyncio
async def test_append_isolates_channels(tmp_path: Path) -> None:
    provider = _build_provider(tmp_path)
    await provider.start()
    try:
        await provider.append("telemetry", {"src": "t"})
        await provider.append("audit", {"src": "a"})
        await provider.flush()
    finally:
        await provider.stop()
    assert (
        (tmp_path / "telemetry.jsonl").read_text(encoding="utf-8")
        == '{"src":"t"}\n'
    )
    assert (
        (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
        == '{"src":"a"}\n'
    )


@pytest.mark.asyncio
async def test_flush_all_channels(tmp_path: Path) -> None:
    provider = _build_provider(tmp_path)
    await provider.start()
    await provider.append("telemetry", {"x": 1})
    await provider.append("audit", {"y": 2})
    # ``flush()`` without an argument flushes every open channel.
    await provider.flush()
    assert (tmp_path / "telemetry.jsonl").exists()
    assert (tmp_path / "audit.jsonl").exists()
    await provider.stop()


@pytest.mark.asyncio
async def test_stop_closes_open_writers(tmp_path: Path) -> None:
    provider = _build_provider(tmp_path)
    await provider.start()
    await provider.append("telemetry", {"x": 1})
    await provider.stop()
    # After stop, the internal writer cache is cleared. A fresh
    # ``append`` opens a new writer (re-creating the cache); the file
    # is appended to, not truncated.
    await provider.append("telemetry", {"x": 2})
    await provider.stop()
    parsed = [
        json.loads(line)
        for line in (tmp_path / "telemetry.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert parsed == [{"x": 1}, {"x": 2}]


@pytest.mark.asyncio
async def test_writes_jsonl_extension(tmp_path: Path) -> None:
    """Channels resolve to ``<channel>.jsonl`` -- the wire format is fixed."""
    provider = _build_provider(tmp_path)
    await provider.start()
    try:
        await provider.append("telemetry", {"a": 1})
        await provider.flush()
    finally:
        await provider.stop()
    assert (tmp_path / "telemetry.jsonl").exists()


# ---- subdir channels (cross-cutting, used by sink-side run scoping) --------


@pytest.mark.asyncio
async def test_channel_with_slash_creates_subdir(tmp_path: Path) -> None:
    """A channel string containing ``/`` resolves to a subdir under
    the provider's directory; the slash becomes a real path
    separator via :meth:`FilesystemConnection.path_for`. This is
    what sinks rely on for per-run scoping (e.g. ``"run-abc/audit"``).
    """
    provider = _build_provider(tmp_path)
    await provider.start()
    try:
        await provider.append("run-abc/telemetry", {"a": 1})
        await provider.append("run-abc/audit", {"b": 2})
        await provider.flush()
    finally:
        await provider.stop()
    assert (tmp_path / "run-abc" / "telemetry.jsonl").exists()
    assert (tmp_path / "run-abc" / "audit.jsonl").exists()
