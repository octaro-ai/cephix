"""``cephix`` command-line entry point.

The CLI is a thin helper around three things:

- the on-disk configuration in ``~/.cephix/`` (see :mod:`src.configuration`)
- the component registry (see :mod:`src.registry`)
- the robot builder (see :mod:`src.builder`)

Anything ``cephix`` can do interactively can also be done by editing
the YAML files directly. The CLI never owns state that doesn't live on
disk.

Smart default behaviour (``cephix`` without a subcommand):

- 0 robots: launch the onboarding wizard
- 1 robot: start that one
- n robots: print the list and prompt for a choice (TTY only)

In non-TTY environments (systemd, Docker, pipes) the smart default is
disabled and an explicit subcommand is required, so unattended runs
fail loudly instead of silently picking a behaviour.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path
from typing import Sequence

from src.configuration import (
    RobotInstance,
    discover_robots,
    ensure_home_config,
    home_defaults,
    home_dir,
    load_robot_config,
    resolve_robot_instance,
    save_robot_config,
    unregister_robot_override,
)
from src.logging_config import configure_logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_init(args: argparse.Namespace) -> int:
    from src.onboarding import run_wizard

    name_arg: str | None = args.name
    home_override = _normalise_home(args.home)
    ensure_home_config(home_override)
    instance = run_wizard(name=name_arg, home_override=home_override)
    if instance is None:
        return 1
    print(f"\nStart with: cephix start {instance.id}\n")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    home_override = _normalise_home(args.home)
    ensure_home_config(home_override)
    instances = discover_robots(home_override)
    if not instances:
        print("No robots configured yet. Run: cephix init")
        return 0
    _print_instance_table(instances)
    return 0


def _cmd_start(args: argparse.Namespace) -> int:
    home_override = _normalise_home(args.home)
    ensure_home_config(home_override)
    try:
        instance = resolve_robot_instance(args.robot_id, home_override=home_override)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not instance.enabled:
        print(
            f"warning: robot {instance.id!r} is disabled in robot.yaml; starting anyway",
            file=sys.stderr,
        )
    return _start_instance(instance, log_file=args.log_file, log_level=args.log_level)


def _cmd_config(args: argparse.Namespace) -> int:
    from src.onboarding import reconfigure

    home_override = _normalise_home(args.home)
    ensure_home_config(home_override)
    try:
        instance = resolve_robot_instance(args.robot_id, home_override=home_override)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    reconfigure(instance, home_override=home_override)
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    """Delete a robot for good: workspace and index entry both go.

    For the soft case ("hide it from the smart default but keep the
    config"), use ``cephix disable`` instead.
    """
    home_override = _normalise_home(args.home)
    ensure_home_config(home_override)
    try:
        instance = resolve_robot_instance(args.robot_id, home_override=home_override)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not args.yes:
        prompt = (
            f"this will permanently delete robot {instance.id!r} and its "
            f"workspace at {instance.workspace}.\n"
            "(use 'cephix disable' to keep it on disk.)\n"
            "continue? [y/N] "
        )
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            answer = ""
        if answer not in {"y", "yes"}:
            print("aborted")
            return 1

    unregister_robot_override(instance.id, home_override=home_override)
    if instance.workspace.exists():
        shutil.rmtree(instance.workspace, ignore_errors=False)
        print(f"deleted workspace {instance.workspace}")
    print(f"removed robot {instance.id!r}")
    return 0


def _cmd_disable(args: argparse.Namespace) -> int:
    return _set_enabled(args, enabled=False)


def _cmd_enable(args: argparse.Namespace) -> int:
    return _set_enabled(args, enabled=True)


def _set_enabled(args: argparse.Namespace, *, enabled: bool) -> int:
    home_override = _normalise_home(args.home)
    ensure_home_config(home_override)
    try:
        instance = resolve_robot_instance(args.robot_id, home_override=home_override)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    cfg = load_robot_config(instance.workspace)
    if cfg.get("enabled", True) == enabled:
        state = "enabled" if enabled else "disabled"
        print(f"robot {instance.id!r} is already {state}")
        return 0
    cfg["enabled"] = enabled
    save_robot_config(instance.workspace, cfg)
    state = "enabled" if enabled else "disabled"
    print(f"robot {instance.id!r} is now {state}")
    return 0


# ---------------------------------------------------------------------------
# Smart default
# ---------------------------------------------------------------------------


def _smart_default(args: argparse.Namespace) -> int:
    home_override = _normalise_home(args.home)
    ensure_home_config(home_override)
    instances = discover_robots(home_override)

    is_tty = sys.stdin.isatty() and sys.stdout.isatty()

    if not instances:
        if not is_tty:
            print(
                "error: no robots configured. Run: cephix init",
                file=sys.stderr,
            )
            return 1
        from src.onboarding import run_wizard

        instance = run_wizard(name=None, home_override=home_override)
        if instance is None:
            return 1
        print(f"\nStart with: cephix start {instance.id}\n")
        return 0

    if len(instances) == 1:
        only = instances[0]
        if not only.enabled and not is_tty:
            print(
                f"error: only configured robot {only.id!r} is disabled; "
                f"explicit `cephix start {only.id}` would override",
                file=sys.stderr,
            )
            return 1
        return _start_instance(only, log_file=args.log_file, log_level=args.log_level)

    if not is_tty:
        print(
            "error: multiple robots configured; specify one with "
            "`cephix start <id>` or `cephix list`",
            file=sys.stderr,
        )
        return 1

    print("Multiple robots are configured:\n")
    _print_instance_table(instances)
    print()
    selected = _prompt_for_robot([i.id for i in instances])
    if selected is None:
        return 1
    chosen = next(i for i in instances if i.id == selected)
    return _start_instance(chosen, log_file=args.log_file, log_level=args.log_level)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_home(home: str | None) -> Path | None:
    if home is None or home == "":
        return None
    return Path(home).expanduser()


def _resolve_log_file(explicit: str | None, *, workspace: Path) -> str | None:
    """Decide where the operational console log goes.

    Precedence:

    - ``--log-file <path>`` always wins. The user is in charge.
    - Interactive terminal (stderr is a TTY) -> ``None`` -> stderr.
      Convenient for ``cephix start`` in a developer shell: logs
      stream to the terminal, no stale files left behind.
    - Detached / daemon-style runs (no TTY: systemd, Docker, pipe)
      -> ``<workspace>/logs/cephix.log``. The directory is created
      lazily so a fresh workspace doesn't need any pre-setup.

    The structured persistence files (``logs/telemetry.jsonl``,
    ``logs/audit.jsonl``) live in the same ``logs/`` directory. The
    operational log sits next to them so all human- and
    machine-readable trails of one bot are co-located.
    """
    if explicit is not None:
        return explicit
    if sys.stderr.isatty():
        return None
    log_dir = workspace / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return str(log_dir / "cephix.log")


def _print_instance_table(instances: Sequence[RobotInstance]) -> None:
    id_width = max((len(i.id) for i in instances), default=2)
    name_width = max((len(i.name) for i in instances), default=4)
    id_width = max(id_width, 2)
    name_width = max(name_width, 4)
    header = f"{'ID':<{id_width}}  {'NAME':<{name_width}}  {'STATUS':<8}  WORKSPACE"
    print(header)
    print("-" * len(header))
    for inst in instances:
        status = "enabled" if inst.enabled else "disabled"
        print(
            f"{inst.id:<{id_width}}  {inst.name:<{name_width}}  {status:<8}  {inst.workspace}"
        )


def _prompt_for_robot(ids: Sequence[str]) -> str | None:
    while True:
        try:
            answer = input("robot id (or 'q' to quit): ").strip()
        except EOFError:
            return None
        if answer.lower() in {"q", "quit", "exit"}:
            return None
        if answer in ids:
            return answer
        print(f"unknown id: {answer!r}")


def _start_instance(
    instance: RobotInstance,
    *,
    log_file: str | None,
    log_level: str,
) -> int:
    from src.builder import build_robot_from_config

    resolved_log_file = _resolve_log_file(log_file, workspace=instance.workspace)
    configure_logging(level=log_level, log_file=resolved_log_file)
    home_override = instance.workspace.parent.parent
    if home_override.name != "robots":
        home_override = None  # workspace lives outside the convention; use defaults

    try:
        defaults = home_defaults(home_override)
    except Exception as exc:
        logger.warning("ignoring cephix.yaml#defaults: %s", exc)
        defaults = {}

    robot_yaml = load_robot_config(instance.workspace)
    declared_id = robot_yaml.get("id")
    if declared_id and declared_id != instance.id:
        logger.warning(
            "robot.yaml#id (%r) does not match resolved id (%r); using resolved id",
            declared_id,
            instance.id,
        )

    robot = build_robot_from_config(
        robot_yaml,
        defaults=defaults,
        workspace=instance.workspace,
    )
    robot.run()
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cephix",
        description="Configuration-driven cephix robot toolchain.",
    )
    parser.add_argument(
        "--home",
        default="",
        help="Override CEPHIX_HOME (default: $CEPHIX_HOME or ~/.cephix)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Log level for the robot process (default: INFO)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="If set, route operational logs to this file instead of stderr",
    )

    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Create a new robot.")
    init_parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Human-readable name (will be slugified into the robot id).",
    )
    init_parser.set_defaults(handler=_cmd_init)

    list_parser = subparsers.add_parser("list", help="List configured robots.")
    list_parser.set_defaults(handler=_cmd_list)

    start_parser = subparsers.add_parser("start", help="Start a robot.")
    start_parser.add_argument("robot_id", help="Robot id to start.")
    start_parser.set_defaults(handler=_cmd_start)

    config_parser = subparsers.add_parser(
        "config", help="Reconfigure an existing robot."
    )
    config_parser.add_argument("robot_id", help="Robot id to reconfigure.")
    config_parser.set_defaults(handler=_cmd_config)

    remove_parser = subparsers.add_parser(
        "remove",
        help="Delete a robot and its workspace. Use 'disable' for a soft toggle.",
    )
    remove_parser.add_argument("robot_id", help="Robot id to remove.")
    remove_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    remove_parser.set_defaults(handler=_cmd_remove)

    disable_parser = subparsers.add_parser(
        "disable",
        help="Mark a robot as disabled (kept on disk, ignored by smart default).",
    )
    disable_parser.add_argument("robot_id", help="Robot id to disable.")
    disable_parser.set_defaults(handler=_cmd_disable)

    enable_parser = subparsers.add_parser(
        "enable", help="Re-enable a previously disabled robot."
    )
    enable_parser.add_argument("robot_id", help="Robot id to enable.")
    enable_parser.set_defaults(handler=_cmd_enable)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "command", None) is None:
        return _smart_default(args)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 1
    return int(handler(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
