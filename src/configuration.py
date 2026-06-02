"""Filesystem layout, slug derivation and config IO for cephix.

Layout
------

::

    ~/.cephix/                       (CEPHIX_HOME or --home overrides this)
    ├── cephix.yaml                  global defaults plus optional robots[] index
    ├── .env                         optional global secrets
    └── robots/
        └── <slug>/                  a bot's "robot home" (auto-discovered)
            ├── robot.yaml           required: a bot's full configuration
            ├── .env                 optional bot-local secrets
            ├── logs/                telemetry, audit, console log
            ├── sessions/            session store
            ├── firmware/            CONSTITUTION.md, POLICY.md, ...
            ├── configs/             user-editable YAML (heartbeats.yaml, ...)
            └── workspace/           files the robot reads/writes via its tools

Conventions
-----------

- Any directory under ``~/.cephix/robots/`` that contains a
  ``robot.yaml`` is automatically a bot. No index entry required.
- ``cephix.yaml#robots[]`` is **only** for bots whose robot home lives
  somewhere else (and therefore can't be discovered). The path is
  stored under ``home:``; legacy entries using ``workspace:`` are
  still read.
- Robot homes are self-contained: paths inside ``robot.yaml`` are
  interpreted relative to the robot home, so a bot directory can be
  moved or symlinked without breaking anything.

Robot home vs workspace
-----------------------

The **robot home** is the bot's whole on-disk presence -- config,
secrets and all internal state (``logs/``, ``sessions/``,
``firmware/``, ``configs/``). The **workspace** is the narrower
``<robot_home>/workspace/`` sub-directory: the sandbox the robot's
filesystem tools are rooted at, deliberately kept apart from the
bot's own machinery so a tool call can't read ``.env`` or rewrite
firmware.

These sub-directories are created lazily by the components that
consume them, not by this module -- it only knows about
``robot.yaml`` and ``.env``.
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
# Sandboxed sub-directory of a robot home that the filesystem tools
# are rooted at. Separate from the bot's machinery (logs, .env, ...).
ROBOT_WORKSPACE_DIRNAME = "workspace"
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


def default_robot_home_for(
    robot_id: str, home_override: str | Path | None = None
) -> Path:
    """Conventional robot home: ``~/.cephix/robots/<robot_id>/``."""
    return robots_root(home_override) / robot_id


def robot_workspace_path(robot_home: str | Path) -> Path:
    """The robot's sandboxed tool workspace: ``<robot_home>/workspace/``.

    This is the directory the filesystem tools are rooted at -- a
    deliberately narrow slice of the robot home, kept apart from the
    bot's own machinery (``logs/``, ``.env``, ``firmware/``).
    """
    return Path(robot_home) / ROBOT_WORKSPACE_DIRNAME


def robot_config_path(robot_home: str | Path) -> Path:
    return Path(robot_home) / ROBOT_CONFIG_FILENAME


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
    wholesale. Originals are not mutated.

    The merge is intentionally generic. Configuration-specific concerns
    (component-name discriminators, ``null`` -> "delete this slot",
    template selection) are NOT handled here -- they live in the
    builder, which understands the schema. ``deep_merge`` stays a
    primitive that any layer of the stack can rely on.
    """
    merged = dict(base)
    for key, value in override.items():
        if (
            isinstance(value, dict)
            and key in merged
            and isinstance(merged[key], dict)
        ):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# Component library: per-(category, name) field defaults
# ---------------------------------------------------------------------------


class ComponentLibrary:
    """Index of default constructor fields per ``(category, name)``.

    The library is the second layer in the three-stage build pipeline
    (template -> instance -> library). Each entry says: "if the user
    picks an *X*-category component named *Y*, here are the field
    defaults you should fill in unless the user already set them
    explicitly."

    Wire format under ``cephix.yaml#defaults.components``::

        components:
          actor:
            - {name: echo, prefix: "echo: "}
            - {name: llm.openai, provider: openai, timeout: 60.0}
          kernel:
            - {name: base, input_topic: input.message,
               actor_timeout: 30.0}
          channel:
            - {name: websocket, host: 127.0.0.1, port: 8765}

    Each entry is a regular component spec minus the ``name:``
    discriminator (which is consumed as the index key and stripped
    from the stored field map).

    Validation at construction time:

    - Each entry must be a mapping with a non-empty string ``name:``.
    - Within a category, ``name:`` must be unique. Two entries with
      the same name would silently shadow each other; we raise
      :class:`ValueError` instead.
    """

    def __init__(self, raw: dict[str, Any] | None) -> None:
        self._index: dict[tuple[str, str], dict[str, Any]] = {}
        if raw is None:
            return
        if not isinstance(raw, dict):
            raise ValueError(
                "defaults.components must be a mapping of "
                "category -> [entries]"
            )
        for category, entries in raw.items():
            if entries is None:
                continue
            if not isinstance(entries, list):
                raise ValueError(
                    f"defaults.components.{category} must be a list of "
                    "component-default entries"
                )
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    raise ValueError(
                        f"defaults.components.{category}[{index}] must "
                        "be a mapping"
                    )
                name = entry.get("name")
                if not isinstance(name, str) or not name:
                    raise ValueError(
                        f"defaults.components.{category}[{index}] is "
                        "missing a non-empty 'name'"
                    )
                key = (category, name)
                if key in self._index:
                    raise ValueError(
                        f"defaults.components.{category}: duplicate "
                        f"entry for name={name!r}"
                    )
                self._index[key] = {
                    k: v for k, v in entry.items() if k != "name"
                }

    def defaults_for(self, category: str, name: str) -> dict[str, Any]:
        """Return a fresh dict of default fields for ``(category, name)``.

        Returns an empty dict if no library entry exists -- the
        component just builds from whatever the spec already carries.
        """
        return dict(self._index.get((category, name), {}))

    def has_entry(self, category: str, name: str) -> bool:
        return (category, name) in self._index

    def __contains__(self, item: tuple[str, str]) -> bool:
        return item in self._index


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


