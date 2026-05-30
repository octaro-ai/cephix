"""Tests for :class:`FilesystemFirmwareStore`.

Three groups:

- identity + lifecycle (UTILITY, plain RobotComponent, ``start``
  reads documents, ``stop`` drops the cache);
- inventory + ordering (every matching file is read, sorted by
  filename; empty documents are skipped at prompt-assembly time);
- ``system_prompt`` rendering shape (``## <NAME>`` headers,
  blank-line separators).
"""

from __future__ import annotations

from pathlib import Path

# Import the bus package first so the ``src.components`` <->
# ``src.bus.ports`` cycle resolves cleanly when this test is the first
# module pytest loads in a run. The rest of the suite already primes
# this order; the explicit import keeps the file runnable in isolation.
import src.bus  # noqa: F401

from src.components import BusComponent, ComponentCategory, RobotComponent
from src.persistence.filesystem.connection import FilesystemConnection
from src.persistence.filesystem.local_adapter import LocalFSAdapter
from src.utility.firmware_store import FilesystemFirmwareStore, FirmwareStorePort


def _make_store(
    root: Path,
    *,
    directory: str = "firmware",
) -> FilesystemFirmwareStore:
    """Build a :class:`FilesystemFirmwareStore` rooted at ``root``.

    Adapter and connection are constructed inline because the
    persistence-layer integration is exercised end-to-end through
    the builder elsewhere; here we just need the store under
    realistic wiring.
    """
    connection = FilesystemConnection(adapter=LocalFSAdapter(), root=root)
    return FilesystemFirmwareStore(connection=connection, directory=directory)


def _firmware_dir(root: Path, directory: str = "firmware") -> Path:
    """Resolve the on-disk path the store reads from."""
    target = root / directory
    target.mkdir(parents=True, exist_ok=True)
    return target


def test_metadata() -> None:
    assert FilesystemFirmwareStore.component_name == "firmware-store"
    assert (
        FilesystemFirmwareStore.component_category is ComponentCategory.UTILITY
    )


def test_is_plain_robot_component(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert isinstance(store, RobotComponent)
    assert not isinstance(store, BusComponent)
    assert isinstance(store, FirmwareStorePort)


async def test_lifecycle_reads_then_drops(tmp_path: Path) -> None:
    firmware = _firmware_dir(tmp_path)
    (firmware / "A.md").write_text("alpha", encoding="utf-8")
    store = _make_store(tmp_path)
    await store.start()
    assert store.documents() == {"A": "alpha"}
    await store.stop()
    assert store.documents() == {}


async def test_missing_directory_is_tolerated(tmp_path: Path) -> None:
    """A missing firmware dir leaves the store empty; no exception."""
    store = _make_store(tmp_path / "ghost")
    await store.start()
    assert store.documents() == {}
    assert store.system_prompt() == ""


async def test_inventory_sorted_by_filename(tmp_path: Path) -> None:
    firmware = _firmware_dir(tmp_path)
    (firmware / "POLICY.md").write_text("policy body", encoding="utf-8")
    (firmware / "AGENTS.md").write_text("agents body", encoding="utf-8")
    (firmware / "CONSTITUTION.md").write_text(
        "constitution body", encoding="utf-8"
    )

    store = _make_store(tmp_path)
    await store.start()
    docs = list(store.documents().items())
    assert [name for name, _ in docs] == [
        "AGENTS",
        "CONSTITUTION",
        "POLICY",
    ]


async def test_only_markdown_files_are_read(tmp_path: Path) -> None:
    firmware = _firmware_dir(tmp_path)
    (firmware / "INSTRUCTIONS.md").write_text("real", encoding="utf-8")
    (firmware / "README.txt").write_text("not md", encoding="utf-8")
    (firmware / "image.png").write_bytes(b"\x89PNG")

    store = _make_store(tmp_path)
    await store.start()
    assert set(store.documents()) == {"INSTRUCTIONS"}


async def test_system_prompt_renders_headers_per_document(
    tmp_path: Path,
) -> None:
    firmware = _firmware_dir(tmp_path)
    (firmware / "CONSTITUTION.md").write_text(
        "Prime directive.\nBe good.\n", encoding="utf-8"
    )
    (firmware / "POLICY.md").write_text(
        "Be polite.\n", encoding="utf-8"
    )

    store = _make_store(tmp_path)
    await store.start()
    prompt = store.system_prompt()
    expected = (
        "## CONSTITUTION\nPrime directive.\nBe good.\n\n"
        "## POLICY\nBe polite."
    )
    assert prompt == expected


async def test_system_prompt_skips_empty_documents(tmp_path: Path) -> None:
    """Whitespace-only documents must not produce an empty header."""
    firmware = _firmware_dir(tmp_path)
    (firmware / "REAL.md").write_text("content", encoding="utf-8")
    (firmware / "BLANK.md").write_text("   \n\n", encoding="utf-8")

    store = _make_store(tmp_path)
    await store.start()
    prompt = store.system_prompt()
    assert "BLANK" not in prompt
    assert "## REAL\ncontent" in prompt


async def test_refresh_picks_up_new_documents(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    await store.start()
    assert store.documents() == {}

    firmware = _firmware_dir(tmp_path)
    (firmware / "NEW.md").write_text("added", encoding="utf-8")
    await store.refresh()
    assert store.documents() == {"NEW": "added"}


async def test_arbitrary_md_filenames_are_read(tmp_path: Path) -> None:
    """No HEARTBEAT-style exclude list; any *.md the operator drops in works."""
    firmware = _firmware_dir(tmp_path)
    (firmware / "CUSTOM-PROMPT.md").write_text("body", encoding="utf-8")
    (firmware / "DEBUG.md").write_text("debug body", encoding="utf-8")

    store = _make_store(tmp_path)
    await store.start()
    assert set(store.documents()) == {"CUSTOM-PROMPT", "DEBUG"}
