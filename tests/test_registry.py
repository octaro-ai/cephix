"""Tests for the component registry."""

from __future__ import annotations

import pytest

from src.actor.echo import EchoActor
from src.bus.asyncio_bus import AsyncioBus
from src.channels.websocket import WebsocketChannel
from src.components import ComponentCategory, RobotComponent
from src.kernel.base import BaseKernel
from src.registry import (
    ConfigError,
    all_registered,
    build,
    get,
    list_by_category,
    register,
)


def test_builtin_components_are_registered() -> None:
    keys = set(all_registered())
    assert {"asyncio", "base", "echo", "websocket"} <= keys


def test_get_returns_registered_class() -> None:
    assert get("base") is BaseKernel
    assert get("asyncio") is AsyncioBus
    assert get("echo") is EchoActor
    assert get("websocket") is WebsocketChannel


def test_get_unknown_raises_with_known_list() -> None:
    with pytest.raises(ConfigError, match="known names"):
        get("does-not-exist")


def test_list_by_category_filters() -> None:
    kernels = list_by_category(ComponentCategory.KERNEL)
    assert BaseKernel in kernels
    assert AsyncioBus not in kernels
    assert EchoActor not in kernels

    actors = list_by_category(ComponentCategory.ACTOR)
    assert EchoActor in actors
    assert BaseKernel not in actors


def test_register_rejects_non_robot_component() -> None:
    class NotAComponent:
        pass

    with pytest.raises(ConfigError):
        register(NotAComponent)  # type: ignore[arg-type]


def test_register_rejects_missing_component_name() -> None:
    class Bare(RobotComponent):
        component_category = ComponentCategory.KERNEL

    with pytest.raises(ConfigError, match="component_name"):
        register(Bare)


def test_register_is_idempotent_for_same_class() -> None:
    register(BaseKernel)
    register(BaseKernel)
    assert get("base") is BaseKernel


def test_register_rejects_collisions() -> None:
    class FakeKernel(RobotComponent):
        component_name = "base"
        component_category = ComponentCategory.KERNEL

    with pytest.raises(ConfigError, match="already registered"):
        register(FakeKernel)


def test_build_with_name_returns_instance() -> None:
    actor = EchoActor()
    instance = build({"name": "base", "actor_timeout": 7.0}, actor=actor)
    assert isinstance(instance, BaseKernel)
    assert instance._actor_timeout == 7.0  # type: ignore[attr-defined]
    assert instance._actor is actor  # type: ignore[attr-defined]


def test_build_actor_with_name_returns_instance() -> None:
    instance = build({"name": "echo", "prefix": "yo: "})
    assert isinstance(instance, EchoActor)
    assert instance._prefix == "yo: "  # type: ignore[attr-defined]


def test_build_extra_kwargs_inject_runtime_dependency() -> None:
    """``build(spec, **extra)`` injects dependencies the YAML can't express."""
    actor = EchoActor()
    kernel = build({"name": "base"}, actor=actor)
    assert isinstance(kernel, BaseKernel)
    assert kernel._actor is actor  # type: ignore[attr-defined]


def test_build_extra_kwargs_override_spec_keys() -> None:
    """Runtime dependencies win over spec fields on collision."""
    instance = build({"name": "echo", "prefix": "from-spec: "}, prefix="from-extra: ")
    assert isinstance(instance, EchoActor)
    assert instance._prefix == "from-extra: "  # type: ignore[attr-defined]


def test_build_with_unknown_kwargs_raises() -> None:
    with pytest.raises(ConfigError, match="unknown parameter"):
        build({"name": "base", "nonsense": 42}, actor=EchoActor())


def test_build_with_class_dotted_path() -> None:
    actor = EchoActor()
    instance = build(
        {"class": "src.kernel.base.BaseKernel", "actor_timeout": 4.0},
        actor=actor,
    )
    assert isinstance(instance, BaseKernel)
    assert instance._actor_timeout == 4.0  # type: ignore[attr-defined]


def test_build_rejects_invalid_class_path() -> None:
    with pytest.raises(ConfigError, match="dotted path"):
        build({"class": "no_dot"})


def test_build_rejects_unknown_module() -> None:
    with pytest.raises(ConfigError, match="cannot import"):
        build({"class": "no_such_module.SomeClass"})


def test_build_rejects_missing_attribute() -> None:
    with pytest.raises(ConfigError, match="no attribute"):
        build({"class": "src.kernel.base.NoSuchClass"})


def test_build_requires_name_or_class() -> None:
    with pytest.raises(ConfigError, match="name.*class"):
        build({"prefix": "x: "})


def test_build_rejects_non_dict_spec() -> None:
    with pytest.raises(ConfigError):
        build("base")  # type: ignore[arg-type]
