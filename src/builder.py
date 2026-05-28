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
    deep_merge,
    load_robot_env,
)
from src.kernel.ports import KernelPort
from src.llm.actor import LLMActor
from src.llm.metadata_service import ModelMetadataService
from src.llm.ports import LLMProviderPort
from src.llm.providers.mock import MockLLMProvider
from src.persistence.provider import JsonlPersistenceProvider, PersistenceProvider
from src.registry import ConfigError, build
from src.robot import ControlPlaneConfig, Robot, RobotIdentity
from src.telemetry.bus_recorder import BusRecorder

logger = logging.getLogger(__name__)

_DEFAULT_BUS_SPEC: dict[str, Any] = {"name": "asyncio"}
_DEFAULT_TELEMETRY_CHANNEL = "telemetry"
_DEFAULT_AUDIT_CHANNEL = "audit"

# Mini-registry for LLM provider classes. Providers are not full
# RobotComponents (they have no component_name / category) and are
# always wrapped by an actor that owns their lifecycle, so they live
# here instead of in the main component registry.
_LLM_PROVIDER_REGISTRY: dict[str, type[LLMProviderPort]] = {
    "mock": MockLLMProvider,
}


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
    Used to locate the ``.env`` file for the control-plane token. If
    omitted, no ``.env`` is read and the control plane refuses to
    start (deny-by-default).
    """
    if not isinstance(robot_yaml, dict):
        raise ConfigError("robot.yaml must be a mapping at the top level")

    base = dict(defaults or {})
    cfg = deep_merge(base, dict(robot_yaml))

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

    # Governance services are built before actors so an LLMActor
    # can take a constructor-time reference to the catalog. The
    # robot's boot order runs the service's start() first anyway
    # (GOVERNANCE=7 < ACTOR=8), but the ports themselves are wired
    # at construction time.
    metadata_service = _build_governance_metadata(cfg.get("governance"))

    # Order matters: the actor is a runtime dependency the kernel
    # holds a direct reference to, so it must exist before the kernel
    # is constructed. The robot's lifecycle still treats both as
    # peers; the kernel just receives the already-built actor through
    # constructor injection.
    actor_spec = cfg.get("actor")
    if not actor_spec:
        raise ConfigError(
            "robot.yaml must declare an actor section; the kernel "
            "needs an actor to consult during its act phase"
        )
    actor_built = _build_actor(actor_spec, metadata_service=metadata_service)
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

    components: list[RobotComponent] = [bus, actor, kernel, *channels]
    if telemetry is not None:
        components.append(telemetry)
    if audit is not None:
        components.append(audit)
    if metadata_service is not None:
        components.append(metadata_service)

    return Robot(
        identity=identity,
        components=components,
        control_plane_config=control_plane_config,
        control_plane_token=control_plane_token,
    )


def _build_governance_metadata(spec: Any) -> ModelMetadataService | None:
    """Build the :class:`ModelMetadataService` from ``governance.model_metadata``.

    Layout::

        governance:
          model_metadata:
            enabled: true       # default true if the block exists
            # (future: source / refresh-on-boot / ...)

    Returns ``None`` when the block is absent or explicitly
    disabled. The service exposes the bundled snapshot by default;
    custom sources arrive once the llmprice-kit-backed source ships.
    """
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise ConfigError("robot.yaml#governance must be a mapping")
    metadata_spec = spec.get("model_metadata")
    if metadata_spec is None:
        return None
    if not isinstance(metadata_spec, dict):
        raise ConfigError(
            "robot.yaml#governance.model_metadata must be a mapping"
        )
    if not bool(metadata_spec.get("enabled", True)):
        return None
    # Drop the well-known keys the builder handles itself; pass the
    # rest as constructor kwargs (forward-compatible with future
    # ``source: ...`` entries).
    metadata_spec = dict(metadata_spec)
    metadata_spec.pop("enabled", None)
    return build({"name": "model-metadata", **metadata_spec})


def _build_actor(
    actor_spec: Any,
    *,
    metadata_service: ModelMetadataService | None,
) -> ActorPort:
    """Build the actor with optional LLM-stack dependency injection.

    Recognised special case: when the spec resolves to
    :class:`LLMActor`, the builder also constructs the nested
    ``provider:`` sub-spec and injects ``catalog`` / ``pricing``
    from the governance metadata service when available. Other
    actors are built unchanged.
    """
    if not isinstance(actor_spec, dict):
        raise ConfigError("robot.yaml#actor must be a mapping")

    actor_spec = dict(actor_spec)
    cls = _resolve_actor_class(actor_spec)

    if cls is LLMActor:
        return _build_llm_actor(actor_spec, metadata_service=metadata_service)

    return build(actor_spec)


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


def _build_llm_actor(
    spec: dict[str, Any],
    *,
    metadata_service: ModelMetadataService | None,
) -> ActorPort:
    """Construct an :class:`LLMActor` and inject its dependencies."""
    provider_spec = spec.pop("provider", None)
    if provider_spec is None:
        raise ConfigError(
            "LLMActor requires a 'provider' sub-mapping in the actor "
            "spec, e.g. provider: {name: mock, model_id: echo, "
            "provider: mock}"
        )

    provider = _build_llm_provider(
        provider_spec,
        metadata_service=metadata_service,
    )

    extras: dict[str, Any] = {"provider": provider}
    if metadata_service is not None and "catalog" not in spec:
        extras["catalog"] = metadata_service.as_catalog_port()

    return build(spec, **extras)


def _build_llm_provider(
    spec: Any,
    *,
    metadata_service: ModelMetadataService | None,
) -> LLMProviderPort:
    """Build an :class:`LLMProviderPort` from a YAML sub-mapping.

    Two ways to specify a provider, mirroring the main registry:

    1. By registered name (``name: mock``).
    2. By dotted Python path (``class: my.pkg.MyProvider``).

    The builder injects ``catalog`` and ``pricing`` keyword arguments
    from the metadata service when the provider's constructor
    accepts them.
    """
    import inspect

    if not isinstance(spec, dict):
        raise ConfigError("provider spec must be a mapping")
    spec = dict(spec)

    cls: type[LLMProviderPort]
    if "class" in spec:
        from src.registry import _import_class  # type: ignore[attr-defined]

        cls_path = spec.pop("class")
        if not isinstance(cls_path, str) or "." not in cls_path:
            raise ConfigError(
                f"'class' must be a dotted path, got {cls_path!r}"
            )
        cls = _import_class(cls_path)
    elif "name" in spec:
        name = spec.pop("name")
        if not isinstance(name, str):
            raise ConfigError(f"'name' must be a string, got {name!r}")
        try:
            cls = _LLM_PROVIDER_REGISTRY[name]
        except KeyError as exc:
            known = ", ".join(sorted(_LLM_PROVIDER_REGISTRY)) or "(none)"
            raise ConfigError(
                f"unknown llm provider {name!r}; known: {known}"
            ) from exc
    else:
        raise ConfigError(
            "provider spec needs either a 'name' or a 'class' key"
        )

    if metadata_service is not None:
        sig = inspect.signature(cls)
        params = sig.parameters
        if "catalog" in params and "catalog" not in spec:
            spec["catalog"] = metadata_service.as_catalog_port()
        if "pricing" in params and "pricing" not in spec:
            spec["pricing"] = metadata_service.as_pricing_port()

    try:
        instance = cls(**spec)
    except TypeError as exc:
        raise ConfigError(
            f"failed to construct provider {cls.__name__}: {exc}"
        ) from exc
    if not isinstance(instance, LLMProviderPort):
        raise ConfigError(
            f"provider {cls.__name__} does not implement LLMProviderPort"
        )
    return instance


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
