"""Component registry that turns config specs into instances.

Two ways to specify a component in a YAML config:

1. By registered name::

       kernel:
         name: base
         actor_timeout: 30.0

2. By dotted Python path (for plugins outside ``src``)::

       kernel:
         class: my_org.kernels.fancy.FancyKernel
         some_arg: 42

Either way, all remaining fields are passed as keyword arguments to the
constructor. Unknown kwargs raise :class:`ConfigError` with a helpful
message before the component is constructed.

Vocabulary: the *category* (BUS, ACTOR, KERNEL, CHANNEL, ...) is what
the role is. The *name* is which concrete implementation in that role
the user picked. ``EchoActor.component_name == "echo"`` means: in the
ACTOR category, "echo" is the registry key for this class.

The builder may inject *runtime dependencies* on top of the YAML
fields via :func:`build`'s ``**extra_kwargs`` (e.g. the actor that
the kernel should drive). These are not part of the user-visible
configuration surface; they live in code where references between
components are resolved.

Built-in components register themselves at import time of this module.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any

from src.components import ComponentCategory, RobotComponent


class ConfigError(ValueError):
    """Raised when a component spec cannot be resolved or built."""


_REGISTRY: dict[str, type[RobotComponent]] = {}


def register(cls: type[RobotComponent]) -> type[RobotComponent]:
    """Register ``cls`` under its declared :attr:`component_name`.

    The registry indexes by ``cls.component_name``. Re-registering the
    same name with a different class raises :class:`ConfigError`.
    """
    if not isinstance(cls, type) or not issubclass(cls, RobotComponent):
        raise ConfigError(
            f"register() expects a RobotComponent subclass, got {cls!r}"
        )
    name = getattr(cls, "component_name", None)
    if not isinstance(name, str) or not name:
        raise ConfigError(
            f"{cls.__name__} is missing a non-empty component_name"
        )
    existing = _REGISTRY.get(name)
    if existing is not None and existing is not cls:
        raise ConfigError(
            f"component_name {name!r} is already registered to "
            f"{existing.__name__}; refusing to override with {cls.__name__}"
        )
    _REGISTRY[name] = cls
    return cls


def get(name: str) -> type[RobotComponent]:
    """Look up a registered component by name."""
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ConfigError(
            f"unknown component name {name!r}; known names: {known}"
        ) from exc


def list_by_category(category: ComponentCategory) -> list[type[RobotComponent]]:
    """Return all registered components of the given category."""
    return [cls for cls in _REGISTRY.values() if cls.component_category == category]


def all_registered() -> dict[str, type[RobotComponent]]:
    """Return a copy of the full registry, keyed by ``component_name``."""
    return dict(_REGISTRY)


def build(spec: dict[str, Any], **extra_kwargs: Any) -> RobotComponent:
    """Build a component instance from a config dictionary.

    Resolution order:

    1. If ``spec`` carries a ``class`` key, import that dotted path.
    2. Otherwise, look up ``spec["name"]`` in the registry.

    All remaining fields become constructor kwargs after they are
    validated against the constructor signature.

    ``extra_kwargs`` are merged on top of the spec fields and passed
    to the constructor. Use this for runtime dependencies that are
    resolved in code, not in YAML (e.g. injecting an :class:`ActorPort`
    instance into a kernel). Extra kwargs override spec fields on
    name collision -- callers are expected to use this only for
    keys the YAML schema does not expose.
    """
    if not isinstance(spec, dict):
        raise ConfigError(f"component spec must be a dict, got {type(spec).__name__}")

    spec = dict(spec)

    cls: type
    if "class" in spec:
        cls_path = spec.pop("class")
        if not isinstance(cls_path, str) or "." not in cls_path:
            raise ConfigError(
                f"'class' must be a dotted path like 'pkg.module.ClassName', "
                f"got {cls_path!r}"
            )
        cls = _import_class(cls_path)
    elif "name" in spec:
        name = spec.pop("name")
        if not isinstance(name, str):
            raise ConfigError(f"'name' must be a string, got {name!r}")
        cls = get(name)
    else:
        raise ConfigError("component spec needs either a 'name' or a 'class' key")

    if extra_kwargs:
        spec.update(extra_kwargs)
    return _instantiate(cls, spec)


def _import_class(dotted: str) -> type:
    module_name, _, attr = dotted.rpartition(".")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ConfigError(f"cannot import module {module_name!r}: {exc}") from exc
    try:
        cls = getattr(module, attr)
    except AttributeError as exc:
        raise ConfigError(
            f"module {module_name!r} has no attribute {attr!r}"
        ) from exc
    if not isinstance(cls, type):
        raise ConfigError(f"{dotted!r} is not a class, got {cls!r}")
    return cls


def _instantiate(cls: type, kwargs: dict[str, Any]) -> Any:
    """Validate ``kwargs`` against ``cls.__init__`` and call it."""
    try:
        sig = inspect.signature(cls)
    except (ValueError, TypeError):
        return cls(**kwargs)

    accepts_kwargs = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if not accepts_kwargs:
        accepted_names = {
            name
            for name, p in sig.parameters.items()
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
        }
        unknown = set(kwargs) - accepted_names
        if unknown:
            sorted_known = sorted(accepted_names)
            raise ConfigError(
                f"unknown parameter(s) for {cls.__name__}: "
                f"{sorted(unknown)}; accepted: {sorted_known}"
            )

    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ConfigError(f"failed to construct {cls.__name__}: {exc}") from exc


def _register_builtins() -> None:
    """Register the components that ship with cephix."""
    from src.actor.echo import EchoActor
    from src.bus.asyncio_bus import AsyncioBus
    from src.channels.websocket import WebsocketChannel
    from src.kernel.base import BaseKernel
    from src.llm.catalog import ModelCatalog
    from src.llm.mock_actor import MockLLMActor

    register(AsyncioBus)
    register(BaseKernel)
    register(EchoActor)
    register(MockLLMActor)
    register(ModelCatalog)
    register(WebsocketChannel)


_register_builtins()
