"""Assemble a :class:`Robot` from a ``robot.yaml`` mapping.

The builder is the bridge between configuration and runtime:

- It deep-merges global defaults from ``cephix.yaml#defaults`` with the
  bot-specific ``robot.yaml`` (bot wins).
- It resolves the ``bus``, ``kernel``, ``actor`` and ``channels`` blocks
  via the component registry. The cross-cutting persistence layer is
  built *once* from the top-level ``persistence:`` block and shared by
  every component that needs an :class:`EventSink`; observer components
  (``telemetry:``, ``audit:``) only declare ``enabled`` and an
  optional ``channel`` name.
- It hands identity, the control-plane configuration and the
  components straight to the :class:`Robot` constructor. The
  control-plane token is taken from the bot-local ``.env`` (key
  ``CEPHIX_CONTROL_PLANE_TOKEN``) so it never lives inside the YAML.

The ``enabled`` flag stays out of the builder: it's a CLI-layer
filter for the smart-default and the future ``--all`` filter, and has
no runtime meaning once a bot is actually being started.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.actor.ports import ActorPort
from src.audit.note_sink import AuditNoteSink
from src.bus.ports import BusPort
from src.channels.ports import ChannelPort
from src.components import RobotComponent
from src.configuration import (
    CONTROL_PLANE_TOKEN_ENV,
    HOME_ENV_FILENAME,
    ROBOT_ENV_FILENAME,
    deep_merge,
    home_dir,
    load_robot_env,
)
from src.components import ComponentCategory
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

_DEFAULT_BUS_SPEC: dict[str, Any] = {"name": "asyncio"}
_DEFAULT_TELEMETRY_CHANNEL = "telemetry"
_DEFAULT_AUDIT_CHANNEL = "audit"


def build_robot_from_config(
    robot_yaml: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
    workspace: str | Path | None = None,
) -> Robot:
    """Build a :class:`Robot` from a parsed ``robot.yaml`` mapping.

    ``defaults`` typically comes from
    :func:`src.configuration.home_defaults`. The merge is deep, with
    ``robot_yaml`` winning on conflicts. Channel lists are not merged
    element-by-element: if ``robot_yaml`` provides a ``channels`` list,
    it fully replaces the default list.

    ``workspace`` is the bot's directory (where ``robot.yaml`` lives).
    Used to locate the ``.env`` file for the control-plane token *and*
    as the default first store for the credential subsystem. If
    omitted, no ``.env`` is read and the control plane refuses to
    start (deny-by-default).

    The build runs in three phases:

    1. **Credential stores** are constructed from ``credentials:`` (or
       a default chain anchored at ``workspace/.env`` and
       ``~/.cephix/.env``) and a :class:`CredentialProvider` is built
       around them.
    2. The remaining configuration is run through
       :func:`~src.credentials.substitution.resolve_secrets` so every
       ``${KEY}`` reference is replaced before any component
       constructor sees it. A missing key raises
       :class:`~src.credentials.exceptions.CredentialNotFound`, which
       aborts the build -- no half-constructed robot.
    3. Components are instantiated in dependency order. Actors whose
       constructors declare a ``catalog`` or ``credentials`` kwarg
       receive the shared instances automatically (Convention-DI).
    """
    if not isinstance(robot_yaml, dict):
        raise ConfigError("robot.yaml must be a mapping at the top level")

    base = dict(defaults or {})
    cfg = deep_merge(base, dict(robot_yaml))

    # Phase 1: credentials. Constructed before any other component so
    # the substitution pass below can resolve ${KEY} references.
    credentials_spec = cfg.pop("credentials", None)
    credentials = _build_credential_provider(
        credentials_spec,
        workspace=workspace,
    )

    # Phase 2: substitution. Walks every remaining section and
    # replaces ``${KEY}`` references with their resolved values.
    # Fail-fast: a missing key raises CredentialNotFound here, the
    # builder propagates it, and no robot is ever born.
    cfg = resolve_secrets(
        cfg,
        lambda key: credentials.resolve_sync(key, requester="builder"),
    )

    # Phase 3: assemble the rest of the robot.
    bus_spec = cfg.get("bus") or _DEFAULT_BUS_SPEC
    bus = build(bus_spec)
    if not isinstance(bus, BusPort):
        raise ConfigError(
            f"bus component {type(bus).__name__} does not implement BusPort"
        )
    if not isinstance(bus, RobotComponent):
        raise ConfigError(
            f"bus component {type(bus).__name__} does not implement "
            "RobotComponent"
        )

    # Utilities are built before actors so a future LLMKernel (or
    # any other consumer) can hold a constructor-time reference to
    # them. The robot's boot order runs them in their boot priority
    # anyway (UTILITY=5 < ACTOR=8 < KERNEL=10), but their *ports* are
    # wired at construction time. The same applies to bus utilities.
    utilities = _build_components_list(
        cfg.get("utility"),
        section_name="utility",
        expected_category=ComponentCategory.UTILITY,
    )
    bus_utilities = _build_components_list(
        cfg.get("bus_utility"),
        section_name="bus_utility",
        expected_category=ComponentCategory.BUS_UTILITY,
    )

    # Order matters: the actor is a runtime dependency the kernel
    # holds a direct reference to, so it must exist before the kernel
    # is constructed. The robot's lifecycle still treats both as
    # peers; the kernel just receives the already-built actor through
    # constructor injection.
    #
    # No ``catalog`` injection here: the model catalog is the future
    # ``LLMKernel``'s dependency, not the actor's. The actor is a
    # driver -- it surfaces ``tokens_in`` / ``tokens_out`` and lets
    # the kernel compute ``cost_usd`` against the catalog.
    actor_spec = cfg.get("actor")
    if not actor_spec:
        raise ConfigError(
            "robot.yaml must declare an actor section; the kernel "
            "needs an actor to consult during its act phase"
        )
    actor_built = _build_actor(
        actor_spec,
        credentials=credentials,
    )
    if not isinstance(actor_built, ActorPort):
        raise ConfigError(
            f"actor component {type(actor_built).__name__} does not "
            "implement ActorPort"
        )
    actor = actor_built

    kernel_spec = cfg.get("kernel")
    if not kernel_spec:
        raise ConfigError("robot.yaml must declare a kernel section")
    kernel = build(kernel_spec, actor=actor)
    if not isinstance(kernel, KernelPort):
        raise ConfigError(
            f"kernel component {type(kernel).__name__} does not implement KernelPort"
        )
    if not isinstance(kernel, RobotComponent):
        raise ConfigError(
            f"kernel component {type(kernel).__name__} does not implement "
            "RobotComponent"
        )

    channel_specs = cfg.get("channels") or []
    if not isinstance(channel_specs, list):
        raise ConfigError("robot.yaml#channels must be a list")
    channels: list[ChannelPort] = []
    for index, spec in enumerate(channel_specs):
        component = build(spec)
        if not isinstance(component, ChannelPort):
            raise ConfigError(
                f"channel #{index} ({type(component).__name__}) does not implement ChannelPort"
            )
        if not isinstance(component, RobotComponent):
            raise ConfigError(
                f"channel #{index} ({type(component).__name__}) does not "
                "implement RobotComponent"
            )
        channels.append(component)

    persistence = _build_persistence_provider(cfg.get("persistence"), workspace)
    telemetry = _build_telemetry(cfg.get("telemetry"), persistence)
    audit = _build_audit(cfg.get("audit"), persistence)

    robot_id_raw = robot_yaml.get("id")
    robot_name_raw = robot_yaml.get("name")
    identity = RobotIdentity(
        id=str(robot_id_raw) if robot_id_raw else None,
        name=str(robot_name_raw) if robot_name_raw else None,
    )

    control_plane_config = _control_plane_config(cfg.get("control_plane"))
    control_plane_token: str | None = None
    if workspace is not None:
        env = load_robot_env(workspace)
        control_plane_token = env.get(CONTROL_PLANE_TOKEN_ENV) or None

    components: list[RobotComponent] = [
        bus,
        credentials,
        actor,
        kernel,
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

    The provider is registered as a :class:`RobotComponent` and
    will be started by the robot during the ``BUS_UTILITY`` boot
    phase. The builder uses it synchronously *now*, before the
    robot lifecycle even runs, so substitution can fail loud and
    early.
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
    ``os.environ``. Missing files are tolerated; the global home
    is created on demand by :func:`home_dir` so the global store
    always has a path even if the file is empty.
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


def _build_components_list(
    spec: Any,
    *,
    section_name: str,
    expected_category: ComponentCategory,
) -> list[RobotComponent]:
    """Build a list of components from a category-keyed YAML section.

    Layout (Variant A from the design discussion)::

        utility:
          - name: model-catalog
          - name: tokenizer-cache  # future
        bus_utility:
          - name: cost-aggregator  # future
          - name: credentials-broker  # future

    Each entry is a regular component spec (resolved via
    :func:`build`). The builder verifies the resulting component's
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
        component = build(item)
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


