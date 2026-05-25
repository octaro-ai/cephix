"""Tests for the component registry."""

from __future__ import annotations

import pytest

from src.bus.asyncio_bus import AsyncioBus
from src.channels.websocket import WebsocketChannel
from src.components import ComponentCategory, RobotComponent
from src.kernel.echo import EchoKernel
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
    assert {"asyncio", "echo", "websocket"} <= keys


def test_get_returns_registered_class() -> None:
    assert get("echo") is EchoKernel
    assert get("asyncio") is AsyncioBus
    assert get("websocket") is WebsocketChannel


def test_get_unknown_raises_with_known_list() -> None:
    with pytest.raises(ConfigError, match="known types"):
        get("does-not-exist")


def test_list_by_category_filters() -> None:
    kernels = list_by_category(ComponentCategory.KERNEL)
    assert EchoKernel in kernels
    assert AsyncioBus not in kernels


def test_register_rejects_non_robot_component() -> None:
    class NotAComponent:
        pass

    with pytest.raises(ConfigError):
        register(NotAComponent)  # type: ignore[arg-type]


def test_register_rejects_missing_component_type() -> None:
    class Bare(RobotComponent):
        component_category = ComponentCategory.KERNEL

    with pytest.raises(ConfigError, match="component_type"):
        register(Bare)


def test_register_is_idempotent_for_same_class() -> None:
    register(EchoKernel)
    register(EchoKernel)
    assert get("echo") is EchoKernel


def test_register_rejects_collisions() -> None:
    class FakeKernel(RobotComponent):
        component_type = "echo"
        component_category = ComponentCategory.KERNEL

    with pytest.raises(ConfigError, match="already registered"):
        register(FakeKernel)


def test_build_with_type_returns_instance() -> None:
    instance = build({"type": "echo", "prefix": "yo: "})
    assert isinstance(instance, EchoKernel)
    assert instance._prefix == "yo: "  # type: ignore[attr-defined]


def test_build_with_unknown_kwargs_raises() -> None:
    with pytest.raises(ConfigError, match="unknown parameter"):
        build({"type": "echo", "nonsense": 42})


def test_build_with_class_dotted_path() -> None:
    instance = build({"class": "src.kernel.echo.EchoKernel", "prefix": "x: "})
    assert isinstance(instance, EchoKernel)
    assert instance._prefix == "x: "  # type: ignore[attr-defined]


def test_build_rejects_invalid_class_path() -> None:
    with pytest.raises(ConfigError, match="dotted path"):
        build({"class": "no_dot"})


def test_build_rejects_unknown_module() -> None:
    with pytest.raises(ConfigError, match="cannot import"):
        build({"class": "no_such_module.SomeClass"})


def test_build_rejects_missing_attribute() -> None:
    with pytest.raises(ConfigError, match="no attribute"):
        build({"class": "src.kernel.echo.NoSuchClass"})


def test_build_requires_type_or_class() -> None:
    with pytest.raises(ConfigError, match="type.*class"):
        build({"prefix": "x: "})


def test_build_rejects_non_dict_spec() -> None:
    with pytest.raises(ConfigError):
        build("echo")  # type: ignore[arg-type]
