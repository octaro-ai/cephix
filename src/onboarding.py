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

import secrets

from src.components import ComponentCategory, RobotComponent
from src.configuration import (
    CONTROL_PLANE_TOKEN_ENV,
    RobotInstance,
    default_robot_home_for,
    home_defaults,
    load_robot_config,
    load_robot_env,
    robot_workspace_path,
    robots_root,
    save_robot_config,
    slugify_robot_id,
    unique_slug,
    write_robot_env,
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

    robot_home = default_robot_home_for(slug, home_override)
    if robot_home.exists() and any(robot_home.iterdir()):
        console.print(
            f"[red]robot home {robot_home} already exists and is not empty; aborting[/]"
        )
        return None

    defaults = home_defaults(home_override)

    # Walk the slots that the user can pick. Defaults come from the
    # selected template (today: ``default``) so the wizard reflects the
    # blueprint the robot will actually be built from. The template
    # name itself is recorded in robot.yaml so the build pipeline
    # falls back to its slots whenever the instance is silent.
    template_name = "default"
    blueprint = _resolve_blueprint(defaults, template_name)

    bus_spec = _pick_component(
        console, ComponentCategory.BUS, blueprint.get("bus") or {"name": "asyncio"}
    )
    kernel_blueprint = dict(blueprint.get("kernel") or {"name": "base"})
    actor_default = dict(kernel_blueprint.pop("actor", None) or {"name": "echo"})
    kernel_spec = _pick_component(
        console, ComponentCategory.KERNEL, kernel_blueprint
    )
    actor_spec = _pick_component(
        console, ComponentCategory.ACTOR, actor_default
    )
    channel_specs = _pick_channels(console, blueprint.get("channels") or [])

    if kernel_spec is not None and actor_spec is not None:
        kernel_spec["actor"] = actor_spec

    robot_yaml: dict[str, Any] = {
        "id": slug,
        "name": name,
        "enabled": True,
        "template": template_name,
    }
    if bus_spec is not None:
        robot_yaml["bus"] = bus_spec
    if kernel_spec is not None:
        robot_yaml["kernel"] = kernel_spec
    if channel_specs is not None:
        robot_yaml["channels"] = channel_specs

    robot_home.mkdir(parents=True, exist_ok=True)
    save_robot_config(robot_home, robot_yaml)

    # Create the filesystem-tool sandbox up front so it shows in the
    # fresh robot home and the tool can list it immediately.
    workspace = robot_workspace_path(robot_home)
    workspace.mkdir(parents=True, exist_ok=True)

    env_path = _ensure_control_plane_token(robot_home)

    console.print()
    console.print(
        Panel(
            f"[bold]{name}[/] is ready.\n\n"
            f"  ID:         {slug}\n"
            f"  Robot home: {robot_home}\n"
            f"  Config:     {robot_home / 'robot.yaml'}\n"
            f"  Workspace:  {workspace}  [dim](robot's file sandbox)[/]\n"
            f"  Secrets:    {env_path}  [dim](control-plane token)[/]",
            title="[green]Robot created[/]",
            border_style="green",
            padding=(0, 1),
        )
    )

    return RobotInstance(
        id=slug,
        name=name,
        enabled=True,
        home=robot_home,
        robot_yaml=robot_home / "robot.yaml",
    )


def reconfigure(
    instance: RobotInstance,
    *,
    home_override: str | Path | None = None,
    console: Console | None = None,
) -> None:
    """Re-run the wizard for an existing bot, preserving id / robot home."""
    console = console or Console()
    console.print(
        Panel(
            f"[bold]Reconfigure [cyan]{instance.id}[/cyan][/bold]",
            border_style="cyan",
            padding=(0, 1),
        )
    )

    current = load_robot_config(instance.home)
    name = Prompt.ask(
        "Robot name", default=str(current.get("name") or instance.name), console=console
    ).strip() or instance.name
    enabled = Confirm.ask(
        "Enabled?", default=bool(current.get("enabled", True)), console=console
    )

    defaults = home_defaults(home_override)
    # The robot's template selection is preserved across reconfigures.
    # Existing robots that pre-date the template system get the
    # ``default`` template assigned -- their slot-by-slot config is
    # still respected; the template merely supplies fallbacks for
    # anything they did not declare explicitly.
    template_name = str(current.get("template") or "default")
    blueprint = _resolve_blueprint(defaults, template_name)

    bus_default = current.get("bus") or blueprint.get("bus") or {"name": "asyncio"}
    current_kernel = dict(current.get("kernel") or {})
    blueprint_kernel = dict(blueprint.get("kernel") or {"name": "base"})
    kernel_default = {**blueprint_kernel, **current_kernel}
    # Actor lives under kernel.actor; legacy top-level actor: from older
    # files is read as a migration fallback so reconfigure doesn't lose
    # the user's previous choice.
    actor_default = dict(
        kernel_default.pop("actor", None)
        or current.get("actor")
        or blueprint_kernel.get("actor")
        or {"name": "echo"}
    )
    channels_default = (
        current.get("channels") or blueprint.get("channels") or []
    )

    bus_spec = _pick_component(console, ComponentCategory.BUS, bus_default)
    kernel_spec = _pick_component(console, ComponentCategory.KERNEL, kernel_default)
    actor_spec = _pick_component(console, ComponentCategory.ACTOR, actor_default)
    channel_specs = _pick_channels(console, channels_default)

    if kernel_spec is not None and actor_spec is not None:
        kernel_spec["actor"] = actor_spec

    new_yaml: dict[str, Any] = {
        "id": instance.id,
        "name": name,
        "enabled": enabled,
        "template": template_name,
    }
    if bus_spec is not None:
        new_yaml["bus"] = bus_spec
    if kernel_spec is not None:
        new_yaml["kernel"] = kernel_spec
    if channel_specs is not None:
        new_yaml["channels"] = channel_specs

    save_robot_config(instance.home, new_yaml)
    _ensure_control_plane_token(instance.home)
    console.print(
        f"[green]updated[/] {instance.home / 'robot.yaml'}"
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _resolve_blueprint(
    defaults: dict[str, Any], template_name: str
) -> dict[str, Any]:
    """Look up ``template_name`` in ``defaults.templates`` (or fall back).

    The wizard treats the blueprint as a *display-only* helper: it
    seeds prompts with the template's slot defaults so the
    interactive flow matches what the build pipeline will produce.
    A missing template triggers a graceful fallback to an empty
    blueprint (the wizard's hard-coded fallbacks then apply).
    """
    templates = defaults.get("templates")
    if not isinstance(templates, dict):
        return {}
    blueprint = templates.get(template_name)
    if not isinstance(blueprint, dict):
        return {}
    return blueprint


def _existing_slugs(home_override: str | Path | None) -> set[str]:
    root = robots_root(home_override)
    if not root.is_dir():
        return set()
    return {p.name for p in root.iterdir() if p.is_dir()}


def _ensure_control_plane_token(robot_home: Path) -> Path:
    """Make sure ``CEPHIX_CONTROL_PLANE_TOKEN`` is in the bot-local .env.

    Existing tokens are kept (token rotation is then a deliberate
    manual step). Only generates a new value if the variable is
    missing. Returns the path to the ``.env`` file for display.
    """
    existing = load_robot_env(robot_home)
    if existing.get(CONTROL_PLANE_TOKEN_ENV):
        return robot_home / ".env"
    token = secrets.token_hex(32)
    return write_robot_env(robot_home, {CONTROL_PLANE_TOKEN_ENV: token})


def _pick_component(
    console: Console,
    category: ComponentCategory,
    default_spec: dict[str, Any],
) -> dict[str, Any] | None:
    options = list_by_category(category)
    if not options:
        console.print(f"[yellow]no components registered for category {category.value}[/]")
        return default_spec or None

    default_name = (default_spec or {}).get("name") or options[0].component_name
    label = category.value.capitalize()

    if len(options) == 1:
        chosen = options[0]
        console.print(
            f"[dim]{label}: only one option ([bold]{chosen.component_name}[/bold]) — using it.[/]"
        )
    else:
        _show_component_table(console, label, options, default_name)
        choices = [cls.component_name for cls in options]
        picked_name = Prompt.ask(
            f"{label} name",
            choices=choices,
            default=default_name if default_name in choices else choices[0],
            console=console,
        )
        chosen = next(cls for cls in options if cls.component_name == picked_name)

    spec = _ask_for_kwargs(
        console,
        chosen,
        existing=default_spec if (default_spec or {}).get("name") == chosen.component_name
        else {},
    )
    spec_with_name: dict[str, Any] = {"name": chosen.component_name}
    spec_with_name.update(spec)
    return spec_with_name


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
    name = spec.get("name") or spec.get("class") or "?"
    extras: list[str] = []
    for field in ("host", "port", "path"):
        if field in spec:
            extras.append(f"{field}={spec[field]}")
    if extras:
        return f"{name} ({', '.join(extras)})"
    return str(name)


def _show_component_table(
    console: Console,
    label: str,
    options: list[type[RobotComponent]],
    default_name: str,
) -> None:
    table = Table(title=f"Available {label}s", show_lines=False)
    table.add_column("name", style="cyan", no_wrap=True)
    table.add_column("description")
    for cls in options:
        marker = "[bold]*[/bold]" if cls.component_name == default_name else " "
        table.add_row(f"{marker} {cls.component_name}", cls.component_description or "")
    console.print(table)


def _ask_for_kwargs(
    console: Console,
    cls: type[RobotComponent],
    *,
    existing: dict[str, Any],
) -> dict[str, Any]:
    """Prompt for each user-facing parameter declared for ``cls``.

    Only parameters listed in :data:`WIZARD_ALLOWLIST` for ``cls`` are
    asked. Plumbing parameters (topics, paths, principal templates,
    ...) keep their defaults silently. A component absent from the
    allow-list falls back to "ask for every parameter" so external
    components without an explicit registration still work.
    """
    import inspect

    try:
        sig = inspect.signature(cls)
    except (ValueError, TypeError):
        return {}

    allow = WIZARD_ALLOWLIST.get(cls)

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

        prompt_text = f"  {cls.component_name}.{param_name}"
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


# ---------------------------------------------------------------------------
# Wizard allow-list registry
# ---------------------------------------------------------------------------
#
# Which constructor parameters of a component the wizard prompts for.
# This is purely a *UI hint* and lives next to the wizard, not on the
# component class -- the component contract (``RobotComponent``) stays
# focused on runtime concerns (lifecycle, identity, manifest, audit).
#
# Semantics:
#
# - A class is in the dict        -> ask only for the listed params,
#                                    everything else is plumbing and
#                                    keeps its default.
# - A class is NOT in the dict    -> "ask for every parameter" fallback
#                                    so external plugins keep working
#                                    even without registration.
# - An empty tuple ``()``         -> "ask nothing"; all defaults.
#
# External plugins register themselves via :func:`register_wizard_fields`.


WIZARD_ALLOWLIST: dict[type[RobotComponent], tuple[str, ...]] = {}


def register_wizard_fields(
    cls: type[RobotComponent],
    fields: tuple[str, ...] = (),
) -> type[RobotComponent]:
    """Declare which constructor params of ``cls`` the wizard prompts for.

    Use this from plugin code that defines its own ``RobotComponent``
    subclass and wants the wizard to expose specific user-facing
    parameters. Without registration, the wizard falls back to asking
    for *every* parameter of the class.

    Idempotent: re-registering the same class with the same fields is
    a no-op. Re-registering with different fields raises so the
    declaration stays unambiguous.
    """
    existing = WIZARD_ALLOWLIST.get(cls)
    if existing is not None and existing != fields:
        raise ValueError(
            f"wizard fields for {cls.__name__} already registered as "
            f"{existing!r}; refusing to override with {fields!r}"
        )
    WIZARD_ALLOWLIST[cls] = fields
    return cls


def _register_builtin_wizard_fields() -> None:
    """Wizard allow-lists for the components that ship with cephix."""
    from src.actor.echo import EchoActor
    from src.actor.llm.mock_actor import MockLLMActor
    from src.actor.llm.openai_actor import LLMActorOpenAI
    from src.utility.model_catalog import ModelCatalog
    from src.bus.asyncio_bus import AsyncioBus
    from src.channels.websocket import WebsocketChannel
    from src.credentials.provider import CredentialProvider
    from src.kernel.base import BaseKernel

    register_wizard_fields(AsyncioBus, ())
    register_wizard_fields(BaseKernel, ("input_topic", "output_topic", "actor_timeout"))
    register_wizard_fields(CredentialProvider, ())
    register_wizard_fields(EchoActor, ("prefix",))
    register_wizard_fields(
        LLMActorOpenAI,
        ("model_id", "api_key", "provider", "base_url", "default_system_prompt"),
    )
    register_wizard_fields(
        MockLLMActor,
        ("model_id", "provider", "default_system_prompt"),
    )
    register_wizard_fields(ModelCatalog, ())
    register_wizard_fields(WebsocketChannel, ("host", "port"))


_register_builtin_wizard_fields()
