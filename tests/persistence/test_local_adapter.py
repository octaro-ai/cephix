"""Tests for :class:`LocalFSAdapter` (BACKEND, level 0)."""

from __future__ import annotations

from pathlib import Path, PurePath

import pytest

from src.components import ComponentCategory
from src.persistence import LocalFSAdapter
from src.persistence.filesystem.port import FilesystemPort


@pytest.mark.asyncio
async def test_adapter_satisfies_port_protocol() -> None:
    adapter = LocalFSAdapter()
    assert isinstance(adapter, FilesystemPort)


def test_adapter_metadata_marks_it_as_backend_level_0() -> None:
    assert LocalFSAdapter.component_category is ComponentCategory.BACKEND
    assert LocalFSAdapter.component_name == "local-fs"


@pytest.mark.asyncio
async def test_open_append_creates_parents_and_returns_writer(
    tmp_path: Path,
) -> None:
    adapter = LocalFSAdapter()
    target = tmp_path / "nested" / "deep" / "file.txt"
    writer = await adapter.open_append(PurePath(target))
    try:
        await writer.write_line("first")
        await writer.write_line("second")
        await writer.flush()
    finally:
        await writer.close()
    # Lines + trailing newlines
    assert target.read_text(encoding="utf-8") == "first\nsecond\n"
    assert target.parent.is_dir()


@pytest.mark.asyncio
async def test_writer_close_is_idempotent(tmp_path: Path) -> None:
    adapter = LocalFSAdapter()
    writer = await adapter.open_append(PurePath(tmp_path / "f.txt"))
    await writer.write_line("ok")
    await writer.close()
    await writer.close()  # second close must not raise


@pytest.mark.asyncio
async def test_writer_rejects_writes_after_close(tmp_path: Path) -> None:
    adapter = LocalFSAdapter()
    writer = await adapter.open_append(PurePath(tmp_path / "f.txt"))
    await writer.close()
    with pytest.raises(RuntimeError):
        await writer.write_line("late")


@pytest.mark.asyncio
async def test_mkdir_is_idempotent(tmp_path: Path) -> None:
    adapter = LocalFSAdapter()
    target = tmp_path / "a" / "b" / "c"
    await adapter.mkdir(PurePath(target))
    await adapter.mkdir(PurePath(target))  # second call must not raise
    assert target.is_dir()


@pytest.mark.asyncio
async def test_exists_round_trip(tmp_path: Path) -> None:
    adapter = LocalFSAdapter()
    target = tmp_path / "thing"
    assert not await adapter.exists(PurePath(target))
    target.write_text("hi", encoding="utf-8")
    assert await adapter.exists(PurePath(target))


@pytest.mark.asyncio
async def test_is_writable_walks_up_to_existing_ancestor(
    tmp_path: Path,
) -> None:
    adapter = LocalFSAdapter()
    # Probe a non-existent path under an existing writable directory.
    probe = tmp_path / "not" / "yet" / "here"
    assert await adapter.is_writable(PurePath(probe))