def load_robot_config(robot_home: str | Path) -> dict[str, Any]:
    return _load_yaml(robot_config_path(robot_home))


def save_robot_config(robot_home: str | Path, config: dict[str, Any]) -> Path:
    target = robot_config_path(robot_home)
    _dump_yaml(config, target)
    return target


# ---------------------------------------------------------------------------
# .env files (per-bot secrets)
# ---------------------------------------------------------------------------


def robot_env_path(robot_home: str | Path) -> Path:
    return Path(robot_home) / ROBOT_ENV_FILENAME


def load_robot_env(robot_home: str | Path) -> dict[str, str]:
    """Load the bot-local ``.env`` next to ``robot.yaml`` (empty if missing).

    Backed by :func:`dotenv.dotenv_values` so we get the same parser
    behaviour everyone else in the Python ecosystem uses (quoted
    values, escapes, multi-line values, comments, ...).
    """
    path = robot_env_path(robot_home)
    if not path.is_file():
        return {}
    parsed = dotenv_values(path)
    return {k: v for k, v in parsed.items() if v is not None}


def write_robot_env(robot_home: str | Path, values: dict[str, str]) -> Path:
    """Merge ``values`` into the bot-local ``.env`` and rewrite the file.

    Existing variables are preserved with their original formatting;
    the ones in ``values`` are overwritten in place or appended at the
    end. ``dotenv.set_key`` does the heavy lifting so quoting and
    escaping match what other tools expect.

    On POSIX the file is chmod'd to 0600 (owner-only) since it
    typically holds secrets like the control-plane token. On Windows
    the call is best-effort.
    """
    path = robot_env_path(robot_home)
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


def _index_entry_home(entry: dict[str, Any]) -> str | None:
    """Read the robot-home path from a ``cephix.yaml#robots[]`` entry.

    Prefers the current ``home:`` key; falls back to the legacy
    ``workspace:`` key so configs written before the rename keep
    resolving. Returns ``None`` when neither is set (the bot then
    falls back to its conventional home).
    """
    raw = entry.get("home")
    if raw is None:
        raw = entry.get("workspace")  # legacy key
    return str(raw) if raw else None


def register_robot_override(
    robot_id: str,
    robot_home: str | Path,
    home_override: str | Path | None = None,
) -> None:
    """Record an out-of-convention robot home in ``cephix.yaml#robots[]``."""
    cfg = load_home_config(home_override)
    robots = cfg.get("robots") or []
    if not isinstance(robots, list):
        robots = []
    robots = [r for r in robots if not (isinstance(r, dict) and r.get("id") == robot_id)]
    robots.append({"id": robot_id, "home": str(Path(robot_home))})
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
    home: Path
    robot_yaml: Path

    @property
    def exists(self) -> bool:
        return self.robot_yaml.is_file()

    @property
    def workspace(self) -> Path:
        """The robot's sandboxed tool workspace (``<home>/workspace/``)."""
        return robot_workspace_path(self.home)


def _instance_from_robot_home(robot_id: str, robot_home: Path) -> RobotInstance | None:
    cfg_path = robot_config_path(robot_home)
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
        home=robot_home,
        robot_yaml=cfg_path,
    )


def discover_robots(home_override: str | Path | None = None) -> list[RobotInstance]:
    """Return every known robot, deduplicated by id.

    Lookup order per id:

    1. Index entries in ``cephix.yaml#robots[]`` (robot-home overrides).
    2. Auto-discovered directories under ``~/.cephix/robots/*/``.

    Index entries win when both a directory under the convention and an
    index entry exist for the same id.
    """
    seen: dict[str, RobotInstance] = {}

    for entry in _index_entries(home_override):
        robot_id = str(entry.get("id") or "").strip()
        if not robot_id:
            continue
        home_raw = _index_entry_home(entry)
        if home_raw:
            robot_home = Path(home_raw).expanduser()
        else:
            robot_home = default_robot_home_for(robot_id, home_override)
        instance = _instance_from_robot_home(robot_id, robot_home)
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
            instance = _instance_from_robot_home(robot_id, child)
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
        home_raw = _index_entry_home(entry)
        if home_raw:
            robot_home = Path(home_raw).expanduser()
        else:
            robot_home = default_robot_home_for(robot_id, home_override)
        instance = _instance_from_robot_home(robot_id, robot_home)
        if instance is None:
            raise FileNotFoundError(
                f"robot {robot_id!r} is registered with home {robot_home}, "
                f"but no robot.yaml was found there"
            )
        return instance

    robot_home = default_robot_home_for(robot_id, home_override)
    instance = _instance_from_robot_home(robot_id, robot_home)
    if instance is None:
        raise FileNotFoundError(f"no such robot: {robot_id!r}")
    return instance