def _build_actor(
    actor_spec: Any,
    *,
    credentials: CredentialProviderPort | None = None,
) -> ActorPort:
    """Build the actor, injecting credentials by Convention-DI.

    Any actor whose constructor declares a ``credentials`` keyword
    gets the shared :class:`CredentialProviderPort`; actors that
    don't declare it (e.g. :class:`~src.actor.echo.EchoActor`) are
    untouched. An explicit value in the YAML spec wins over the
    auto-injection.

    Note: the model catalog is *not* injected into actors. Actors
    are drivers -- they report token counts; the future
    ``LLMKernel`` consults the catalog to compute cost. Catalog
    injection happens at kernel construction (once the LLMKernel
    lands), not here.
    """
    import inspect

    if not isinstance(actor_spec, dict):
        raise ConfigError("robot.yaml#actor must be a mapping")
    actor_spec = dict(actor_spec)

    cls = _resolve_actor_class(actor_spec)
    try:
        sig = inspect.signature(cls)
    except (ValueError, TypeError):
        sig = None

    extras: dict[str, Any] = {}
    if (
        credentials is not None
        and "credentials" not in actor_spec
        and sig is not None
        and "credentials" in sig.parameters
    ):
        extras["credentials"] = credentials

    return build(actor_spec, **extras)


