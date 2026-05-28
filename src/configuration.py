"""Filesystem layout, slug derivation and config IO for cephix.

Layout
------

::

    ~/.cephix/                       (CEPHIX_HOME or --home overrides this)
    ├── cephix.yaml                  global defaults plus optional robots[] index
    ├── .env                         optional global secrets
    └── robots/
        └── <slug>/                  default workspace per bot (auto-discovered)
            ├── robot.yaml           required: a bot's full configuration
            └── .env                 optional bot-local secrets

Conventions
-----------

- Any directory under ``~/.cephix/robots/`` that contains a
  ``robot.yaml`` is automatically a bot. No index entry required.
- ``cephix.yaml#robots[]`` is **only** for bots whose workspace lives
  somewhere else (and therefore can't be discovered).
- Workspaces are self-contained: paths inside ``robot.yaml`` are
  interpreted relative to the workspace, so a bot directory can be
  moved or symlinked without breaking anything.

This module deliberately avoids the heavier shape of the legacy
``_reference`` configuration: there are no firmware/memory/sops/logs
sub-directories yet because we don't have components that consume
them.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values, set_key

logger = logging.getLogger(__name__)

ROBOT_CONFIG_FILENAME = "robot.yaml"
ROBOT_ENV_FILENAME = ".env"
ROBOTS_DIRNAME = "robots"
HOME_CONFIG_FILENAME = "cephix.yaml"
HOME_ENV_FILENAME = ".env"

# The single environment variable used by the bot's local .env to
# carry the control-plane token. Kept here so the wire-side
# (``ControlPlane``) and the producer side (``onboarding``) stay in
# sync without each importing the other.
CONTROL_PLANE_TOKEN_ENV = "CEPHIX_CONTROL_PLANE_TOKEN"

_PACKAGED_DEFAULTS = Path(__file__).with_name("defaults.yaml")


# ---------------------------------------------------------------------------
# Paths and home
# ---------------------------------------------------------------------------


def home_dir(override: str | Path | None = None) -> Path:
    """Resolve the cephix home directory.

    Order of precedence:

    1. Explicit ``override`` argument
    2. ``CEPHIX_HOME`` environment variable
    3. ``~/.cephix``
    """
    if override is not None:
        path = Path(override).expanduser()
    elif env := os.environ.get("CEPHIX_HOME"):
        path = Path(env).expanduser()
    else:
        path = Path("~/.cephix").expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def home_config_path(home_override: str | Path | None = None) -> Path:
    return home_dir(home_override) / HOME_CONFIG_FILENAME


def robots_root(home_override: str | Path | None = None) -> Path:
    path = home_dir(home_override) / ROBOTS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_workspace_for(robot_id: str, home_override: str | Path | None = None) -> Path:
    return robots_root(home_override) / robot_id


def robot_config_path(workspace: str | Path) -> Path:
    return Path(workspace) / ROBOT_CONFIG_FILENAME


# ---------------------------------------------------------------------------
# Slugification
# ---------------------------------------------------------------------------


def slugify_robot_id(name: str) -> str:
    """Derive a filesystem-/URL-safe id from a human-readable name.

    Uses ``python-slugify`` (with ``text-unidecode``) so unicode
    characters are transliterated rather than dropped. Empty results
    fall back to ``"robot"``.
    """
    from slugify import slugify  # local import: optional dependency surface

    result = slugify(name, lowercase=True)
    return result or "robot"


def unique_slug(base_slug: str, taken: set[str]) -> str:
    """Return ``base_slug`` if available, else ``base_slug-2``, ``-3``, ..."""
    if base_slug not in taken:
        return base_slug
    counter = 2
    while True:
        candidate = f"{base_slug}-{counter}"
        if candidate not in taken:
            return candidate
        counter += 1


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not contain a YAML mapping at the top level")
    return data


def _dump_yaml(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base`` and return a new dict.

    Plain dicts are merged key-by-key with ``override`` winning on
    leaves; nested dicts recurse. Lists and other scalars are replaced
    wholesale.

    **Discriminator rule**: a sub-dict is treated as a *component spec*
    when both sides carry a ``name`` key. ``name`` identifies the
    component class and therefore the constructor signature; if the
    two ``name`` values differ, the sub-dicts describe two unrelated
    components and merging them would produce a Frankenstein spec
    (e.g. an ``LLMActorOpenAI`` with an orphan ``prefix`` field
    inherited from an ``echo`` actor default). In that case the
    override sub-dict replaces the base sub-dict wholesale -- only
    fields the user explicitly wrote for the new component survive.
    Sub-dicts that share the same ``name`` (or where one side omits
    it) merge normally.
    """
    merged = dict(base)
    for key, value in override.items():
        if (
            isinstance(value, dict)
            and key in merged
            and isinstance(merged[key], dict)
            and not _component_identity_changed(merged[key], value)
        ):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _component_identity_changed(
    base: dict[str, Any], override: dict[str, Any]
) -> bool:
    """``True`` when both sides declare conflicting ``name`` values.

    A ``name`` mismatch means the two specs target different
    component classes; their other fields are not interchangeable.
    """
    base_name = base.get("name")
    override_name = override.get("name")
    return (
        base_name is not None
        and override_name is not None
        and base_name != override_name
    )


