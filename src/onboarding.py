"""Rich-based onboarding wizard for ``cephix init`` and ``cephix config``.

The wizard writes exactly one file per bot in the standard case:
``~/.cephix/robots/<slug>/robot.yaml``. No ``cephix.yaml`` entry is
created -- auto-discovery picks the bot up automatically.

Component options come from the registry, so any new component that
registers itself becomes selectable here without touching the wizard.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from src.components import ComponentCategory, RobotComponent
from src.configuration import (
    RobotInstance,
    default_workspace_for,
    home_defaults,
    load_robot_config,
    robots_root,
    save_robot_config,
    slugify_robot_id,
    unique_slug,
)
from src.registry import all_registered, list_by_category


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def run_wizard(
    *,
    name: str | None,
    home_override: str | Path | None = None,
    console: Console | None = None,
) -> RobotInstance | None:
    """Create a new bot interactively. Returns the new instance, or ``None``
    if the user aborted."""
    console = console or Console()

    console.print(
        Panel(
            "[bold]Cephix onboarding[/bold]",
            border_style="cyan",
            padding=(0, 1),
        )
    )

    if name is None:
        name = Prompt.ask("Robot name", console=console).strip()
    if not name:
        console.print("[red]aborted: no name provided[/]")
        return None

    base_slug = slugify_robot_id(name)
    taken = _existing_slugs(home_override)
    slug = unique_slug(base_slug, taken)
    if slug != base_slug:
        console.print(
            f"[yellow]slug [bold]{base_slug}[/bold] is taken; "
            f"proposing [bold]{slug}[/bold][/yellow]"
        )
        slug = Prompt.ask("Robot id (slug)", default=slug, console=console).strip()
        if not slug:
            console.print("[red]aborted: empty slug[/]")
            return None
        if slug in taken:
            console.print(f"[red]slug {slug!r} is already taken; aborting[/]")
            return None

    workspace = default_workspace_for(slug, home_override)
    if workspace.exists() and any(workspace.iterdir()):
        console.print(
            f"[red]workspace {workspace} already exists and is not empty; aborting[/]"
        )
        return None

    defaults = home_defaults(home_override)

    bus_spec = _pick_component(
        console, ComponentCategory.BUS, defaults.get("bus") or {"type": "asyncio"}
    )
    kernel_spec = _pick_component(
        console, ComponentCategory.KERNEL, defaults.get("kernel") or {"type": "echo"}
    )
    channel_specs = _pick_channels(console, defaults.get("channels") or [])

    robot_yaml: dict[str, Any] = {
        "id": slug,
        "name": name,
        "enabled": True,
    }
    if bus_spec is not None:
        robot_yaml["bus"] = bus_spec
    if kernel_spec is not None:
        robot_yaml["kernel"] = kernel_spec
    if channel_specs is not None:
        robot_yaml["channels"] = channel_specs

    workspace.mkdir(parents=True, exist_ok=True)
    save_robot_config(workspace, robot_yaml)

    console.print()
    console.print(
        Panel(
            f"[bold]{name}[/] is ready.\n\n"
            f"  ID:        {slug}\n"
            f"  Workspace: {workspace}\n"
            f"  Config:    {workspace / 'robot.yaml'}",
            title="[green]Robot created[/]",
            border_style="green",
            padding=(0, 1),
        )
    )

    return RobotInstance(
        id=slug,
        name=name,
        enabled=True,
        workspace=workspace,
        robot_yaml=workspace / "robot.yaml",
    )


def reconfigure(
    instance: RobotInstance,
    *,
    home_override: str | Path | None = None,
    console: Console | None = None,
) -> None:
    """Re-run the wizard for an existing bot, preserving id / workspace."""
    console = console or Console()
    console.print(
        Panel(
            f"[bold]Reconfigure [cyan]{instance.id}[/cyan][/bold]",
            border_style="cyan",
            padding=(0, 1),
        )
    )

    current = load_robot_config(instance.workspace)
    name = Prompt.ask(
        "Robot name", default=str(current.get("name") or instance.name), console=console
    ).strip() or instance.name
    enabled = Confirm.ask(
        "Enabled?", default=bool(current.get("enabled", True)), console=console
    )

    defaults = home_defaults(home_override)

    bus_default = current.get("bus") or defaults.get("bus") or {"type": "asyncio"}
    kernel_default = current.get("kernel") or defaults.get("kernel") or {"type": "echo"}
    channels_default = current.get("channels") or defaults.get("channels") or []

    bus_spec = _pick_component(console, ComponentCategory.BUS, bus_default)
    kernel_spec = _pick_component(console, ComponentCategory.KERNEL, kernel_default)
    channel_specs = _pick_channels(console, channels_default)

    new_yaml: dict[str, Any] = {
        "id": instance.id,
        "name": name,
        "enabled": enabled,
    }
    if bus_spec is not None:
        new_yaml["bus"] = bus_spec
    if kernel_spec is not None:
        new_yaml["kernel"] = kernel_spec
    if channel_specs is not None:
        new_yaml["channels"] = channel_specs

    save_robot_config(instance.workspace, new_yaml)
    console.print(
        f"[green]updated[/] {instance.workspace / 'robot.yaml'}"
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _existing_slugs(home_override: str | Path | None) -> set[str]:
    root = robots_root(home_override)
    if not root.is_dir():
        return set()
    return {p.name for p in root.iterdir() if p.is_dir()}


def _pick_component(
    console: Console,
    category: ComponentCategory,
    default_spec: dict[str, Any],
) -> dict[str, Any] | None:
    options = list_by_category(category)
    if not options:
        console.print(f"[yellow]no components registered for category {category.value}[/]")
        return default_spec or None

    default_type = (default_spec or {}).get("type") or options[0].component_type
    label = category.value.capitalize()

    if len(options) == 1:
        chosen = options[0]
        console.print(
            f"[dim]{label}: only one option ([bold]{chosen.component_type}[/bold]) — using it.[/]"
        )
    else:
        _show_component_table(console, label, options, default_type)
        choices = [cls.component_type for cls in options]
        type_key = Prompt.ask(
            f"{label} type",
            choices=choices,
            default=default_type if default_type in choices else choices[0],
            console=console,
        )
        chosen = next(cls for cls in options if cls.component_type == type_key)

    spec = _ask_for_kwargs(
        console,
        chosen,
        existing=default_spec if (default_spec or {}).get("type") == chosen.component_type
        else {},
    )
    spec_with_type: dict[str, Any] = {"type": chosen.component_type}
    spec_with_type.update(spec)
    return spec_with_type


def _pick_channels(
    console: Console,
    default_channels: list[Any] | None,
) -> list[dict[str, Any]] | None:
    options = list_by_category(ComponentCategory.CHANNEL)
    if not options:
        return default_channels or []

    console.print()
    default_specs = [dict(spec) for spec in (default_channels or []) if isinstance(spec, dict)]

    if default_specs:
        summary = ", ".join(_summarise_channel(spec) for spec in default_specs)
        use_defaults = Confirm.ask(
            f"Use the default channel(s) ({summary})?",
            default=True,
            console=console,
        )
        if use_defaults:
            return default_specs

    if not Confirm.ask("Add a channel?", default=True, console=console):
        return []

    channels: list[dict[str, Any]] = []
    while True:
        spec = _pick_component(console, ComponentCategory.CHANNEL, {})
        if spec is not None:
            channels.append(spec)
        if not Confirm.ask("Add another channel?", default=False, console=console):
            break
    return channels


def _summarise_channel(spec: dict[str, Any]) -> str:
    """One-line human summary of a channel spec for the Confirm prompt."""
    type_key = spec.get("type") or spec.get("class") or "?"
    extras: list[str] = []
    for field in ("host", "port", "path"):
        if field in spec:
            extras.append(f"{field}={spec[field]}")
    if extras:
        return f"{type_key} ({', '.join(extras)})"
    return str(type_key)


def _show_component_table(
    console: Console,
    label: str,
    options: list[type[RobotComponent]],
    default_type: str,
) -> None:
    table = Table(title=f"Available {label}s", show_lines=False)
    table.add_column("type", style="cyan", no_wrap=True)
    table.add_column("description")
    for cls in options:
        marker = "[bold]*[/bold]" if cls.component_type == default_type else " "
        table.add_row(f"{marker} {cls.component_type}", cls.component_description or "")
    console.print(table)


def _ask_for_kwargs(
    console: Console,
    cls: type[RobotComponent],
    *,
    existing: dict[str, Any],
) -> dict[str, Any]:
    """Prompt for each user-facing parameter declared by ``cls``.

    Only parameters listed in ``cls.component_wizard_fields`` are
    asked. Plumbing parameters (topics, paths, principal templates,
    ...) keep their defaults silently. ``component_wizard_fields = None``
    falls back to "ask for everything" so external components without
    an explicit allow-list still work.
    """
    import inspect

    try:
        sig = inspect.signature(cls)
    except (ValueError, TypeError):
        return {}

    allow = getattr(cls, "component_wizard_fields", None)

    answers: dict[str, Any] = {}
    for param_name, param in sig.parameters.items():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if param_name == "self":
            continue

        if allow is not None and param_name not in allow:
            existing_value = existing.get(param_name)
            if existing_value is not None and (
                param.default is inspect.Parameter.empty
                or existing_value != param.default
            ):
                answers[param_name] = existing_value
            continue

        existing_value = existing.get(param_name)
        if existing_value is not None:
            default = existing_value
        elif param.default is not inspect.Parameter.empty:
            default = param.default
        else:
            default = None

        prompt_text = f"  {cls.component_type}.{param_name}"
        if default is None:
            answer = Prompt.ask(prompt_text, console=console).strip()
            if not answer:
                continue
        else:
            answer = Prompt.ask(prompt_text, default=str(default), console=console)
            if str(answer) == str(default):
                if existing_value is not None:
                    answers[param_name] = existing_value
                continue

        answers[param_name] = _coerce(answer, default)
    return answers


def _coerce(text: str, default: Any) -> Any:
    """Best-effort coerce ``text`` to the type of ``default``."""
    if isinstance(default, bool):
        return text.strip().lower() in {"1", "true", "yes", "y"}
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(text)
        except ValueError:
            return text
    if isinstance(default, float):
        try:
            return float(text)
        except ValueError:
            return text
    return text
