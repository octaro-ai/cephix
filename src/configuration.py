from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
import re
import stat
from typing import Any

import yaml

from src.net import find_free_port

_CORE_MEMORIES_TEMPLATE = """# CORE_MEMORIES

Wirklich praegende, robotweit wichtige Erinnerungen gehoeren hierher.
Nur Schluesselwissen, das ueber alle Runs hinweg praesent bleiben soll.
"""

_DIRECTORY_TEMPLATE = """# DIRECTORY

Verzeichnis fuer andere Roboter, Teilnehmer und einfache Kommunikationswege.
Hier kann spaeter Flurfunk, Kontaktmapping und Robot-zu-Robot-Kommunikation gepflegt werden.
"""

_ENV_TEMPLATE = """# Cephix secrets
# Copy values into ~/.cephix/.env or let the onboarding wizard write them.
#
# Example:
# CEPHIX_MAIN_WS_ACCESS_TOKEN=
# CEPHIX_MAIN_WS_ADMIN_TOKEN=
# OPENAI_API_KEY=
# ANTHROPIC_API_KEY=
"""


@dataclass
class RobotPaths:
    home_dir: Path
    home_config_path: Path
    robot_config_path: Path
    global_env_path: Path
    workspace_dir: Path
    instance_env_path: Path
    firmware_dir: Path
    memory_dir: Path
    logs_dir: Path
    sessions_dir: Path


@dataclass
class RobotInstanceConfig:
    robot_id: str
    robot_name: str
    paths: RobotPaths
    bind: str
    port: int
    access_token: str
    admin_token: str
    access_token_env: str
    admin_token_env: str
    auto_approve_loopback: bool
    poll_interval_seconds: float
    enabled: bool
    autostart: bool
    onboarded: bool


def home_dir(override: str | Path | None = None) -> Path:
    if override is not None:
        path = Path(override).expanduser()
    else:
        path = Path(os.environ.get("CEPHIX_HOME", "~/.cephix")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path(override: str | Path | None = None) -> Path:
    return home_dir(override) / "cephix.yaml"


def global_env_path(override: str | Path | None = None) -> Path:
    return home_dir(override) / ".env"


def env_template_path(override: str | Path | None = None) -> Path:
    return home_dir(override) / ".env.template"


def instance_env_path(workspace_dir: str | Path) -> Path:
    return Path(workspace_dir) / ".env"


def robot_config_path(workspace_dir: str | Path) -> Path:
    return Path(workspace_dir) / "robot.yaml"


@lru_cache(maxsize=1)
def load_defaults() -> dict[str, Any]:
    defaults_path = Path(__file__).with_name("defaults.yaml")
    return _load_yaml(defaults_path)


def ensure_home_config(override: str | Path | None = None) -> Path:
    home = home_dir(override)
    cfg_path = config_path(override)
    if not cfg_path.exists():
        save_home_config(load_defaults(), override)
    template_path = env_template_path(home)
    if not template_path.exists():
        template_path.write_text(_ENV_TEMPLATE, encoding="utf-8")
    return cfg_path


def load_home_config(override: str | Path | None = None) -> dict[str, Any]:
    ensure_home_config(override)
    path = config_path(override)
    base = load_defaults()
    if not path.exists():
        return _deep_merge(base, {})
    return _deep_merge(base, _load_yaml(path))


def save_home_config(config: dict[str, Any], override: str | Path | None = None) -> Path:
    path = config_path(override)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False, allow_unicode=True)
    return path


def save_secret(key: str, value: str, target: str | Path) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    replaced = False

    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                lines.append(line)
                continue
            current_key = stripped.split("=", 1)[0].strip()
            if current_key == key:
                lines.append(f"{key}={value}")
                replaced = True
            else:
                lines.append(line)

    if not replaced:
        lines.append(f"{key}={value}")

    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    try:
        target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return target


_KNOWN_API_KEY_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")


def seed_global_env(
    *,
    cwd: str | Path | None = None,
    home_override: str | Path | None = None,
) -> list[str]:
    """Copy known API keys from CWD ``.env`` into ``~/.cephix/.env``.

    Only keys that are **not already present** in the global file are copied.
    Returns the list of keys that were seeded.
    """
    cwd_env = Path(cwd or Path.cwd()) / ".env"
    if not cwd_env.exists():
        return []

    source_map = _read_env_map(cwd_env)
    global_path = global_env_path(home_override)
    existing = _read_env_map(global_path) if global_path.exists() else {}

    seeded: list[str] = []
    for key in _KNOWN_API_KEY_VARS:
        value = source_map.get(key, "")
        if value and not existing.get(key):
            save_secret(key, value, global_path)
            seeded.append(key)
    return seeded