# ---------------------------------------------------------------------------
# Home config (cephix.yaml)
# ---------------------------------------------------------------------------


def ensure_home_config(home_override: str | Path | None = None) -> Path:
    """Create ``~/.cephix/cephix.yaml`` from packaged defaults if missing.

    The full file (defaults plus comments) is copied verbatim so the
    user gets a self-explanatory starting point. After this call the
    file is guaranteed to exist.
    """
    target = home_config_path(home_override)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_PACKAGED_DEFAULTS, target)
        logger.info("created %s from packaged defaults", target)
    return target


def load_home_config(home_override: str | Path | None = None) -> dict[str, Any]:
    ensure_home_config(home_override)
    return _load_yaml(home_config_path(home_override))


def save_home_config(
    config: dict[str, Any],
    home_override: str | Path | None = None,
) -> Path:
    target = home_config_path(home_override)
    _dump_yaml(config, target)
    return target


def home_defaults(home_override: str | Path | None = None) -> dict[str, Any]:
    """Return the ``defaults:`` block from cephix.yaml (or an empty dict)."""
    cfg = load_home_config(home_override)
    block = cfg.get("defaults") or {}
    if not isinstance(block, dict):
        raise ValueError("cephix.yaml#defaults must be a mapping")
    return block


# ---------------------------------------------------------------------------
# Robot config (per-bot robot.yaml)
# ---------------------------------------------------------------------------


def load_robot_config(workspace: str | Path) -> dict[str, Any]:
    return _load_yaml(robot_config_path(workspace))


def save_robot_config(workspace: str | Path, config: dict[str, Any]) -> Path:
    target = robot_config_path(workspace)
    _dump_yaml(config, target)
    return target


# ---------------------------------------------------------------------------
# .env files (per-bot secrets)
# ---------------------------------------------------------------------------


def robot_env_path(workspace: str | Path) -> Path:
    return Path(workspace) / ROBOT_ENV_FILENAME


def load_robot_env(workspace: str | Path) -> dict[str, str]:
    """Load the bot-local ``.env`` next to ``robot.yaml`` (empty if missing).

    Backed by :func:`dotenv.dotenv_values` so we get the same parser
    behaviour everyone else in the Python ecosystem uses (quoted
    values, escapes, multi-line values, comments, ...).
    """
    path = robot_env_path(workspace)
    if not path.is_file():
        return {}
    parsed = dotenv_values(path)
    return {k: v for k, v in parsed.items() if v is not None}


def write_robot_env(workspace: str | Path, values: dict[str, str]) -> Path:
    """Merge ``values`` into the bot-local ``.env`` and rewrite the file.

    Existing variables are preserved with their original formatting;
    the ones in ``values`` are overwritten in place or appended at the
    end. ``dotenv.set_key`` does the heavy lifting so quoting and
    escaping match what other tools expect.

    On POSIX the file is chmod'd to 0600 (owner-only) since it
    typically holds secrets like the control-plane token. On Windows
    the call is best-effort.
    """
    path = robot_env_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
    for key, value in values.items():
        set_key(str(path), key, value, quote_mode="never")
    try:
        path.chmod(0o600)
    except (OSError, NotImplementedError):
        # Windows or restricted filesystem: best effort, the file lives
        # under the user's home anyway.
        pass
    return path


# ---------------------------------------------------------------------------
# Index entries (cephix.yaml#robots[])
# ---------------------------------------------------------------------------


