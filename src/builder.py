"""Assemble a :class:`Robot` from a ``robot.yaml`` mapping.

The builder is the bridge between configuration and runtime. Its
backbone is a *three-stage* resolution pipeline:

1. **Template** -- if ``robot.yaml`` carries ``template: <name>``, the
   matching blueprint from ``cephix.yaml#defaults.templates`` is
   loaded as a starting point. Without ``template:`` the build starts
   from an empty mapping; the robot.yaml must declare every slot it
   wants.
2. **Slot-merge** -- the robot.yaml is merged onto the template
   slot-by-slot. ``null`` deletes a slot, a different ``name:``
   replaces a component spec wholesale (no field bleed), the same
   ``name:`` (or no ``name:``) merges fields with the instance
   winning.
3. **Library-fill** -- when a component is finally constructed, any
   missing fields are filled from
   ``cephix.yaml#defaults.components.<category>`` indexed by ``name:``.
   Explicit fields always win.

The builder also resolves credentials, runs ``${KEY}`` substitution
on the rest of the config, and hands identity, the control-plane
config and the components straight to the :class:`Robot` constructor.

Top-level ``id``, ``name``, ``enabled`` and ``template`` are not
slots -- they are robot identity / wiring metadata. The ``enabled``
flag stays out of the runtime: it's a CLI-layer filter for the
smart-default and the future ``--all`` filter.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.actor.ports import ActorPort
from src.audit.note_sink import AuditNoteSink
from src.bus.ports import BusPort
from src.channels.ports import ChannelPort
from src.components import ComponentCategory, RobotComponent
from src.configuration import (
    CONTROL_PLANE_TOKEN_ENV,
    HOME_ENV_FILENAME,
    ROBOT_ENV_FILENAME,
    ComponentLibrary,
    home_dir,
    load_robot_env,
)
from src.credentials import (
    CredentialProvider,
    CredentialProviderPort,
    EnvCredentialStore,
    ProcessEnvCredentialStore,
    resolve_secrets,
)
from src.credentials.ports import CredentialStorePort
from src.kernel.ports import KernelPort
from src.persistence.provider import JsonlPersistenceProvider, PersistenceProvider
from src.registry import ConfigError, build
from src.robot import ControlPlaneConfig, Robot, RobotIdentity
from src.telemetry.bus_recorder import BusRecorder

logger = logging.getLogger(__name__)

_DEFAULT_TELEMETRY_CHANNEL = "telemetry"
_DEFAULT_AUDIT_CHANNEL = "audit"

# Top-level keys in ``robot.yaml`` that are NOT component slots:
# they are robot-level metadata (identity, CLI filter, template
# selector). Everything else is a slot subject to template merging.
_NON_SLOT_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {"id", "name", "enabled", "template"}
)

# Singular/plural slot pairs. The robot.yaml may use either form for
# a given slot, but never both at once. The plural form always
# normalises to a list internally.
_SINGULAR_PLURAL_PAIRS: dict[str, str] = {
    "kernel": "kernels",
    "channel": "channels",
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_robot_from_config(
    robot_yaml: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
    workspace: str | Path | None = None,
) -> Robot:
    """Build a :class:`Robot` from a parsed ``robot.yaml`` mapping.

    ``defaults`` typically comes from
    :func:`src.configuration.home_defaults`. It is split into a
    ``templates:`` section (named blueprints) and a ``components:``
    section (per-name field defaults); the builder consults both.
    Legacy callers that pass a flat dict (without ``templates:``
    /``components:``) get only the lookup paths they provide.

    ``workspace`` is the bot's directory (where ``robot.yaml``
    lives). Used to locate the ``.env`` file for the control-plane
    token *and* as the default first store for the credential
    subsystem. If omitted, no ``.env`` is read and the control plane
    refuses to start (deny-by-default).

    Build order:

    1. **Template + slot-merge** produces the resolved configuration.
    2. **Credentials** are constructed from the ``credentials:``
       section (or a default chain anchored at workspace/.env and
       ``~/.cephix/.env``).
    3. The remaining configuration is run through
       :func:`~src.credentials.substitution.resolve_secrets` so every
       ``${KEY}`` reference is replaced before any component
       constructor sees it. A missing key raises
       :class:`~src.credentials.exceptions.CredentialNotFound`,
       aborting the build -- no half-constructed robot.
    4. Components are instantiated in dependency order. The actor
       lives **under** the kernel (``kernel.actor``) and is
       constructed first; the kernel receives it via constructor
       injection. Actors whose constructors declare a ``credentials``
       kwarg receive the shared provider automatically (Convention-DI).
    """
    if not isinstance(robot_yaml, dict):
        raise ConfigError("robot.yaml must be a mapping at the top level")

    defaults_block = dict(defaults or {})
    templates_block = defaults_block.get("templates")
    if templates_block is not None and not isinstance(templates_block, dict):
        raise ConfigError(
            "cephix.yaml#defaults.templates must be a mapping of "
            "template-name -> blueprint"
        )
    components_block = defaults_block.get("components")
    library = ComponentLibrary(components_block)

    # Phase 1: template + slot-merge.
    template_block = _resolve_template(robot_yaml, templates_block or {})
    cfg = _merge_slots(template_block, dict(robot_yaml))

    # Top-level guard: the legacy flat ``actor:`` slot is no longer
    # accepted. The actor lives under ``kernel.actor`` because it is
    # the kernel's runtime dependency. Catch this here so the user
    # sees a precise migration hint instead of a vague ConfigError
    # later.
    if "actor" in cfg:
        raise ConfigError(
            "robot.yaml#actor on the top level is no longer supported; "
            "move the actor block under kernel: (the actor is the "
            "kernel's runtime dependency, not a sibling). Example:\n"
            "  kernel:\n"
            "    name: base\n"
            "    actor:\n"
            "      name: echo"
        )

    # Phase 2: credentials. Constructed before the substitution pass so
    # ${KEY} references in the rest of the config can resolve.
    credentials_spec = cfg.pop("credentials", None)
    credentials = _build_credential_provider(
        credentials_spec,
        workspace=workspace,
    )

    # Phase 3: substitution. Walks every remaining section and
    # replaces ``${KEY}`` references with their resolved values.
    cfg = resolve_secrets(
        cfg,
        lambda key: credentials.resolve_sync(key, requester="builder"),
    )

    # Phase 4: assemble components.
    bus = _build_bus(cfg.get("bus"), library=library)

    utilities = _build_components_list(
        cfg.get("utility"),
        section_name="utility",
        expected_category=ComponentCategory.UTILITY,
        library=library,
    )
    bus_utilities = _build_components_list(
        cfg.get("bus_utility"),
        section_name="bus_utility",
        expected_category=ComponentCategory.BUS_UTILITY,
        library=library,
    )

    # Kernels (singular or plural form). Each kernel owns a nested
    # ``actor:`` mapping, built and injected into the kernel.
    kernel_specs = _normalize_singular_or_list(
        cfg, singular="kernel", plural="kernels"
    )
    if not kernel_specs:
        raise ConfigError(
            "robot.yaml requires a kernel (use 'kernel:' for one or "
            "'kernels:' for several)"
        )
    actors, kernels = _build_kernels(
        kernel_specs, library=library, credentials=credentials
    )

    # Channels (singular or plural form).
    channel_specs = _normalize_singular_or_list(
        cfg, singular="channel", plural="channels"
    )
    channels = _build_channels(channel_specs, library=library)

    persistence = _build_persistence_provider(
        cfg.get("persistence"), workspace, library=library
    )
    telemetry = _build_telemetry(
        cfg.get("telemetry"), persistence, library=library
    )
    audit = _build_audit(cfg.get("audit"), persistence, library=library)

    robot_id_raw = robot_yaml.get("id")
    robot_name_raw = robot_yaml.get("name")
    identity = RobotIdentity(
        id=str(robot_id_raw) if robot_id_raw else None,
        name=str(robot_name_raw) if robot_name_raw else None,
    )

    control_plane_config = _control_plane_config(
        cfg.get("control_plane"), library=library
    )
    control_plane_token: str | None = None
    if workspace is not None:
        env = load_robot_env(workspace)
        control_plane_token = env.get(CONTROL_PLANE_TOKEN_ENV) or None

    components: list[RobotComponent] = [
        bus,
        credentials,
        *actors,
        *kernels,
        *utilities,
        *bus_utilities,
        *channels,
    ]
    if telemetry is not None:
        components.append(telemetry)
    if audit is not None:
        components.append(audit)

    return Robot(
        identity=identity,
        components=components,
        control_plane_config=control_plane_config,
        control_plane_token=control_plane_token,
    )


# ---------------------------------------------------------------------------
# Stage 1: template resolution + slot-merge
# ---------------------------------------------------------------------------


def _resolve_template(
    robot_yaml: dict[str, Any], templates: dict[str, Any]
) -> dict[str, Any]:
    """Return a copy of the named template, or an empty dict.

    ``robot.yaml#template: <name>`` selects one of the blueprints in
    ``cephix.yaml#defaults.templates``. A missing ``template:`` key (or
    an explicit ``null``) means "no template layer" -- the robot.yaml
    must declare every slot it wants. An unknown template name aborts
    the build with the list of available templates so the typo is
    immediately obvious.
    """
    name = robot_yaml.get("template")
    if name is None:
        return {}
    if not isinstance(name, str) or not name:
        raise ConfigError(
            "robot.yaml#template must be a non-empty string naming a "
            "template under cephix.yaml#defaults.templates"
        )
    if name not in templates:
        available = sorted(templates.keys())
        raise ConfigError(
            f"robot.yaml#template: unknown template {name!r}; "
            f"available templates: {available or '(none defined)'}"
        )
    blueprint = templates[name]
    if not isinstance(blueprint, dict):
        raise ConfigError(
            f"defaults.templates.{name} must be a mapping of slot -> "
            "component spec"
        )
    return _deep_copy(blueprint)


def _merge_slots(
    template: dict[str, Any], instance: dict[str, Any]
) -> dict[str, Any]:
    """Slot-by-slot merge of ``instance`` onto ``template``.

    Rules per slot:

    - **Identity / wiring keys** (``id``, ``name``, ``enabled``,
      ``template``): instance wins; not subject to component-merge
      rules.
    - **Slot is ``null`` in instance**: the slot is removed entirely
      (the user opted out of that component).
    - **Slot exists in both as a dict**: descend into
      :func:`_merge_component_dict` which respects the ``name:``
      discriminator.
    - **Slot is a list / scalar in instance**: replaces the template
      value wholesale (no element-wise list merge).
    - **Slot only in template / only in instance**: copied through.
    """
    result: dict[str, Any] = {}
    for slot, value in template.items():
        result[slot] = _deep_copy(value)

    for slot, value in instance.items():
        if slot in _NON_SLOT_TOP_LEVEL_KEYS:
            result[slot] = value
            continue
        if value is None:
            result.pop(slot, None)
            continue
        if (
            slot in result
            and isinstance(result[slot], dict)
            and isinstance(value, dict)
        ):
            result[slot] = _merge_component_dict(result[slot], value)
        else:
            # list-or-scalar wholesale replace; deep-copy the instance
            # value so later mutations on the merged dict don't leak
            # back into the caller's robot.yaml.
            result[slot] = _deep_copy(value)
    return result


def _merge_component_dict(
    template_d: dict[str, Any], instance_d: dict[str, Any]
) -> dict[str, Any]:
    """Recursive merge with ``name:`` discriminator semantics.

    Used both for top-level slot dicts (``bus``, ``kernel``,
    ``persistence``, ...) and for nested component-shaped sub-dicts
    such as ``kernel.actor``.

    - Different ``name:`` values mean "two different component
      classes"; merging their fields would Frankenstein the spec
      (e.g. an :class:`LLMActorOpenAI` carrying a ``prefix`` from a
      template :class:`EchoActor`). The instance wins wholesale.
    - Same ``name:`` (or instance without ``name:``) means "same
      component, fill missing fields"; we recurse.
    """
    t_name = template_d.get("name")
    i_name = instance_d.get("name")
    if i_name is not None and t_name is not None and i_name != t_name:
        return _deep_copy(instance_d)

    merged = _deep_copy(template_d)
    for key, value in instance_d.items():
        if value is None:
            merged.pop(key, None)
            continue
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _merge_component_dict(merged[key], value)
        else:
            merged[key] = _deep_copy(value)
    return merged


def _deep_copy(value: Any) -> Any:
    """Cheap recursive copy for plain YAML structures (dict/list/scalar)."""
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Stage 2: singular/plural slot normalisation
# ---------------------------------------------------------------------------


def _normalize_singular_or_list(
    cfg: dict[str, Any], *, singular: str, plural: str
) -> list[dict[str, Any]]:
    """Read ``singular``/``plural`` from ``cfg`` and return a list.

    The two forms exist so the common single-component case stays
    readable (``kernel: {...}``) while the rare multi-component case
    is still expressible (``kernels: [{...}, {...}]``). Both forms at
    once are a configuration error -- the ambiguity ("does the list
    extend or replace the single?") is not worth the surprise.
    """
    has_singular = singular in cfg and cfg[singular] is not None
    has_plural = plural in cfg and cfg[plural] is not None
    if has_singular and has_plural:
        raise ConfigError(
            f"robot.yaml#{singular} and robot.yaml#{plural} are mutually "
            f"exclusive; pick one form (use {plural}: as a list when you "
            "need more than one)"
        )
    if has_singular:
        value = cfg[singular]
        if not isinstance(value, dict):
            raise ConfigError(
                f"robot.yaml#{singular} must be a mapping"
            )
        return [value]
    if has_plural:
        value = cfg[plural]
        if not isinstance(value, list):
            raise ConfigError(
                f"robot.yaml#{plural} must be a list of component specs"
            )
        for index, entry in enumerate(value):
            if not isinstance(entry, dict):
                raise ConfigError(
                    f"robot.yaml#{plural}[{index}] must be a mapping"
                )
        return list(value)
    return []


# ---------------------------------------------------------------------------
# Stage 3: library-aware component construction
# ---------------------------------------------------------------------------


def _build_with_library(
    spec: dict[str, Any],
    *,
    category: str,
    library: ComponentLibrary,
    **extra_kwargs: Any,
) -> RobotComponent:
    """Build a component, filling missing fields from the library.

    Library lookup is keyed by ``(category, spec['name'])``. A spec
    without ``name:`` (e.g. one using ``class:``) skips the library --
    third-party plugins reach the registry directly.
    """
    if not isinstance(spec, dict):
        raise ConfigError(
            f"{category} spec must be a mapping, got {type(spec).__name__}"
        )
    enriched = _apply_library_defaults(spec, category=category, library=library)
    return build(enriched, **extra_kwargs)


def _apply_library_defaults(
    spec: dict[str, Any],
    *,
    category: str,
    library: ComponentLibrary,
) -> dict[str, Any]:
    """Return a copy of ``spec`` with library defaults filled in.

    Explicit fields in ``spec`` always win. The ``name:`` key is what
    selects the library entry; without it we cannot look up defaults
    and pass the spec through unchanged.
    """
    name = spec.get("name")
    if not isinstance(name, str) or not name:
        return dict(spec)
    defaults_dict = library.defaults_for(category, name)
    if not defaults_dict:
        return dict(spec)
    merged = dict(defaults_dict)
    for key, value in spec.items():
        merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# Component-specific builders
# ---------------------------------------------------------------------------


def _build_bus(spec: Any, *, library: ComponentLibrary) -> BusPort:
    if spec is None:
        raise ConfigError(
            "robot.yaml requires a bus (set 'template: default' or "
            "declare 'bus:' explicitly)"
        )
    if not isinstance(spec, dict):
        raise ConfigError("robot.yaml#bus must be a mapping")
    component = _build_with_library(spec, category="bus", library=library)
    if not isinstance(component, BusPort):
        raise ConfigError(
            f"bus component {type(component).__name__} does not implement "
            "BusPort"
        )
    if not isinstance(component, RobotComponent):
        raise ConfigError(
            f"bus component {type(component).__name__} does not implement "
            "RobotComponent"
        )
    return component


def _build_kernels(
    kernel_specs: list[dict[str, Any]],
    *,
    library: ComponentLibrary,
    credentials: CredentialProviderPort | None,
) -> tuple[list[ActorPort], list[RobotComponent]]:
    """Build every kernel together with its nested actor.

    The actor lives under the kernel because it is a constructor-time
    dependency, not a peer. Both end up as separate
    :class:`RobotComponent`s in the robot's component list -- the
    runtime lifecycle treats them independently, the kernel just
    holds a reference for its act phase.
    """
    actors: list[ActorPort] = []
    kernels: list[RobotComponent] = []
    for index, raw in enumerate(kernel_specs):
        kspec = dict(raw)
        actor_spec = kspec.pop("actor", None)
        if not actor_spec:
            label = "robot.yaml#kernel.actor" if len(kernel_specs) == 1 else (
                f"robot.yaml#kernels[{index}].actor"
            )
            raise ConfigError(
                f"{label} is required -- the kernel needs an actor "
                "to consult during its act phase"
            )
        if not isinstance(actor_spec, dict):
            raise ConfigError(
                "kernel.actor must be a mapping (component spec)"
            )

        actor = _build_actor(
            actor_spec, library=library, credentials=credentials
        )
        if not isinstance(actor, ActorPort):
            raise ConfigError(
                f"actor component {type(actor).__name__} does not "
                "implement ActorPort"
            )
        if not isinstance(actor, RobotComponent):
            raise ConfigError(
                f"actor component {type(actor).__name__} does not "
                "implement RobotComponent"
            )

        kernel = _build_with_library(
            kspec, category="kernel", library=library, actor=actor
        )
        if not isinstance(kernel, KernelPort):
            raise ConfigError(
                f"kernel component {type(kernel).__name__} does not "
                "implement KernelPort"
            )
        if not isinstance(kernel, RobotComponent):
            raise ConfigError(
                f"kernel component {type(kernel).__name__} does not "
                "implement RobotComponent"
            )
        actors.append(actor)
        kernels.append(kernel)
    return actors, kernels


def _build_actor(
    actor_spec: dict[str, Any],
    *,
    library: ComponentLibrary,
    credentials: CredentialProviderPort | None,
) -> ActorPort:
    """Build an actor with library-fill and Convention-DI.

    Any actor whose constructor declares a ``credentials`` keyword
    gets the shared :class:`CredentialProviderPort`; actors that
    don't declare it (e.g. :class:`~src.actor.echo.EchoActor`) are
    untouched. An explicit value in the spec wins over the
    auto-injection.

    Note: the model catalog is *not* injected into actors. Actors
    are drivers -- they report token counts; the future ``LLMKernel``
    consults the catalog to compute cost.
    """
    import inspect

    enriched = _apply_library_defaults(
        actor_spec, category="actor", library=library
    )

    cls = _resolve_actor_class(enriched)
    try:
        sig = inspect.signature(cls)
    except (ValueError, TypeError):
        sig = None

    extras: dict[str, Any] = {}
    if (
        credentials is not None
        and "credentials" not in enriched
        and sig is not None
        and "credentials" in sig.parameters
    ):
        extras["credentials"] = credentials

    return build(enriched, **extras)


def _resolve_actor_class(spec: dict[str, Any]) -> type:
    """Peek the registered/imported class without consuming the spec."""
    from src.registry import _import_class, get  # type: ignore[attr-defined]

    if "class" in spec:
        cls_path = spec["class"]
        if not isinstance(cls_path, str):
            raise ConfigError(
                "actor.class must be a dotted path string"
            )
        return _import_class(cls_path)
    if "name" in spec:
        name = spec["name"]
        if not isinstance(name, str):
            raise ConfigError("actor.name must be a string")
        return get(name)
    raise ConfigError(
        "actor needs either a 'name' or a 'class' key"
    )


def _build_channels(
    channel_specs: list[dict[str, Any]],
    *,
    library: ComponentLibrary,
) -> list[ChannelPort]:
    out: list[ChannelPort] = []
    for index, spec in enumerate(channel_specs):
        component = _build_with_library(
            spec, category="channel", library=library
        )
        if not isinstance(component, ChannelPort):
            raise ConfigError(
                f"channel #{index} ({type(component).__name__}) does "
                "not implement ChannelPort"
            )
        if not isinstance(component, RobotComponent):
            raise ConfigError(
                f"channel #{index} ({type(component).__name__}) does "
                "not implement RobotComponent"
            )
        out.append(component)
    return out


def _build_components_list(
    spec: Any,
    *,
    section_name: str,
    expected_category: ComponentCategory,
    library: ComponentLibrary,
) -> list[RobotComponent]:
    """Build a list of components from a category-keyed YAML section.

    Used for ``utility:`` and ``bus_utility:``. Each entry is a
    regular component spec (resolved via :func:`build` with library
    defaults). The builder verifies the resulting component's
    category matches the section so a misconfigured channel can't
    sneak into the utility list and vice versa.
    """
    if spec is None:
        return []
    if not isinstance(spec, list):
        raise ConfigError(
            f"robot.yaml#{section_name} must be a list of component specs"
        )
    out: list[RobotComponent] = []
    for index, item in enumerate(spec):
        if not isinstance(item, dict):
            raise ConfigError(
                f"robot.yaml#{section_name}[{index}] must be a mapping"
            )
        component = _build_with_library(
            item, category=section_name, library=library
        )
        if not isinstance(component, RobotComponent):
            raise ConfigError(
                f"{section_name}#{index} ({type(component).__name__}) "
                f"does not implement RobotComponent"
            )
        if component.component_category is not expected_category:
            raise ConfigError(
                f"{section_name}#{index} resolved to "
                f"{type(component).__name__} with category "
                f"{component.component_category.value}; expected "
                f"{expected_category.value}"
            )
        out.append(component)
    return out


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def _build_credential_provider(
    spec: Any,
    *,
    workspace: str | Path | None,
) -> CredentialProvider:
    """Build the :class:`CredentialProvider` and its store chain.

    Two cases:

    - **No ``credentials:`` section** -- the default chain is
      ``robot-workspace/.env`` (if a workspace is provided),
      ``~/.cephix/.env`` (if it exists) and ``process-env``. This
      preserves the pre-credentials behaviour of cephix where a
      bot-local ``.env`` was implicitly read.
    - **An explicit ``credentials:`` section** -- the user lists
      stores in resolution order. Each entry is a mapping with at
      least ``type:``; supported types today are ``env`` (with a
      ``path:``) and ``process-env``.

    The provider is registered as a :class:`RobotComponent` and will
    be started by the robot during the ``BUS_UTILITY`` boot phase.
    The builder uses it synchronously *now*, before the robot
    lifecycle even runs, so substitution can fail loud and early.
    """
    stores: list[CredentialStorePort] = []
    if spec is None:
        stores.extend(_default_credential_stores(workspace))
    else:
        if not isinstance(spec, dict):
            raise ConfigError(
                "robot.yaml#credentials must be a mapping with a "
                "'stores' list"
            )
        store_specs = spec.get("stores")
        if store_specs is None:
            stores.extend(_default_credential_stores(workspace))
        else:
            if not isinstance(store_specs, list):
                raise ConfigError(
                    "robot.yaml#credentials.stores must be a list"
                )
            for index, store_spec in enumerate(store_specs):
                stores.append(
                    _build_credential_store(
                        store_spec, index=index, workspace=workspace
                    )
                )
    return CredentialProvider(stores=stores)


def _default_credential_stores(
    workspace: str | Path | None,
) -> list[CredentialStorePort]:
    """The default store chain when no ``credentials:`` section exists.

    Order: bot-local ``.env`` -> global ``~/.cephix/.env`` ->
    ``os.environ``. Missing files are tolerated; the global home is
    created on demand by :func:`home_dir` so the global store always
    has a path even if the file is empty.
    """
    out: list[CredentialStorePort] = []
    if workspace is not None:
        out.append(
            EnvCredentialStore(
                Path(workspace) / ROBOT_ENV_FILENAME,
                name="env:robot",
            )
        )
    out.append(
        EnvCredentialStore(
            home_dir() / HOME_ENV_FILENAME,
            name="env:cephix-home",
        )
    )
    out.append(ProcessEnvCredentialStore())
    return out


def _build_credential_store(
    spec: Any,
    *,
    index: int,
    workspace: str | Path | None,
) -> CredentialStorePort:
    """Build one :class:`CredentialStorePort` from a YAML entry."""
    if not isinstance(spec, dict):
        raise ConfigError(
            f"robot.yaml#credentials.stores[{index}] must be a mapping"
        )
    spec = dict(spec)
    kind = spec.pop("type", None)
    if not isinstance(kind, str):
        raise ConfigError(
            f"robot.yaml#credentials.stores[{index}] needs a 'type' string"
        )
    name_override = spec.pop("name", None)

    if kind == "env":
        path_raw = spec.pop("path", None)
        if not isinstance(path_raw, str) or not path_raw:
            raise ConfigError(
                f"credentials.stores[{index}] (env) requires a non-empty "
                "'path'"
            )
        path = Path(path_raw).expanduser()
        if not path.is_absolute() and workspace is not None:
            path = Path(workspace) / path
        if spec:
            raise ConfigError(
                f"credentials.stores[{index}] (env): unknown keys "
                f"{sorted(spec)}"
            )
        return EnvCredentialStore(path, name=name_override)

    if kind == "process-env":
        snapshot = bool(spec.pop("snapshot", True))
        if spec:
            raise ConfigError(
                f"credentials.stores[{index}] (process-env): unknown "
                f"keys {sorted(spec)}"
            )
        return ProcessEnvCredentialStore(
            snapshot=snapshot,
            name=name_override or "process-env",
        )

    raise ConfigError(
        f"credentials.stores[{index}] has unknown type {kind!r}; "
        "supported types: env, process-env"
    )


# ---------------------------------------------------------------------------
# Observers (persistence, telemetry, audit) and the control plane
# ---------------------------------------------------------------------------


def _build_persistence_provider(
    spec: Any,
    workspace: str | Path | None,
    *,
    library: ComponentLibrary,
) -> PersistenceProvider | None:
    """Build the robot-wide :class:`PersistenceProvider`.

    Returns ``None`` when no spec is present (the slot was deleted /
    never declared) or when no usable root can be derived (no
    explicit path *and* no workspace). Components that depend on the
    provider take ``None`` as "no persistence configured" and skip
    themselves with an info log -- the boot succeeds either way.

    Library defaults under ``components.persistence[name=jsonl]``
    fill missing fields (today: ``path: logs``).
    """
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise ConfigError("robot.yaml#persistence must be a mapping")

    enriched = _apply_library_defaults(
        spec, category="persistence", library=library
    )

    name = str(enriched.get("name", "jsonl"))
    if name != "jsonl":
        raise ConfigError(
            f"unknown persistence backend {name!r}; "
            "the only built-in persistence backend is 'jsonl'"
        )

    raw_path = enriched.get("path", "logs")
    candidate = Path(str(raw_path)).expanduser()
    if not candidate.is_absolute():
        if workspace is None:
            return None
        candidate = Path(workspace) / candidate

    return JsonlPersistenceProvider(candidate)


def _build_telemetry(
    spec: Any,
    persistence: PersistenceProvider | None,
    *,
    library: ComponentLibrary,
) -> BusRecorder | None:
    """Build the telemetry component from ``robot.yaml#telemetry``.

    Returns ``None`` when the slot is absent or no persistence is
    available -- without a sink, the recorder is a passive observer
    with side effects and nothing more.
    """
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise ConfigError("robot.yaml#telemetry must be a mapping")

    enriched = _apply_library_defaults(
        spec, category="telemetry", library=library
    )

    name = str(enriched.get("name", "bus_recorder"))
    if name != "bus_recorder":
        raise ConfigError(
            f"unknown telemetry component {name!r}; "
            "the only built-in telemetry component is 'bus_recorder'"
        )

    if persistence is None:
        logger.info(
            "telemetry configured but no persistence available; "
            "skipping BusRecorder"
        )
        return None

    channel = str(enriched.get("channel", _DEFAULT_TELEMETRY_CHANNEL))
    return BusRecorder(sink=persistence.open(channel))


def _build_audit(
    spec: Any,
    persistence: PersistenceProvider | None,
    *,
    library: ComponentLibrary,
) -> AuditNoteSink | None:
    """Build the audit component from ``robot.yaml#audit``."""
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise ConfigError("robot.yaml#audit must be a mapping")

    enriched = _apply_library_defaults(
        spec, category="audit", library=library
    )

    name = str(enriched.get("name", "audit_note_sink"))
    if name != "audit_note_sink":
        raise ConfigError(
            f"unknown audit component {name!r}; "
            "the only built-in audit component is 'audit_note_sink'"
        )

    if persistence is None:
        logger.info(
            "audit configured but no persistence available; "
            "skipping AuditNoteSink"
        )
        return None

    channel = str(enriched.get("channel", _DEFAULT_AUDIT_CHANNEL))
    return AuditNoteSink(sink=persistence.open(channel))


def _control_plane_config(
    spec: Any, *, library: ComponentLibrary
) -> ControlPlaneConfig:
    """Translate a ``control_plane:`` YAML block into a config dataclass.

    A missing slot returns the all-defaults
    :class:`ControlPlaneConfig` (enabled, 127.0.0.1:9876). Library
    defaults under ``components.control_plane[name=control_plane]``
    fill missing fields.
    """
    if spec is None:
        return ControlPlaneConfig()
    if not isinstance(spec, dict):
        raise ConfigError("robot.yaml#control_plane must be a mapping")

    enriched = _apply_library_defaults(
        spec, category="control_plane", library=library
    )

    name = enriched.get("name", "control_plane")
    if name != "control_plane":
        raise ConfigError(
            f"unknown control_plane component {name!r}; "
            "the only built-in control_plane component is "
            "'control_plane'"
        )

    enabled = bool(enriched.get("enabled", True))
    host = str(enriched.get("host", "127.0.0.1"))
    port = int(enriched.get("port", 9876))
    path = str(enriched.get("path", "/control"))

    raw_range = enriched.get("port_range") or [9876, 9999]
    if (
        not isinstance(raw_range, (list, tuple))
        or len(raw_range) != 2
        or not all(isinstance(v, int) for v in raw_range)
    ):
        raise ConfigError(
            "control_plane.port_range must be a list of two integers"
        )
    low, high = int(raw_range[0]), int(raw_range[1])
    if low > high:
        raise ConfigError("control_plane.port_range: low must be <= high")

    return ControlPlaneConfig(
        host=host,
        port=port,
        port_range=(low, high),
        path=path,
        enabled=enabled,
    )