def resolve_robot_instance(
    *,
    robot_id: str,
    robot_name: str | None = None,
    home_override: str | Path | None = None,
    bind_override: str | None = None,
    port_override: int | None = None,
    respect_port_override: bool = False,
    access_token_override: str | None = None,
    admin_token_override: str | None = None,
    auto_approve_loopback_override: bool | None = None,
    poll_interval_override: float | None = None,
) -> RobotInstanceConfig:
    cfg = load_home_config(home_override)
    home = home_dir(home_override)
    home_cfg_path = config_path(home_override)
    robots = list(cfg.get("robots", []))
    defaults = cfg.get("defaults", {})
    websocket_defaults = defaults.get("websocket", {})
    runtime_defaults = defaults.get("runtime", {})

    entry = next((item for item in robots if item.get("id") == robot_id), None)
    workspace_dir = Path(str(entry.get("workspace"))) if entry and entry.get("workspace") else home / "robots" / robot_id
    robot_cfg_path = (
        Path(str(entry.get("config_path")))
        if entry and entry.get("config_path")
        else robot_config_path(workspace_dir)
    )
    robot_cfg = _load_yaml(robot_cfg_path) if robot_cfg_path.exists() else {}
    onboarded = entry is not None and robot_cfg_path.exists()
    instance_env = instance_env_path(workspace_dir)
    firmware_dir = workspace_dir / "firmware"
    memory_dir = workspace_dir / "memory"
    logs_dir = workspace_dir / "logs"
    sessions_dir = workspace_dir / "sessions"

    entry_ws = dict(entry.get("websocket", {})) if entry else {}
    entry_runtime = dict(entry.get("runtime", {})) if entry else {}
    robot_ws = dict(robot_cfg.get("websocket", {}))
    robot_runtime = dict(robot_cfg.get("runtime", {}))
    resolved_name = (
        robot_name
        or str(robot_cfg.get("name") or "")
        or (str(entry.get("name")) if entry and entry.get("name") else robot_id)
    )
    bind = bind_override if bind_override not in (None, "") else str(robot_ws.get("bind") or entry_ws.get("bind") or websocket_defaults.get("bind", "127.0.0.1"))
    preferred_port = int(port_override) if port_override is not None else int(robot_ws.get("port") or entry_ws.get("port") or websocket_defaults.get("port", 8765))
    port = preferred_port if respect_port_override and port_override is not None else find_free_port(bind=bind, preferred=preferred_port)
    access_token_env = str(robot_ws.get("access_token_env") or entry_ws.get("access_token_env") or websocket_defaults.get("access_token_env") or _robot_secret_env_var(robot_id, "WS_ACCESS_TOKEN"))
    admin_token_env = str(robot_ws.get("admin_token_env") or entry_ws.get("admin_token_env") or websocket_defaults.get("admin_token_env") or _robot_secret_env_var(robot_id, "WS_ADMIN_TOKEN"))
    global_env = global_env_path(home_override)
    access_token = access_token_override if access_token_override is not None else read_secret(access_token_env, instance_env, global_fallback=global_env)
    admin_token = admin_token_override if admin_token_override is not None else read_secret(admin_token_env, instance_env, global_fallback=global_env)
    auto_approve_loopback = (
        auto_approve_loopback_override
        if auto_approve_loopback_override is not None
        else bool(robot_ws.get("auto_approve_loopback", entry_ws.get("auto_approve_loopback", websocket_defaults.get("auto_approve_loopback", True))))
    )
    poll_interval_seconds = (
        poll_interval_override
        if poll_interval_override is not None
        else float(robot_runtime.get("poll_interval_seconds", entry_runtime.get("poll_interval_seconds", runtime_defaults.get("poll_interval_seconds", 0.05))))
    )
    enabled = bool(robot_cfg.get("enabled", entry.get("enabled", True) if entry else True))
    autostart = bool(robot_cfg.get("autostart", entry.get("autostart", False) if entry else False))

    if onboarded:
        onboarded = is_robot_workspace_initialized(workspace_dir)

    return RobotInstanceConfig(
        robot_id=robot_id,
        robot_name=resolved_name,
        paths=RobotPaths(
            home_dir=home,
            home_config_path=home_cfg_path,
            robot_config_path=robot_cfg_path,
            global_env_path=global_env_path(home_override),
            workspace_dir=workspace_dir,
            instance_env_path=instance_env,
            firmware_dir=firmware_dir,
            memory_dir=memory_dir,
            logs_dir=logs_dir,
            sessions_dir=sessions_dir,
        ),
        bind=bind,
        port=port,
        access_token=access_token,
        admin_token=admin_token,
        access_token_env=access_token_env,
        admin_token_env=admin_token_env,
        auto_approve_loopback=auto_approve_loopback,
        poll_interval_seconds=poll_interval_seconds,
        enabled=enabled,
        autostart=autostart,
        onboarded=onboarded,
    )


