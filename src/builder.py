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
import shutil
from collections.abc import Sequence
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
from src.credentials.exceptions import CredentialNotFound
from src.credentials.ports import CredentialStorePort
from src.kernel.ports import KernelPort
from src.persistence import (
    EventStreamProviderPort,
    FilesystemConnection,
    FilesystemEventStreamProvider,
    LocalFSAdapter,
)
from src.registry import ConfigError, build
from src.robot import ControlPlaneConfig, Robot, RobotIdentity
from src.telemetry.bus_recorder import BusRecorder

logger = logging.getLogger(__name__)

_DEFAULT_TELEMETRY_CHANNEL = "telemetry"
_DEFAULT_AUDIT_CHANNEL = "audit"

# Packaged firmware templates that ship with cephix. Copied into the
# robot workspace on a per-file copy-if-missing basis whenever a
# ``firmware-store`` utility is built. Lives next to ``defaults.yaml``
# under :mod:`src` so editable installs and wheels both find it.
_PACKAGED_FIRMWARE_DIR: Path = Path(__file__).with_name("firmware")

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

    # Phase 2: builder-side credentials. The same ``credentials:``
    # spec is materialised twice with intentionally separate
    # lifetimes:
    #
    # - The *builder* set lives only here, in this function, as
    #   plain value objects. It feeds the ${KEY} substitution pass
    #   below and is then garbage-collected. No lifecycle, no audit.
    # - The *robot* set is built further down, ends up in
    #   ``robot.components`` as :class:`RobotComponent` instances at
    #   boot level 3 (UTILITY), and is injected into the runtime
    #   :class:`CredentialProvider`. That set is what answers
    #   ``resolve()`` from bus components during operation.
    #
    # Sharing one instance across both phases would conflate two
    # lifetimes (build-time substitution vs robot runtime) into one
    # object, which is exactly the mix we want to dissolve.
    credentials_spec = cfg.pop("credentials", None)
    builder_credentials = _build_credential_stores(
        credentials_spec, workspace=workspace
    )

    # Phase 3: substitution. Walks every remaining section and
    # replaces ``${KEY}`` references with their resolved values
    # against the builder-side store chain.
    cfg = resolve_secrets(
        cfg,
        lambda key: _resolve_via_stores(builder_credentials, key),
    )

    # The builder-side stores are no longer needed; the substitution
    # pass is complete. Robot-side stores are built fresh below so
    # the runtime resolve path is independent from the build path.
    del builder_credentials

    # Phase 4: robot-side credentials. Same ``credentials:`` spec
    # materialised again -- but this time as :class:`RobotComponent`
    # instances at UTILITY level. They appear in the boot log,
    # ``start()`` logs the key count, and ``stop()`` clears the cache.
    # The :class:`CredentialProvider` is built next and holds them as
    # the runtime resolve chain.
    robot_credential_stores = _build_credential_stores(
        credentials_spec, workspace=workspace
    )
    credentials = CredentialProvider(stores=robot_credential_stores)

    # Phase 4: assemble components.
    bus = _build_bus(cfg.get("bus"), library=library)

    # Persistence is built FIRST so utilities that need a
    # ``FilesystemConnection`` (the session store today) can be
    # wired against the same connection observers use. The boot
    # order is still BACKEND -> CONNECTION -> PROVIDER -> UTILITY,
    # set by ``BOOT_PRIORITY`` -- construction order in the builder
    # only governs DI wiring.
    (
        persistence_components,
        persistence_index,
        connection_index,
    ) = _build_persistence_components(
        cfg.get("persistence"), workspace, library=library
    )

    utilities = _build_components_list(
        cfg.get("utility"),
        section_name="utility",
        expected_category=ComponentCategory.UTILITY,
        library=library,
        workspace=workspace,
        connections=connection_index,
    )
    bus_utilities = _build_components_list(
        cfg.get("bus_utility"),
        section_name="bus_utility",
        expected_category=ComponentCategory.BUS_UTILITY,
        library=library,
        workspace=workspace,
        connections=connection_index,
    )
    bus_providers = _build_components_list(
        cfg.get("bus_provider"),
        section_name="bus_provider",
        expected_category=ComponentCategory.BUS_PROVIDER,
        library=library,
        workspace=workspace,
        connections=connection_index,
    )

    # Index utilities by ``component_name`` so the kernel builder can
    # inject the right instance by convention without the YAML having
    # to repeat references. Bus providers (tool execution layers,
    # future federated bus bridges) are indexed the same way so
    # channels and kernels can pick them up via Convention-DI.
    utility_index = _index_components_by_name(
        utilities + bus_utilities + bus_providers
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
        kernel_specs,
        library=library,
        credentials=credentials,
        utilities=utility_index,
    )

    # Channels (singular or plural form).
    channel_specs = _normalize_singular_or_list(
        cfg, singular="channel", plural="channels"
    )
    channels = _build_channels(
        channel_specs, library=library, utilities=utility_index
    )
    telemetry_components = _build_observer_components(
        cfg.get("telemetry"),
        slot="telemetry",
        expected_category=ComponentCategory.TELEMETRY,
        default_name="bus_recorder",
        default_channel=_DEFAULT_TELEMETRY_CHANNEL,
        sink_bound_names={"bus_recorder": BusRecorder},
        persistence=persistence_index,
        library=library,
    )
    audit_components = _build_observer_components(
        cfg.get("audit"),
        slot="audit",
        expected_category=ComponentCategory.AUDIT,
        default_name="audit_note_sink",
        default_channel=_DEFAULT_AUDIT_CHANNEL,
        sink_bound_names={"audit_note_sink": AuditNoteSink},
        persistence=persistence_index,
        library=library,
    )

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

    # Robot-side credential stores live in the components list so
    # their lifecycle (UTILITY level, boot 3) is visible in the boot
    # log and they get attached to the bus exactly like every other
    # utility. The provider follows at BUS_UTILITY level.
    components: list[RobotComponent] = [
        bus,
        *robot_credential_stores,
        credentials,
        *persistence_components,
        *actors,
        *kernels,
        *utilities,
        *bus_utilities,
        *bus_providers,
        *channels,
        *telemetry_components,
        *audit_components,
    ]

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
    utilities: dict[str, RobotComponent] | None = None,
) -> tuple[list[ActorPort], list[RobotComponent]]:
    """Build every kernel together with its nested actor.

    The actor lives under the kernel because it is a constructor-time
    dependency, not a peer. Both end up as separate
    :class:`RobotComponent`s in the robot's component list -- the
    runtime lifecycle treats them independently, the kernel just
    holds a reference for its act phase.

    Convention-DI: a kernel constructor that declares any of
    ``firmware``, ``sessions``, ``model_catalog`` automatically
    receives the matching utility instance from ``utilities``
    (indexed by ``component_name``). An explicit value in the YAML
    wins; a missing utility raises a precise ``ConfigError`` instead
    of a vague ``TypeError`` deep inside the constructor.
    """
    import inspect

    actors: list[ActorPort] = []
    kernels: list[RobotComponent] = []
    utilities = utilities or {}
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

        # Convention-DI for kernels that consume off-bus utilities.
        # Map kernel-constructor keyword -> required utility
        # ``component_name``.
        kernel_utility_conventions: dict[str, str] = {
            "firmware": "firmware-store",
            "sessions": "session-store",
            "model_catalog": "model-catalog",
        }
        cls = _resolve_kernel_class(kspec)
        try:
            sig = inspect.signature(cls)
        except (ValueError, TypeError):
            sig = None
        kernel_extras: dict[str, Any] = {"actor": actor}
        if sig is not None:
            for kwarg, util_name in kernel_utility_conventions.items():
                if kwarg in kspec:
                    continue
                if kwarg not in sig.parameters:
                    continue
                utility = utilities.get(util_name)
                if utility is None:
                    label = (
                        "robot.yaml#kernel"
                        if len(kernel_specs) == 1
                        else f"robot.yaml#kernels[{index}]"
                    )
                    raise ConfigError(
                        f"{label} ({cls.__name__}) requires a "
                        f"{util_name!r} utility for its "
                        f"{kwarg!r} parameter; add it to the "
                        "robot.yaml 'utility:' list (or the template's)."
                    )
                kernel_extras[kwarg] = utility

        kernel = _build_with_library(
            kspec, category="kernel", library=library, **kernel_extras
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


def _resolve_kernel_class(spec: dict[str, Any]) -> type:
    """Peek the registered/imported kernel class for Convention-DI.

    Mirrors :func:`_resolve_actor_class`; we need the class object
    before construction so we can inspect its constructor signature
    and decide which utilities to inject.
    """
    from src.registry import _import_class, get  # type: ignore[attr-defined]

    if "class" in spec:
        cls_path = spec["class"]
        if not isinstance(cls_path, str):
            raise ConfigError("kernel.class must be a dotted path string")
        return _import_class(cls_path)
    if "name" in spec:
        name = spec["name"]
        if not isinstance(name, str):
            raise ConfigError("kernel.name must be a string")
        return get(name)
    raise ConfigError("kernel needs either a 'name' or a 'class' key")


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
    utilities: dict[str, RobotComponent] | None = None,
) -> list[ChannelPort]:
    """Build channels from the ``channels:`` YAML section.

    Convention-DI:

    - ``heartbeat`` -> ``config_store`` (the indexed instance whose
      ``component_name == 'config-store'``). The heartbeat needs a
      config store to load its schedule list from; rather than make
      the operator repeat ``config_store: config-store`` in the
      robot.yaml, the builder resolves it from the already-built
      utility index. Missing config-store is a hard fail with a
      clear message so the misconfiguration is caught at boot,
      not at the first tick.
    """
    utility_index = utilities or {}
    out: list[ChannelPort] = []
    for index, spec in enumerate(channel_specs):
        if not isinstance(spec, dict):
            raise ConfigError(
                f"channel #{index} must be a mapping"
            )
        spec = dict(spec)
        extras: dict[str, Any] = {}
        name = spec.get("name")
        if name == "heartbeat" and "config_store" not in spec:
            config_store = utility_index.get("config-store")
            if config_store is None:
                raise ConfigError(
                    "channel 'heartbeat' needs a config-store utility "
                    "but none is configured; add `{name: config-store}` "
                    "to the robot's utility list (or inherit a template "
                    "that does)."
                )
            extras["config_store"] = config_store
        component = _build_with_library(
            spec, category="channel", library=library, **extras
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


_CONNECTION_AUTO_DI: dict[str, tuple[str, bool]] = {
    # name -> (constructor kwarg, required)
    "session-store": ("connection", True),
    "firmware-store": ("connection", True),
    "config-store": ("connection", True),
    # tool-execution layer takes the default persistence connection
    # to back its MCS filesystem adapter; without a persistence stack
    # the layer simply boots without the filesystem tools.
    "tool-execution": ("filesystem_connection", False),
}


def _build_components_list(
    spec: Any,
    *,
    section_name: str,
    expected_category: ComponentCategory,
    library: ComponentLibrary,
    workspace: str | Path | None = None,
    connections: dict[str, FilesystemConnection] | None = None,
) -> list[RobotComponent]:
    """Build a list of components from a category-keyed YAML section.

    Used for ``utility:`` and ``bus_utility:``. Each entry is a
    regular component spec (resolved via :func:`build` with library
    defaults). The builder verifies the resulting component's
    category matches the section so a misconfigured channel can't
    sneak into the utility list and vice versa.

    Convention-DI for components that take a
    :class:`FilesystemConnection` is driven by
    :data:`_CONNECTION_AUTO_DI` (name -> (kwarg, required)). The
    persistence-stack connection is auto-injected when the spec
    leaves the kwarg blank. ``required=True`` (stores) makes a
    missing persistence stack a build error; ``required=False``
    (the tool-execution layer) leaves the kwarg unset so the
    constructor's optional path is taken.

    Special case: ``firmware-store`` also gets its starter
    templates seeded into the resolved directory on first build
    (copy-if-missing).
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
        item = dict(item)
        extras: dict[str, Any] = {}
        name = item.get("name")
        if isinstance(name, str) and name in _CONNECTION_AUTO_DI:
            kwarg, required = _CONNECTION_AUTO_DI[name]
            if kwarg in item:
                connection = item[kwarg]
            else:
                connection = _resolve_persistence_connection(
                    name, item, connections
                )
                if connection is None:
                    if required:
                        raise ConfigError(
                            f"{name} needs a FilesystemConnection but no "
                            "persistence stack is configured; declare a "
                            "persistence entry (e.g. 'filesystem-events') "
                            "or pass an explicit connection: <id>."
                        )
                else:
                    extras[kwarg] = connection
            # ``persistence:`` is a routing hint for the builder,
            # not a kwarg the store understands -- drop it before
            # the spec walks into ``build()``.
            item.pop("persistence", None)
            # firmware-store also needs its starter templates seeded
            # into the resolved directory on first build (copy-if-
            # missing). Anchor the seed at the connection root + the
            # store's ``directory`` field so the layout matches what
            # the store will read at startup.
            if name == "firmware-store" and connection is not None:
                directory = str(item.get("directory", "firmware"))
                seed_dir = Path(connection.root) / directory if directory else Path(connection.root)
                _seed_firmware(seed_dir)
        component = _build_with_library(
            item, category=section_name, library=library, **extras
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


def _resolve_persistence_connection(
    consumer_name: str,
    spec: dict[str, Any],
    connections: dict[str, FilesystemConnection] | None,
) -> FilesystemConnection | None:
    """Pick the :class:`FilesystemConnection` a utility binds to.

    Selection (mirrors observer ``persistence:`` semantics):

    - explicit ``persistence: <id>`` on the spec -> exact lookup;
    - no field set and exactly one persistence stack -> that one;
    - no field set, multiple stacks, ``"default"`` present -> that;
    - everything else -> ``None`` (caller surfaces the ConfigError).

    Reusing the ``persistence: <id>`` key (and not inventing a
    parallel ``connection: <id>``) keeps the YAML grammar uniform
    with telemetry/audit entries -- one knob, one meaning.
    """
    if not connections:
        return None
    requested = spec.get("persistence")
    if requested is not None:
        requested_id = str(requested)
        connection = connections.get(requested_id)
        if connection is None:
            available = sorted(connections.keys()) or ["<none>"]
            raise ConfigError(
                f"{consumer_name} references persistence id "
                f"{requested_id!r} but no such stack is configured; "
                f"available: {available}"
            )
        return connection
    if len(connections) == 1:
        return next(iter(connections.values()))
    if _DEFAULT_PERSISTENCE_ID in connections:
        return connections[_DEFAULT_PERSISTENCE_ID]
    available = sorted(connections.keys())
    raise ConfigError(
        f"{consumer_name} needs a persistence id (multiple stacks "
        f"available: {available}); add 'persistence: <id>' to the "
        f"{consumer_name} entry."
    )


def _index_components_by_name(
    components: list[RobotComponent],
) -> dict[str, RobotComponent]:
    """Map ``component_name`` -> instance.

    Used to look up utilities by convention name when a kernel asks
    for one via Convention-DI (e.g. ``ChatKernel`` wanting
    ``firmware`` -> ``firmware-store``). If the same name appears
    more than once (multi-instance utilities), the last entry wins
    -- multi-instance utility wiring would need an explicit
    ``utilities:`` mapping per-kernel, which is out of scope here.
    """
    out: dict[str, RobotComponent] = {}
    for component in components:
        name = getattr(component, "component_name", None)
        if isinstance(name, str) and name:
            out[name] = component
    return out


def _seed_firmware(workspace_firmware_dir: Path) -> None:
    """Copy packaged firmware templates into the workspace per file.

    Only ever ``copy-if-missing``: a file the user has edited or
    deleted stays whatever the user made of it. Runs on every build
    so a brand-new robot ends up with the starter set, and a
    previously-pruned set has the missing files re-seeded next time
    around. The packaged template directory ships next to
    ``defaults.yaml`` under :mod:`src`.
    """
    if not _PACKAGED_FIRMWARE_DIR.exists():
        return
    workspace_firmware_dir.mkdir(parents=True, exist_ok=True)
    for src_file in sorted(_PACKAGED_FIRMWARE_DIR.glob("*.md")):
        dst = workspace_firmware_dir / src_file.name
        if not dst.exists():
            shutil.copyfile(src_file, dst)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def _build_credential_stores(
    spec: Any,
    *,
    workspace: str | Path | None,
) -> list[CredentialStorePort]:
    """Build the credential store chain from a ``credentials:`` section.

    Two cases:

    - **No ``credentials:`` section** -- the default chain is
      ``robot-workspace/.env`` (if a workspace is provided),
      ``~/.cephix/.env`` (if it exists) and ``process-env``.
    - **An explicit ``credentials:`` section** -- the user lists
      stores in resolution order. Each entry is a mapping with at
      least ``type:``; supported types today are ``env`` (with a
      ``path:``) and ``process-env``.

    Returns a *fresh* list of stores. The builder calls this twice
    intentionally:

    - Once for its own ${KEY}-substitution pass (ephemeral plain
      use; the instances never see ``start()``).
    - Once for the robot, where the stores live in the components
      list, boot at UTILITY level, and get injected into the
      :class:`CredentialProvider`.

    The two sets are independent instances of the same classes
    pointing at the same .env paths -- the builder discards its
    set after substitution, the robot owns its set for the run's
    lifetime.
    """
    stores: list[CredentialStorePort] = []
    if spec is None:
        stores.extend(_default_credential_stores(workspace))
        return stores
    if not isinstance(spec, dict):
        raise ConfigError(
            "robot.yaml#credentials must be a mapping with a "
            "'stores' list"
        )
    store_specs = spec.get("stores")
    if store_specs is None:
        stores.extend(_default_credential_stores(workspace))
        return stores
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
    return stores


def _resolve_via_stores(
    stores: Sequence[CredentialStorePort], key: str
) -> str:
    """Walk a store chain and return the first hit; raise otherwise.

    Used by the builder's substitution pass against a *plain*
    store chain. We don't go through :class:`CredentialProvider`
    here because the provider is for runtime audit emission --
    builder substitution is pre-lifecycle and has no bus to audit
    against.
    """
    for store in stores:
        value = store.lookup(key)
        if value is not None:
            return value
    raise CredentialNotFound(
        key,
        stores_tried=tuple(s.name for s in stores),
        requester="builder",
    )


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


_DEFAULT_PERSISTENCE_ID = "default"


def _build_persistence_components(
    spec: Any,
    workspace: str | Path | None,
    *,
    library: ComponentLibrary,
) -> tuple[
    list[RobotComponent],
    dict[str, EventStreamProviderPort],
    dict[str, FilesystemConnection],
]:
    """Build the persistence stack from ``robot.yaml#persistence``.

    Each persistence entry is synthesized into the three-level DAO
    stack: a backend adapter (level 0), a connection (level 1) and
    a provider (level 2). All three land in :data:`robot.components`
    and the robot's boot order surfaces them as separate ``Boot
    Level X`` markers with their own ``injected into`` log lines.

    Returns ``(components, provider_index, connection_index)``:

    - ``components`` -- the full list of synthesized
      :class:`RobotComponent`s across all configured persistence
      entries, in any order. The robot resorts them by
      :data:`src.components.BOOT_PRIORITY`.
    - ``provider_index`` -- mapping ``id -> provider`` so observers
      (telemetry, audit) can reference a specific stack via
      ``persistence: <id>``.
    - ``connection_index`` -- mapping ``id -> connection`` so
      utilities that need raw filesystem IO (e.g. the
      :class:`~src.utility.session_store.store.FilesystemSessionStore`)
      can be wired against the same connection the observers above
      use.

    Shapes accepted: singular mapping or list. A singular spec
    implicitly gets ``id: "default"``. A list may later mix backends
    (only ``filesystem-events`` ships today; future entries might be
    ``database-events``, ``object-store-events``, or a composite).

    Path semantics:

    - ``path:`` (default: the workspace) -- root of the
      :class:`FilesystemConnection`. Every utility wired to this
      connection (events, sessions, ...) sees paths relative to it.
    - ``directory:`` (default: ``logs``) -- the provider's bucket
      *inside* that root. Telemetry / audit channels land under
      ``<root>/<directory>/``; a session store using the same
      connection keeps its own bucket (``sessions/``).

    Library defaults under ``components.persistence[name=<name>]``
    fill missing fields.
    """
    entries = _normalize_observer_specs(spec, slot="persistence")
    all_components: list[RobotComponent] = []
    provider_index: dict[str, EventStreamProviderPort] = {}
    connection_index: dict[str, FilesystemConnection] = {}
    used_ids: set[str] = set()
    for index, entry in enumerate(entries):
        enriched = _apply_library_defaults(
            entry, category="persistence", library=library
        )

        name = str(enriched.get("name", "filesystem-events"))
        if name != "filesystem-events":
            raise ConfigError(
                f"unknown persistence provider {name!r}; the only "
                "built-in provider today is 'filesystem-events' "
                "(filesystem-backed event-stream provider)"
            )

        # ``id`` discriminator: explicit if provided, else "default"
        # for a single entry, else the entry's index as a string. We
        # reject duplicates so observers can rely on the mapping.
        explicit_id = enriched.get("id")
        if explicit_id is not None:
            persistence_id = str(explicit_id)
        elif len(entries) == 1:
            persistence_id = _DEFAULT_PERSISTENCE_ID
        else:
            persistence_id = str(index)
        if persistence_id in used_ids:
            raise ConfigError(
                f"persistence[{index}] re-uses id {persistence_id!r}; "
                "each persistence entry needs a unique id when more "
                "than one is declared"
            )
        used_ids.add(persistence_id)

        # Resolve connection root. ``path:`` is the user override --
        # relative paths anchor at the workspace, absolute paths
        # land as-is. Default = workspace (so the connection is
        # shared between events and the session store, each with
        # its own bucket below). Without a workspace and no absolute
        # ``path:``, the entry is silently dropped so a
        # workspace-less robot still boots.
        raw_path = enriched.get("path")
        if raw_path is None:
            if workspace is None:
                continue
            root = Path(workspace)
        else:
            candidate = Path(str(raw_path)).expanduser()
            if candidate.is_absolute():
                root = candidate
            else:
                if workspace is None:
                    continue
                root = Path(workspace) / candidate

        # Adapter (BACKEND) -- ``adapter:`` may name a future
        # ``s3-fs`` etc.; today only the local FS adapter ships.
        adapter_name = str(enriched.get("adapter", "local-fs"))
        if adapter_name != "local-fs":
            raise ConfigError(
                f"unknown filesystem adapter {adapter_name!r}; the "
                "only built-in adapter today is 'local-fs'"
            )
        adapter = LocalFSAdapter()

        # Connection (CONNECTION) -- holds the adapter + root.
        connection = FilesystemConnection(adapter=adapter, root=root)

        # Provider (PROVIDER) -- the DAO observers depend on. Its
        # ``directory:`` is the provider's bucket inside the shared
        # root, defaulting to ``logs/`` so a fresh workspace ends up
        # with ``<root>/logs/telemetry.jsonl``,
        # ``<root>/logs/audit.jsonl``, ... while sibling utilities
        # (session store) keep their own bucket.
        directory = str(enriched.get("directory", "logs"))
        provider = FilesystemEventStreamProvider(
            connection=connection, directory=directory
        )

        all_components.extend((adapter, connection, provider))
        provider_index[persistence_id] = provider
        connection_index[persistence_id] = connection
    return all_components, provider_index, connection_index


def _resolve_default_persistence(
    persistence: dict[str, EventStreamProviderPort],
) -> EventStreamProviderPort | None:
    """Pick the implicit default when an observer omits ``persistence:``.

    Rule: a single configured provider is the default. With two or
    more, an observer must declare ``persistence: <id>`` explicitly --
    we refuse to guess which storage gets the writes.
    """
    if not persistence:
        return None
    if len(persistence) == 1:
        return next(iter(persistence.values()))
    if _DEFAULT_PERSISTENCE_ID in persistence:
        return persistence[_DEFAULT_PERSISTENCE_ID]
    return None


def _normalize_observer_specs(spec: Any, *, slot: str) -> list[dict[str, Any]]:
    """Accept a single mapping or a list of mappings for an observer slot."""
    if spec is None:
        return []
    if isinstance(spec, dict):
        return [dict(spec)]
    if isinstance(spec, list):
        for index, entry in enumerate(spec):
            if not isinstance(entry, dict):
                raise ConfigError(
                    f"robot.yaml#{slot}[{index}] must be a mapping"
                )
        return [dict(entry) for entry in spec]
    raise ConfigError(
        f"robot.yaml#{slot} must be a mapping or a list of mappings"
    )


def _build_observer_components(
    spec: Any,
    *,
    slot: str,
    expected_category: ComponentCategory,
    default_name: str,
    default_channel: str,
    sink_bound_names: dict[str, type[RobotComponent]],
    persistence: dict[str, EventStreamProviderPort],
    library: ComponentLibrary,
) -> list[RobotComponent]:
    """Build a list of observer components for one slot (``telemetry`` /
    ``audit``).

    Both slots are kind-aliased lists of bus-attached observers that
    write through an :class:`EventStreamProviderPort`. Shape parity
    with ``kernel`` / ``channel``: singular mapping or list, both
    valid.

    Two flavours of entries:

    - **Provider-bound observers** -- names listed in
      ``sink_bound_names`` (today: ``bus_recorder`` for telemetry,
      ``audit_note_sink`` for audit). Constructed via the mapped
      class with ``provider=<chosen provider>, channel=<channel>``.
      The provider is selected by an optional ``persistence: <id>``
      field; a single configured provider is the implicit default.
      When no persistence is configured the observer is skipped with
      an info log (instead of failing the boot).
    - **Regular bus-attached observers** -- any other name resolves
      through the registry like a normal component. Constructed via
      :func:`_build_with_library`. The result must have the slot's
      expected category, otherwise the build aborts.
    """
    out: list[RobotComponent] = []
    for entry in _normalize_observer_specs(spec, slot=slot):
        # Slot has a canonical default name (``bus_recorder`` /
        # ``audit_note_sink``), so an entry that only overrides a
        # field (e.g. ``{channel: raw-events}``) still resolves.
        if "name" not in entry:
            entry = {"name": default_name, **entry}
        enriched = _apply_library_defaults(
            entry, category=slot, library=library
        )
        name = str(enriched.get("name", ""))
        if not name:
            raise ConfigError(f"{slot} entry must declare a 'name'")

        sink_class = sink_bound_names.get(name)
        if sink_class is not None:
            provider = _select_persistence(enriched, persistence, slot=slot)
            if provider is None:
                logger.info(
                    "%s configured but no persistence available; "
                    "skipping %s",
                    slot,
                    sink_class.__name__,
                )
                continue
            channel = str(enriched.get("channel", default_channel))
            out.append(sink_class(provider=provider, channel=channel))  # type: ignore[call-arg]
            continue

        component = _build_with_library(
            enriched, category=slot, library=library
        )
        if component.component_category is not expected_category:
            raise ConfigError(
                f"{slot} component {name!r} resolved to "
                f"{type(component).__name__} with category "
                f"{component.component_category.value}; expected "
                f"{expected_category.value}"
            )
        out.append(component)
    return out


def _select_persistence(
    spec: dict[str, Any],
    persistence: dict[str, EventStreamProviderPort],
    *,
    slot: str,
) -> EventStreamProviderPort | None:
    """Pick the persistence provider an observer entry asks for.

    Resolution rules:

    - explicit ``persistence: <id>`` on the entry -> exact lookup
      (missing id is a configuration error, not a silent skip);
    - no field set and exactly one provider configured -> that one;
    - no field set and multiple providers -> :func:`_resolve_default_persistence`
      (returns the ``default``-id provider if present, ``None``
      otherwise -- which the caller turns into a skipped observer);
    - no providers at all -> ``None``.
    """
    requested = spec.get("persistence")
    if requested is not None:
        requested_id = str(requested)
        provider = persistence.get(requested_id)
        if provider is None:
            available = sorted(persistence.keys()) or ["<none>"]
            raise ConfigError(
                f"{slot} references persistence id {requested_id!r} "
                f"which is not configured (available: {available})"
            )
        return provider
    return _resolve_default_persistence(persistence)


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
