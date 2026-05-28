"""Tests for :class:`MarkdownFirmwareStore`.

Three groups:

- identity + lifecycle (UTILITY, plain RobotComponent, ``start``
  reads documents, ``stop`` drops the cache);
- inventory + ordering (every ``*.md`` is read, sorted by
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
from src.utility.firmware_store import FirmwareStorePort, MarkdownFirmwareStore


def test_metadata() -> None:
    assert MarkdownFirmwareStore.component_name == "firmware-store"
    assert (
        MarkdownFirmwareStore.component_category is ComponentCategory.UTILITY
    )


def test_is_plain_robot_component(tmp_path: Path) -> None:
    store = MarkdownFirmwareStore(firmware_dir=tmp_path)
    assert isinstance(store, RobotComponent)
    assert not isinstance(store, BusComponent)
    assert isinstance(store, FirmwareStorePort)


async def test_lifecycle_reads_then_drops(tmp_path: Path) -> None:
    (tmp_path / "A.md").write_text("alpha", encoding="utf-8")
    store = MarkdownFirmwareStore(firmware_dir=tmp_path)
    await store.start()
    assert store.documents() == {"A": "alpha"}
    await store.stop()
    assert store.documents() == {}


async def test_missing_directory_is_tolerated(tmp_path: Path) -> None:
    """A missing firmware dir leaves the store empty; no exception."""
    store = MarkdownFirmwareStore(firmware_dir=tmp_path / "ghost")
    await store.start()
    assert store.documents() == {}
    assert store.system_prompt() == ""


async def test_inventory_sorted_by_filename(tmp_path: Path) -> None:
    (tmp_path / "POLICY.md").write_text("policy body", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("agents body", encoding="utf-8")
    (tmp_path / "CONSTITUTION.md").write_text(
        "constitution body", encoding="utf-8"
    )

    store = MarkdownFirmwareStore(firmware_dir=tmp_path)
    await store.start()
    docs = list(store.documents().items())
    assert [name for name, _ in docs] == [
        "AGENTS",
        "CONSTITUTION",
        "POLICY",
    ]


async def test_only_markdown_files_are_read(tmp_path: Path) -> None:
    (tmp_path / "INSTRUCTIONS.md").write_text("real", encoding="utf-8")
    (tmp_path / "README.txt").write_text("not md", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")

    store = MarkdownFirmwareStore(firmware_dir=tmp_path)
    await store.start()
    assert set(store.documents()) == {"INSTRUCTIONS"}


async def test_system_prompt_renders_headers_per_document(
    tmp_path: Path,
) -> None:
    (tmp_path / "CONSTITUTION.md").write_text(
        "Prime directive.\nBe good.\n", encoding="utf-8"
    )
    (tmp_path / "POLICY.md").write_text(
        "Be polite.\n", encoding="utf-8"
    )

    store = MarkdownFirmwareStore(firmware_dir=tmp_path)
    await store.start()
    prompt = store.system_prompt()
    expected = (
        "## CONSTITUTION\nPrime directive.\nBe good.\n\n"
        "## POLICY\nBe polite."
    )
    assert prompt == expected


async def test_system_prompt_skips_empty_documents(tmp_path: Path) -> None:
    """Whitespace-only documents must not produce an empty header."""
    (tmp_path / "REAL.md").write_text("content", encoding="utf-8")
    (tmp_path / "BLANK.md").write_text("   \n\n", encoding="utf-8")

    store = MarkdownFirmwareStore(firmware_dir=tmp_path)
    await store.start()
    prompt = store.system_prompt()
    assert "BLANK" not in prompt
    assert "## REAL\ncontent" in prompt


async def test_refresh_picks_up_new_documents(tmp_path: Path) -> None:
    store = MarkdownFirmwareStore(firmware_dir=tmp_path)
    await store.start()
    assert store.documents() == {}

    (tmp_path / "NEW.md").write_text("added", encoding="utf-8")
    store.refresh()
    assert store.documents() == {"NEW": "added"}


async def test_arbitrary_md_filenames_are_read(tmp_path: Path) -> None:
    """No HEARTBEAT-style exclude list; any *.md the operator drops in works."""
    (tmp_path / "CUSTOM-PROMPT.md").write_text("body", encoding="utf-8")
    (tmp_path / "DEBUG.md").write_text("debug body", encoding="utf-8")

    store = MarkdownFirmwareStore(firmware_dir=tmp_path)
    await store.start()
    assert set(store.documents()) == {"CUSTOM-PROMPT", "DEBUG"}