def _index_entries(home_override: str | Path | None = None) -> list[dict[str, Any]]:
    cfg = load_home_config(home_override)
    raw = cfg.get("robots")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("cephix.yaml#robots must be a list")
    return [dict(entry) for entry in raw if isinstance(entry, dict)]


def register_robot_override(
    robot_id: str,
    workspace: str | Path,
    home_override: str | Path | None = None,
) -> None:
    """Record an out-of-convention workspace in ``cephix.yaml#robots[]``."""
    cfg = load_home_config(home_override)
    robots = cfg.get("robots") or []
    if not isinstance(robots, list):
        robots = []
    robots = [r for r in robots if not (isinstance(r, dict) and r.get("id") == robot_id)]
    robots.append({"id": robot_id, "workspace": str(Path(workspace))})
    robots.sort(key=lambda r: str(r.get("id", "")))
    cfg["robots"] = robots
    save_home_config(cfg, home_override)


def unregister_robot_override(
    robot_id: str,
    home_override: str | Path | None = None,
) -> None:
    cfg = load_home_config(home_override)
    robots = cfg.get("robots") or []
    if not isinstance(robots, list):
        return
    cfg["robots"] = [
        r for r in robots if not (isinstance(r, dict) and r.get("id") == robot_id)
    ]
    save_home_config(cfg, home_override)


# ---------------------------------------------------------------------------
# Discovery and resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RobotInstance:
    """A discovered or indexed robot, ready to be loaded."""

    id: str
    name: str
    enabled: bool
    workspace: Path
    robot_yaml: Path

    @property
    def exists(self) -> bool:
        return self.robot_yaml.is_file()


def _instance_from_workspace(robot_id: str, workspace: Path) -> RobotInstance | None:
    cfg_path = robot_config_path(workspace)
    if not cfg_path.is_file():
        return None
    try:
        cfg = _load_yaml(cfg_path)
    except Exception:
        logger.warning("could not parse %s; skipping", cfg_path, exc_info=True)
        return None
    name = str(cfg.get("name") or robot_id)
    enabled = bool(cfg.get("enabled", True))
    return RobotInstance(
        id=robot_id,
        name=name,
        enabled=enabled,
        workspace=workspace,
        robot_yaml=cfg_path,
    )


def discover_robots(home_override: str | Path | None = None) -> list[RobotInstance]:
    """Return every known robot, deduplicated by id.

    Lookup order per id:

    1. Index entries in ``cephix.yaml#robots[]`` (workspace overrides).
    2. Auto-discovered directories under ``~/.cephix/robots/*/``.

    Index entries win when both a directory under the convention and an
    index entry exist for the same id.
    """
    seen: dict[str, RobotInstance] = {}

    for entry in _index_entries(home_override):
        robot_id = str(entry.get("id") or "").strip()
        if not robot_id:
            continue
        workspace_raw = entry.get("workspace")
        if workspace_raw:
            workspace = Path(str(workspace_raw)).expanduser()
        else:
            workspace = default_workspace_for(robot_id, home_override)
        instance = _instance_from_workspace(robot_id, workspace)
        if instance is not None:
            seen[robot_id] = instance

    root = robots_root(home_override)
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            robot_id = child.name
            if robot_id in seen:
                continue
            instance = _instance_from_workspace(robot_id, child)
            if instance is not None:
                seen[robot_id] = instance

    return sorted(seen.values(), key=lambda r: r.id)


def resolve_robot_instance(
    robot_id: str,
    home_override: str | Path | None = None,
) -> RobotInstance:
    """Resolve a single robot id to its :class:`RobotInstance` or raise."""
    for entry in _index_entries(home_override):
        if str(entry.get("id") or "") != robot_id:
            continue
        workspace_raw = entry.get("workspace")
        if workspace_raw:
            workspace = Path(str(workspace_raw)).expanduser()
        else:
            workspace = default_workspace_for(robot_id, home_override)
        instance = _instance_from_workspace(robot_id, workspace)
        if instance is None:
            raise FileNotFoundError(
                f"robot {robot_id!r} is registered with workspace {workspace}, "
                f"but no robot.yaml was found there"
            )
        return instance

    workspace = default_workspace_for(robot_id, home_override)
    instance = _instance_from_workspace(robot_id, workspace)
    if instance is None:
        raise FileNotFoundError(f"no such robot: {robot_id!r}")
    return instance
