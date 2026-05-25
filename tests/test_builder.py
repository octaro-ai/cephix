"""Tests for src.builder.build_robot_from_config."""

from __future__ import annotations

import pytest

from src.builder import build_robot_from_config
from src.bus.asyncio_bus import AsyncioBus
from src.channels.websocket import WebsocketChannel
from src.kernel.echo import EchoKernel
from src.registry import ConfigError
from src.robot import Robot


def test_builder_assembles_minimum_robot() -> None:
    robot = build_robot_from_config(
        {
            "id": "x",
            "name": "X",
            "enabled": True,
            "kernel": {"type": "echo"},
        }
    )
    assert isinstance(robot, Robot)
    assert isinstance(robot.bus, AsyncioBus)
    assert isinstance(robot.kernel, EchoKernel)
    assert robot.channels == ()


def test_builder_uses_default_bus_when_missing() -> None:
    robot = build_robot_from_config({"kernel": {"type": "echo"}})
    assert isinstance(robot.bus, AsyncioBus)


def test_builder_assembles_channels() -> None:
    robot = build_robot_from_config(
        {
            "kernel": {"type": "echo"},
            "channels": [{"type": "websocket", "port": 0}],
        }
    )
    assert len(robot.channels) == 1
    assert isinstance(robot.channels[0], WebsocketChannel)


def test_builder_passes_kernel_kwargs() -> None:
    robot = build_robot_from_config(
        {"kernel": {"type": "echo", "prefix": "yo: "}}
    )
    assert robot.kernel._prefix == "yo: "  # type: ignore[attr-defined]


def test_builder_merges_defaults_with_robot_yaml() -> None:
    defaults = {
        "kernel": {"type": "echo", "prefix": "default: "},
        "channels": [{"type": "websocket", "port": 9999}],
    }
    robot_yaml = {
        "kernel": {"prefix": "override: "},
    }
    robot = build_robot_from_config(robot_yaml, defaults=defaults)
    assert robot.kernel._prefix == "override: "  # type: ignore[attr-defined]
    assert robot.channels[0]._port == 9999  # type: ignore[attr-defined]


def test_builder_robot_yaml_replaces_default_channels() -> None:
    defaults = {"channels": [{"type": "websocket", "port": 1111}]}
    robot_yaml = {
        "kernel": {"type": "echo"},
        "channels": [{"type": "websocket", "port": 2222}],
    }
    robot = build_robot_from_config(robot_yaml, defaults=defaults)
    assert len(robot.channels) == 1
    assert robot.channels[0]._port == 2222  # type: ignore[attr-defined]


def test_builder_rejects_missing_kernel() -> None:
    with pytest.raises(ConfigError, match="kernel"):
        build_robot_from_config({"id": "x"})


def test_builder_rejects_non_dict_top_level() -> None:
    with pytest.raises(ConfigError, match="mapping"):
        build_robot_from_config([])  # type: ignore[arg-type]


def test_builder_rejects_non_list_channels() -> None:
    with pytest.raises(ConfigError, match="channels"):
        build_robot_from_config(
            {"kernel": {"type": "echo"}, "channels": {"type": "websocket"}}
        )


def test_builder_propagates_identity_to_robot() -> None:
    robot = build_robot_from_config(
        {
            "id": "alpha",
            "name": "Alpha",
            "enabled": False,
            "kernel": {"type": "echo"},
        }
    )
    assert isinstance(robot, Robot)
    assert robot.robot_id == "alpha"
    assert robot.robot_name == "Alpha"


def test_builder_handles_missing_identity() -> None:
    robot = build_robot_from_config({"kernel": {"type": "echo"}})
    assert robot.robot_id is None
    assert robot.robot_name is None