def _resolve_actor_class(spec: dict[str, Any]) -> type:
    """Peek the registered/imported class without consuming the spec."""
    from src.registry import _import_class, get  # type: ignore[attr-defined]

    if "class" in spec:
        cls_path = spec["class"]
        if not isinstance(cls_path, str):
            raise ConfigError(
                "robot.yaml#actor.class must be a dotted path string"
            )
        return _import_class(cls_path)
    if "name" in spec:
        name = spec["name"]
        if not isinstance(name, str):
            raise ConfigError("robot.yaml#actor.name must be a string")
        return get(name)
    raise ConfigError(
        "robot.yaml#actor needs either a 'name' or a 'class' key"
    )


def _build_persistence_provider(
    spec: Any,
    workspace: str | Path | None,
) -> PersistenceProvider | None:
    """Build the robot-wide :class:`PersistenceProvider`.

    Returns ``None`` when persistence is explicitly disabled or when
    no usable root can be derived (no explicit path *and* no
    workspace). Components that depend on the provider take ``None``
    as "no persistence configured" and skip themselves with an info
    log -- the boot succeeds either way.
    """
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ConfigError("robot.yaml#persistence must be a mapping")
    if not bool(spec.get("enabled", True)):
        return None

    name = str(spec.get("name", "jsonl"))
    if name != "jsonl":
        raise ConfigError(
            f"unknown persistence backend {name!r}; "
            "the only built-in persistence backend is 'jsonl'"
        )

    raw_path = spec.get("path", "logs")
    candidate = Path(str(raw_path)).expanduser()
    if not candidate.is_absolute():
        if workspace is None:
            return None
        candidate = Path(workspace) / candidate

    return JsonlPersistenceProvider(candidate)


def _build_telemetry(
    spec: Any,
    persistence: PersistenceProvider | None,
) -> BusRecorder | None:
    """Build the telemetry component from ``robot.yaml#telemetry``.

    Returns ``None`` when telemetry is explicitly disabled or when no
    persistence provider is available -- without somewhere to write
    to, a recorder is a passive observer with side effects and
    nothing more.
    """
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ConfigError("robot.yaml#telemetry must be a mapping")
    if not bool(spec.get("enabled", True)):
        return None

    name = str(spec.get("name", "bus_recorder"))
    if name != "bus_recorder":
        raise ConfigError(
            f"unknown telemetry component {name!r}; "
            "the only built-in telemetry component is 'bus_recorder'"
        )

    if persistence is None:
        logger.info(
            "telemetry enabled but no persistence configured; "
            "skipping BusRecorder"
        )
        return None

    channel = str(spec.get("channel", _DEFAULT_TELEMETRY_CHANNEL))
    return BusRecorder(sink=persistence.open(channel))


def _build_audit(
    spec: Any,
    persistence: PersistenceProvider | None,
) -> AuditNoteSink | None:
    """Build the audit component from ``robot.yaml#audit``."""
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ConfigError("robot.yaml#audit must be a mapping")
    if not bool(spec.get("enabled", True)):
        return None

    name = str(spec.get("name", "audit_note_sink"))
    if name != "audit_note_sink":
        raise ConfigError(
            f"unknown audit component {name!r}; "
            "the only built-in audit component is 'audit_note_sink'"
        )

    if persistence is None:
        logger.info(
            "audit enabled but no persistence configured; "
            "skipping AuditNoteSink"
        )
        return None

    channel = str(spec.get("channel", _DEFAULT_AUDIT_CHANNEL))
    return AuditNoteSink(sink=persistence.open(channel))


def _control_plane_config(spec: Any) -> ControlPlaneConfig:
    """Translate a ``control_plane:`` YAML block into a config dataclass."""
    if spec is None:
        return ControlPlaneConfig()
    if not isinstance(spec, dict):
        raise ConfigError("robot.yaml#control_plane must be a mapping")

    enabled = bool(spec.get("enabled", True))
    host = str(spec.get("host", "127.0.0.1"))
    port = int(spec.get("port", 9876))
    path = str(spec.get("path", "/control"))

    raw_range = spec.get("port_range") or [9876, 9999]
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
