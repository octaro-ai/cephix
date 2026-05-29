"""Tests for :class:`FilesystemConnection` (CONNECTION, level 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.components import ComponentCategory
from src.persistence import FilesystemConnection, LocalFSAdapter


def test_connection_metadata_marks_it_as_connection_level_1() -> None:
    assert FilesystemConnection.component_category is ComponentCategory.CONNECTION
    assert FilesystemConnection.component_name == "filesystem"


def test_constructor_rejects_non_port_adapter(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        FilesystemConnection(adapter=object(), root=tmp_path)  # type: ignore[arg-type]


def test_path_for_resolves_channel_against_root(tmp_path: Path) -> None:
    conn = FilesystemConnection(adapter=LocalFSAdapter(), root=tmp_path)
    p = conn.path_for("telemetry", suffix=".jsonl")
    assert Path(p) == tmp_path / "telemetry.jsonl"


def test_path_for_supports_subdirectories(tmp_path: Path) -> None:
    conn = FilesystemConnection(adapter=LocalFSAdapter(), root=tmp_path)
    p = conn.path_for("sessions/sess-abc", suffix=".jsonl")
    assert Path(p) == tmp_path / "sessions" / "sess-abc.jsonl"


def test_path_for_rejects_empty_channel(tmp_path: Path) -> None:
    conn = FilesystemConnection(adapter=LocalFSAdapter(), root=tmp_path)
    with pytest.raises(ValueError):
        conn.path_for("")


def test_path_for_rejects_absolute_channel(tmp_path: Path) -> None:
    conn = FilesystemConnection(adapter=LocalFSAdapter(), root=tmp_path)
    with pytest.raises(ValueError):
        conn.path_for("/etc/passwd")


def test_path_for_rejects_parent_traversal(tmp_path: Path) -> None:
    conn = FilesystemConnection(adapter=LocalFSAdapter(), root=tmp_path)
    with pytest.raises(ValueError):
        conn.path_for("../outside")


@pytest.mark.asyncio
async def test_start_creates_root(tmp_path: Path) -> None:
    root = tmp_path / "not-yet"
    conn = FilesystemConnection(adapter=LocalFSAdapter(), root=root)
    assert not root.exists()
    await conn.start()
    assert root.is_dir()


@pytest.mark.asyncio
async def test_open_append_writes_through_adapter(tmp_path: Path) -> None:
    conn = FilesystemConnection(adapter=LocalFSAdapter(), root=tmp_path)
    await conn.start()
    writer = await conn.open_append("telemetry", suffix=".jsonl")
    try:
        await writer.write_line('{"a":1}')
    finally:
        await writer.close()
    assert (tmp_path / "telemetry.jsonl").read_text(encoding="utf-8") == '{"a":1}\n'


@pytest.mark.asyncio
async def test_health_check_ok_when_root_is_writable(tmp_path: Path) -> None:
    conn = FilesystemConnection(adapter=LocalFSAdapter(), root=tmp_path)
    await conn.start()
    health = await conn.health_check()
    assert health.status == "ok"
    assert health.metadata["root"] == str(tmp_path)