def onboard_robot_instance(
    *,
    robot_id: str,
    robot_name: str | None = None,
    home_override: str | Path | None = None,
    bind_override: str | None = None,
    port_override: int | None = None,
    respect_port_override: bool = False,
    access_token: str | None = None,
    admin_token: str | None = None,
    auto_approve_loopback: bool | None = None,
    poll_interval_seconds: float | None = None,
    llm_config: dict[str, str] | None = None,
) -> RobotInstanceConfig:
    instance = resolve_robot_instance(
        robot_id=robot_id,
        robot_name=robot_name,
        home_override=home_override,
        bind_override=bind_override,
        port_override=port_override,
        respect_port_override=respect_port_override,
        auto_approve_loopback_override=auto_approve_loopback,
        poll_interval_override=poll_interval_seconds,
    )
    cfg = load_home_config(home_override)
    robots = [item for item in list(cfg.get("robots", [])) if item.get("id") != robot_id]
    robots.append(
        {
            "id": robot_id,
            "name": robot_name or instance.robot_name,
            "workspace": str(instance.paths.workspace_dir),
            "config_path": str(instance.paths.robot_config_path),
            "enabled": True,
            "autostart": False,
        }
    )
    cfg["robots"] = sorted(robots, key=lambda item: str(item.get("id", "")))
    save_home_config(cfg, home_override)

    robot_cfg = {
        "id": robot_id,
        "name": robot_name or instance.robot_name,
        "enabled": True,
        "autostart": False,
        "websocket": {
            "bind": instance.bind,
            "port": instance.port,
            "access_token_env": instance.access_token_env,
            "admin_token_env": instance.admin_token_env,
            "auto_approve_loopback": instance.auto_approve_loopback,
        },
        "runtime": {"poll_interval_seconds": instance.poll_interval_seconds},
    }
    # LLM section: use caller-provided config, fall back to Anthropic defaults.
    effective_llm = llm_config or {}
    robot_cfg["llm"] = {
        "provider": effective_llm.get("provider", "anthropic"),
        "model": effective_llm.get("model", "claude-sonnet-4-20250514"),
        "api_key_env": effective_llm.get("api_key_env", "ANTHROPIC_API_KEY"),
    }
    save_robot_config(robot_cfg, instance.paths.robot_config_path)

    if access_token is not None and access_token != "":
        save_secret(instance.access_token_env, access_token, instance.paths.instance_env_path)
    if admin_token is not None and admin_token != "":
        save_secret(instance.admin_token_env, admin_token, instance.paths.instance_env_path)

    # Persist LLM API key to instance .env if provided directly.
    llm_api_key_value = (effective_llm.get("api_key_value") or "").strip()
    llm_api_key_env = robot_cfg["llm"].get("api_key_env", "")
    if llm_api_key_value and llm_api_key_env:
        save_secret(llm_api_key_env, llm_api_key_value, instance.paths.instance_env_path)

    ensure_robot_workspace(workspace_dir=instance.paths.workspace_dir, robot_name=robot_name or instance.robot_name)

    return resolve_robot_instance(
        robot_id=robot_id,
        robot_name=robot_name or instance.robot_name,
        home_override=home_override,
        bind_override=bind_override,
        port_override=instance.port,
        respect_port_override=True,
        access_token_override=access_token if access_token is not None else instance.access_token,
        admin_token_override=admin_token if admin_token is not None else instance.admin_token,
        auto_approve_loopback_override=instance.auto_approve_loopback,
        poll_interval_override=instance.poll_interval_seconds,
    )


