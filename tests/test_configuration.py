"""Tests for src.configuration: layout, slug, IO, discovery."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.configuration import (
    deep_merge,
    default_workspace_for,
    discover_robots,
    ensure_home_config,
    home_config_path,
    home_defaults,
    home_dir,
    load_home_config,
    load_robot_config,
    register_robot_override,
    resolve_robot_instance,
    robot_config_path,
    robots_root,
    save_home_config,
    save_robot_config,
    slugify_robot_id,
    unique_slug,
    unregister_robot_override,
)


# ---------------------------------------------------------------------------
# home_dir / paths
# ---------------------------------------------------------------------------


def test_home_dir_with_explicit_override(tmp_path: Path) -> None:
    target = tmp_path / "myhome"
    assert home_dir(target) == target
    assert target.exists()


def test_home_dir_uses_cephix_home_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "envhome"
    monkeypatch.setenv("CEPHIX_HOME", str(target))
    assert home_dir() == target
    assert target.exists()


def test_robots_root_under_home(tmp_path: Path) -> None:
    root = robots_root(tmp_path)
    assert root == tmp_path / "robots"
    assert root.exists()


def test_default_workspace_for(tmp_path: Path) -> None:
    ws = default_workspace_for("dreamgirl", tmp_path)
    assert ws == tmp_path / "robots" / "dreamgirl"


def test_robot_config_path_appends_filename(tmp_path: Path) -> None:
    assert robot_config_path(tmp_path) == tmp_path / "robot.yaml"


# ---------------------------------------------------------------------------
# Slug
# ---------------------------------------------------------------------------


def test_slugify_basic() -> None:
    assert slugify_robot_id("My Bot") == "my-bot"
    assert slugify_robot_id("Über-Bot") == "uber-bot"


def test_slugify_empty_falls_back() -> None:
    assert slugify_robot_id("---") == "robot"


def test_unique_slug_passes_through_when_free() -> None:
    assert unique_slug("foo", set()) == "foo"


def test_unique_slug_appends_counter() -> None:
    assert unique_slug("foo", {"foo"}) == "foo-2"
    assert unique_slug("foo", {"foo", "foo-2"}) == "foo-3"


# ---------------------------------------------------------------------------
# Home config
# ---------------------------------------------------------------------------


def test_ensure_home_config_creates_from_defaults(tmp_path: Path) -> None:
    target = ensure_home_config(tmp_path)
    assert target == home_config_path(tmp_path)
    assert target.exists()
    cfg = load_home_config(tmp_path)
    assert "defaults" in cfg
    assert "robots" in cfg
    assert isinstance(cfg["defaults"], dict)


def test_ensure_home_config_is_idempotent(tmp_path: Path) -> None:
    ensure_home_config(tmp_path)
    cfg_path = home_config_path(tmp_path)
    cfg_path.write_text("custom: 1\n", encoding="utf-8")
    ensure_home_config(tmp_path)
    assert cfg_path.read_text(encoding="utf-8") == "custom: 1\n"


def test_save_home_config_round_trip(tmp_path: Path) -> None:
    save_home_config({"defaults": {"foo": "bar"}, "robots": []}, tmp_path)
    cfg = load_home_config(tmp_path)
    assert cfg["defaults"]["foo"] == "bar"


def test_home_defaults_returns_block(tmp_path: Path) -> None:
    ensure_home_config(tmp_path)
    block = home_defaults(tmp_path)
    assert "kernel" in block
    assert block["kernel"]["type"] == "echo"


# ---------------------------------------------------------------------------
# Robot config
# ---------------------------------------------------------------------------


def test_save_and_load_robot_config(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    save_robot_config(workspace, {"id": "x", "name": "X", "enabled": True})
    cfg = load_robot_config(workspace)
    assert cfg == {"id": "x", "name": "X", "enabled": True}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _make_bot(home: Path, robot_id: str, *, enabled: bool = True, name: str | None = None) -> Path:
    workspace = default_workspace_for(robot_id, home)
    workspace.mkdir(parents=True, exist_ok=True)
    save_robot_config(
        workspace,
        {
            "id": robot_id,
            "name": name or robot_id.capitalize(),
            "enabled": enabled,
        },
    )
    return workspace


def test_discover_robots_empty(tmp_path: Path) -> None:
    ensure_home_config(tmp_path)
    assert discover_robots(tmp_path) == []


def test_discover_robots_finds_convention_bot(tmp_path: Path) -> None:
    ensure_home_config(tmp_path)
    _make_bot(tmp_path, "alpha")
    instances = discover_robots(tmp_path)
    assert [i.id for i in instances] == ["alpha"]
    assert instances[0].name == "Alpha"
    assert instances[0].enabled is True


def test_discover_robots_skips_directories_without_yaml(tmp_path: Path) -> None:
    ensure_home_config(tmp_path)
    (robots_root(tmp_path) / "ghost").mkdir()
    assert discover_robots(tmp_path) == []


def test_discover_robots_dedupes_index_and_convention(tmp_path: Path) -> None:
    ensure_home_config(tmp_path)
    convention_ws = _make_bot(tmp_path, "alpha")
    other_ws = tmp_path / "elsewhere" / "alpha"
    other_ws.mkdir(parents=True)
    save_robot_config(other_ws, {"id": "alpha", "name": "Alpha-Override", "enabled": False})
    register_robot_override("alpha", other_ws, tmp_path)
    instances = discover_robots(tmp_path)
    assert len(instances) == 1
    assert instances[0].name == "Alpha-Override"
    assert instances[0].workspace == other_ws
    assert instances[0].enabled is False
    assert convention_ws.exists()  # convention bot still on disk, just shadowed


def test_resolve_robot_instance_via_convention(tmp_path: Path) -> None:
    ensure_home_config(tmp_path)
    _make_bot(tmp_path, "alpha")
    inst = resolve_robot_instance("alpha", tmp_path)
    assert inst.id == "alpha"
    assert inst.workspace == default_workspace_for("alpha", tmp_path)


def test_resolve_robot_instance_via_index(tmp_path: Path) -> None:
    ensure_home_config(tmp_path)
    other_ws = tmp_path / "elsewhere" / "beta"
    other_ws.mkdir(parents=True)
    save_robot_config(other_ws, {"id": "beta", "name": "Beta", "enabled": True})
    register_robot_override("beta", other_ws, tmp_path)
    inst = resolve_robot_instance("beta", tmp_path)
    assert inst.workspace == other_ws


def test_resolve_robot_instance_missing_raises(tmp_path: Path) -> None:
    ensure_home_config(tmp_path)
    with pytest.raises(FileNotFoundError):
        resolve_robot_instance("ghost", tmp_path)


def test_unregister_robot_override(tmp_path: Path) -> None:
    ensure_home_config(tmp_path)
    other_ws = tmp_path / "elsewhere" / "gamma"
    other_ws.mkdir(parents=True)
    save_robot_config(other_ws, {"id": "gamma", "name": "Gamma", "enabled": True})
    register_robot_override("gamma", other_ws, tmp_path)
    assert any(r.id == "gamma" for r in discover_robots(tmp_path))
    unregister_robot_override("gamma", tmp_path)
    cfg = load_home_config(tmp_path)
    assert all(entry.get("id") != "gamma" for entry in (cfg.get("robots") or []))


# ---------------------------------------------------------------------------
# deep_merge
# ---------------------------------------------------------------------------


def test_deep_merge_overrides_scalars() -> None:
    merged = deep_merge({"a": 1}, {"a": 2})
    assert merged == {"a": 2}


def test_deep_merge_nests_dicts() -> None:
    base = {"k": {"a": 1, "b": 2}}
    override = {"k": {"b": 3, "c": 4}}
    assert deep_merge(base, override) == {"k": {"a": 1, "b": 3, "c": 4}}


def test_deep_merge_replaces_lists_wholesale() -> None:
    base = {"channels": [{"type": "x"}]}
    override = {"channels": [{"type": "y"}, {"type": "z"}]}
    merged = deep_merge(base, override)
    assert merged == {"channels": [{"type": "y"}, {"type": "z"}]}


def test_deep_merge_preserves_originals() -> None:
    base = {"k": {"a": 1}}
    override = {"k": {"b": 2}}
    deep_merge(base, override)
    assert base == {"k": {"a": 1}}
    assert override == {"k": {"b": 2}}
