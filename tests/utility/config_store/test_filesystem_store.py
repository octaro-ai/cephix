"""Tests for :class:`FilesystemConfigStore`.

Three groups:

- identity + lifecycle (UTILITY, plain RobotComponent, ``start``
  reads files, ``stop`` drops the cache);
- inventory + parsing (every matching YAML is read, file stem is
  the key, top-level lists of mappings are required, malformed
  files are skipped without breaking the rest);
- ``refresh`` re-reads on demand.
"""

from __future__ import annotations

from pathlib import Path

import src.bus  # noqa: F401

from src.components import BusComponent, ComponentCategory, RobotComponent
from src.persistence.filesystem.connection import FilesystemConnection
from src.persistence.filesystem.local_adapter import LocalFSAdapter
from src.utility.config_store import ConfigStorePort, FilesystemConfigStore


def _make_store(
    root: Path,
    *,
    directory: str = "configs",
) -> FilesystemConfigStore:
    connection = FilesystemConnection(adapter=LocalFSAdapter(), root=root)
    return FilesystemConfigStore(connection=connection, directory=directory)


def _configs_dir(root: Path, directory: str = "configs") -> Path:
    target = root / directory
    target.mkdir(parents=True, exist_ok=True)
    return target


def test_metadata() -> None:
    assert FilesystemConfigStore.component_name == "config-store"
    assert (
        FilesystemConfigStore.component_category is ComponentCategory.UTILITY
    )


def test_is_plain_robot_component(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert isinstance(store, RobotComponent)
    assert not isinstance(store, BusComponent)
    assert isinstance(store, ConfigStorePort)


async def test_lifecycle_reads_then_drops(tmp_path: Path) -> None:
    configs = _configs_dir(tmp_path)
    (configs / "heartbeats.yaml").write_text(
        "- id: a\n  cron: '* * * * *'\n", encoding="utf-8"
    )
    store = _make_store(tmp_path)
    await store.start()
    assert store.configs("heartbeats") == [{"id": "a", "cron": "* * * * *"}]
    await store.stop()
    assert store.configs("heartbeats") == []


async def test_missing_directory_is_tolerated(tmp_path: Path) -> None:
    store = _make_store(tmp_path / "ghost")
    await store.start()
    assert store.configs("heartbeats") == []
    assert store.configs("anything") == []


async def test_unknown_key_returns_empty(tmp_path: Path) -> None:
    configs = _configs_dir(tmp_path)
    (configs / "heartbeats.yaml").write_text("- id: a\n", encoding="utf-8")
    store = _make_store(tmp_path)
    await store.start()
    assert store.configs("does-not-exist") == []


async def test_file_stem_is_the_key(tmp_path: Path) -> None:
    configs = _configs_dir(tmp_path)
    (configs / "heartbeats.yaml").write_text("- id: hb\n", encoding="utf-8")
    (configs / "webhooks.yaml").write_text("- id: wh\n", encoding="utf-8")
    store = _make_store(tmp_path)
    await store.start()
    assert store.configs("heartbeats") == [{"id": "hb"}]
    assert store.configs("webhooks") == [{"id": "wh"}]


async def test_only_yaml_files_are_read(tmp_path: Path) -> None:
    configs = _configs_dir(tmp_path)
    (configs / "real.yaml").write_text("- id: x\n", encoding="utf-8")
    (configs / "notes.txt").write_text("text", encoding="utf-8")
    store = _make_store(tmp_path)
    await store.start()
    assert store.configs("real") == [{"id": "x"}]
    assert store.configs("notes") == []


async def test_yml_extension_also_matches(tmp_path: Path) -> None:
    configs = _configs_dir(tmp_path)
    (configs / "alt.yml").write_text("- id: y\n", encoding="utf-8")
    store = _make_store(tmp_path)
    await store.start()
    assert store.configs("alt") == [{"id": "y"}]


async def test_non_list_top_level_is_skipped(tmp_path: Path) -> None:
    """A YAML file whose top level is a mapping is rejected with a warning."""
    configs = _configs_dir(tmp_path)
    (configs / "ok.yaml").write_text("- id: a\n", encoding="utf-8")
    (configs / "bad.yaml").write_text("just_a_key: 1\n", encoding="utf-8")
    store = _make_store(tmp_path)
    await store.start()
    assert store.configs("ok") == [{"id": "a"}]
    assert store.configs("bad") == []


async def test_invalid_yaml_is_skipped(tmp_path: Path) -> None:
    configs = _configs_dir(tmp_path)
    (configs / "ok.yaml").write_text("- id: a\n", encoding="utf-8")
    (configs / "broken.yaml").write_text(
        "- id: ok\n  cron: '* * *'\n   bad-indent\n", encoding="utf-8"
    )
    store = _make_store(tmp_path)
    await store.start()
    assert store.configs("ok") == [{"id": "a"}]
    assert store.configs("broken") == []


async def test_non_mapping_entries_are_dropped(tmp_path: Path) -> None:
    configs = _configs_dir(tmp_path)
    (configs / "mixed.yaml").write_text(
        "- id: a\n- just-a-string\n- id: b\n", encoding="utf-8"
    )
    store = _make_store(tmp_path)
    await store.start()
    assert store.configs("mixed") == [{"id": "a"}, {"id": "b"}]


async def test_empty_file_is_empty_list(tmp_path: Path) -> None:
    configs = _configs_dir(tmp_path)
    (configs / "empty.yaml").write_text("", encoding="utf-8")
    store = _make_store(tmp_path)
    await store.start()
    assert store.configs("empty") == []


async def test_returned_list_is_independent(tmp_path: Path) -> None:
    """Mutating the caller's copy must not affect the cache."""
    configs = _configs_dir(tmp_path)
    (configs / "heartbeats.yaml").write_text("- id: a\n", encoding="utf-8")
    store = _make_store(tmp_path)
    await store.start()
    snapshot = store.configs("heartbeats")
    snapshot.clear()
    assert store.configs("heartbeats") == [{"id": "a"}]


async def test_refresh_picks_up_new_files(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    await store.start()
    assert store.configs("heartbeats") == []

    configs = _configs_dir(tmp_path)
    (configs / "heartbeats.yaml").write_text("- id: new\n", encoding="utf-8")
    await store.refresh()
    assert store.configs("heartbeats") == [{"id": "new"}]