def ensure_robot_workspace(*, workspace_dir: Path, robot_name: str) -> bool:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    env_file = instance_env_path(workspace_dir)
    firmware_dir = workspace_dir / "firmware"
    memory_dir = workspace_dir / "memory"
    logs_dir = workspace_dir / "logs"
    sessions_dir = workspace_dir / "sessions"
    daily_dir = memory_dir / "daily"

    firmware_dir.mkdir(exist_ok=True)
    memory_dir.mkdir(exist_ok=True)
    logs_dir.mkdir(exist_ok=True)
    sessions_dir.mkdir(exist_ok=True)
    daily_dir.mkdir(exist_ok=True)

    changed = False
    template_root = Path(__file__).resolve().parent.parent / "robot"
    firmware_templates = template_root / "firmware"
    memory_templates = template_root / "memory"

    for source in firmware_templates.glob("*.md"):
        target = firmware_dir / source.name
        if not target.exists():
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            changed = True

    for source in memory_templates.glob("*.md"):
        target = memory_dir / source.name
        if not target.exists():
            content = source.read_text(encoding="utf-8")
            if source.name == "IDENTITY.md":
                content = content.replace("Cephix", robot_name)
            target.write_text(content, encoding="utf-8")
            changed = True

    extra_files = {
        memory_dir / "DIRECTORY.md": _DIRECTORY_TEMPLATE,
        memory_dir / "CORE_MEMORIES.md": _CORE_MEMORIES_TEMPLATE,
    }
    for target, content in extra_files.items():
        if not target.exists():
            target.write_text(content, encoding="utf-8")
            changed = True

    gitkeep = daily_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("", encoding="utf-8")
        changed = True

    if not env_file.exists():
        env_file.write_text("# Instance secrets for this robot\n", encoding="utf-8")
        try:
            env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        changed = True

    return changed


def is_robot_workspace_initialized(workspace_dir: Path) -> bool:
    required = [
        workspace_dir / "firmware" / "AGENTS.md",
        workspace_dir / "firmware" / "POLICY.md",
        workspace_dir / "firmware" / "CONSTITUTION.md",
        workspace_dir / "memory" / "IDENTITY.md",
        workspace_dir / "memory" / "MEMORY.md",
    ]
    return all(path.exists() for path in required)


def save_robot_config(config: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False, allow_unicode=True)
    return target


def slugify_robot_id(name: str) -> str:
    """Derive a filesystem/URL-safe robot ID from a human-readable name.

    Uses ``python-slugify`` (with ``text-unidecode``) so that unicode
    characters are transliterated rather than dropped:

        ``"Über-Bot"`` → ``"uber-bot"``
        ``"Dreamgirl"`` → ``"dreamgirl"``
        ``"My Cool Bot 3"`` → ``"my-cool-bot-3"``
    """
    from slugify import slugify
    result = slugify(name, lowercase=True)
    return result or "robot"


def _robot_secret_env_var(robot_id: str, suffix: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", robot_id).strip("_").upper() or "MAIN"
    return f"CEPHIX_{slug}_{suffix}"


def read_secret(
    key: str,
    source: str | Path,
    *,
    global_fallback: str | Path | None = None,
) -> str:
    """Resolve a secret with layered fallback: instance .env → global .env → OS env."""
    value = _read_env_map(Path(source)).get(key, "")
    if value:
        return value
    if global_fallback is not None:
        value = _read_env_map(Path(global_fallback)).get(key, "")
        if value:
            return value
    return os.environ.get(key, "")


def has_secret(key: str, source: str | Path, *, global_fallback: str | Path | None = None) -> bool:
    return bool(read_secret(key, source, global_fallback=global_fallback))


def copy_secret(key: str, *, source: str | Path, target: str | Path) -> bool:
    value = read_secret(key, source)
    if value == "":
        return False
    save_secret(key, value, target)
    return True


def load_global_secret_candidates(
    home_override: str | Path | None,
    *keys: str,
) -> dict[str, bool]:
    env_map = _read_env_map(global_env_path(home_override))
    return {key: bool(env_map.get(key, "")) for key in keys}


def _read_env_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    env_map: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        env_map[key.strip()] = value.strip()
    return env_map


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return dict(data)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
