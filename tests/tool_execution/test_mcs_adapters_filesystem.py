"""Tests for :class:`MCSFilesystemAdapter`.

The adapter is sync and pure (no event loop), so the tests are
pure too: build a tmp-path-rooted ``FilesystemConnection``, hand
it to the adapter, exercise every port method, plus the traversal
guards.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.bus  # noqa: F401

from mcs.driver.filesystem.ports import FilesystemPort

from src.persistence.filesystem.connection import FilesystemConnection
from src.persistence.filesystem.local_adapter import LocalFSAdapter
from src.tool_execution.mcs_adapters import MCSFilesystemAdapter


def _make_adapter(root: Path) -> MCSFilesystemAdapter:
    connection = FilesystemConnection(adapter=LocalFSAdapter(), root=root)
    return MCSFilesystemAdapter(connection=connection)


# ---- identity ----------------------------------------------------------------


def test_satisfies_filesystem_port_structurally(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    assert isinstance(adapter, FilesystemPort)


# ---- list_dir ----------------------------------------------------------------


def test_list_dir_returns_json_with_entries(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    adapter = _make_adapter(tmp_path)
    payload = json.loads(adapter.list_dir("."))
    names = {entry["name"]: entry["type"] for entry in payload["entries"]}
    assert names == {"a.txt": "file", "sub": "directory"}


def test_list_dir_on_missing_path_returns_error(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    payload = json.loads(adapter.list_dir("nowhere"))
    assert "error" in payload


# ---- read_text / write_text --------------------------------------------------


def test_write_then_read_text_roundtrip(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    write_result = json.loads(
        adapter.write_text("notes/hello.txt", "hi there")
    )
    assert write_result["bytes_written"] == len("hi there".encode("utf-8"))
    assert (tmp_path / "notes" / "hello.txt").read_text(encoding="utf-8") == "hi there"

    read_result = json.loads(adapter.read_text("notes/hello.txt"))
    assert read_result["content"] == "hi there"


def test_read_text_on_directory_returns_error(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    adapter = _make_adapter(tmp_path)
    payload = json.loads(adapter.read_text("sub"))
    assert "error" in payload


# ---- list_files + exists -----------------------------------------------------


def test_list_files_returns_relative_paths(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("", encoding="utf-8")
    (tmp_path / "b.log").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.txt").write_text("", encoding="utf-8")
    adapter = _make_adapter(tmp_path)
    assert adapter.list_files(".", "*.txt") == ["a.txt"]


def test_exists_true_and_false(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("", encoding="utf-8")
    adapter = _make_adapter(tmp_path)
    assert adapter.exists("x.txt") is True
    assert adapter.exists("nope.txt") is False


# ---- traversal guards --------------------------------------------------------


def test_parent_traversal_raises(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    with pytest.raises(ValueError):
        adapter.list_dir("../somewhere")


def test_absolute_path_raises(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    with pytest.raises(ValueError):
        adapter.read_text("/etc/passwd")


def test_exists_returns_false_for_blocked_path(tmp_path: Path) -> None:
    """``exists`` swallows the guard error and returns False so the
    LLM-side caller does not have to think about validation."""
    adapter = _make_adapter(tmp_path)
    assert adapter.exists("../outside") is False
