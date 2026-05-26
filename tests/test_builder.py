"""Tests for src.builder.build_robot_from_config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.builder import build_robot_from_config
from src.bus.asyncio_bus import AsyncioBus
from src.channels.websocket import WebsocketChannel
from src.kernel.echo import EchoKernel
from src.registry import ConfigError
from src.robot import Robot

# control plane is not what these tests are about; force it off so the
# builder produces a robot that won't try to bind ports if anyone
# actually starts it.
_CP_OFF: dict[str, Any] = {"control_plane": {"enabled": False}}


def _cfg(extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(_CP_OFF)
    merged.update(extra)
    return merged


def _channels_of(robot: Robot) -> tuple[WebsocketChannel, ...]:
    """Pull the WebsocketChannel(s) out of robot.components."""
    return tuple(
        c for c in robot.components if isinstance(c, WebsocketChannel)
    )


def _bus_of(robot: Robot) -> AsyncioBus:
    bus = robot.components[0]
    assert isinstance(bus, AsyncioBus)
    return bus


def _kernel_of(robot: Robot) -> EchoKernel:
    for c in robot.components:
        if isinstance(c, EchoKernel):
            return c
    raise AssertionError("no EchoKernel in robot.components")


def test_builder_assembles_minimum_robot() -> None:
    robot = build_robot_from_config(
        _cfg(
            {
                "id": "x",
                "name": "X",
                "enabled": True,
                "kernel": {"type": "echo"},
            }
        )
    )
    assert isinstance(robot, Robot)
    assert isinstance(_bus_of(robot), AsyncioBus)
    assert isinstance(_kernel_of(robot), EchoKernel)
    assert _channels_of(robot) == ()


def test_builder_uses_default_bus_when_missing() -> None:
    robot = build_robot_from_config(_cfg({"kernel": {"type": "echo"}}))
    assert isinstance(_bus_of(robot), AsyncioBus)


def test_builder_assembles_channels() -> None:
    robot = build_robot_from_config(
        _cfg(
            {
                "kernel": {"type": "echo"},
                "channels": [{"type": "websocket", "port": 0}],
            }
        )
    )
    channels = _channels_of(robot)
    assert len(channels) == 1
    assert isinstance(channels[0], WebsocketChannel)


def test_builder_passes_kernel_kwargs() -> None:
    robot = build_robot_from_config(
        _cfg({"kernel": {"type": "echo", "prefix": "yo: "}})
    )
    kernel = _kernel_of(robot)
    assert kernel._prefix == "yo: "  # type: ignore[attr-defined]


def test_builder_merges_defaults_with_robot_yaml() -> None:
    defaults = {
        "kernel": {"type": "echo", "prefix": "default: "},
        "channels": [{"type": "websocket", "port": 9999}],
        "control_plane": {"enabled": False},
    }
    robot_yaml = {
        "kernel": {"prefix": "override: "},
    }
    robot = build_robot_from_config(robot_yaml, defaults=defaults)
    kernel = _kernel_of(robot)
    channels = _channels_of(robot)
    assert kernel._prefix == "override: "  # type: ignore[attr-defined]
    assert channels[0]._port == 9999  # type: ignore[attr-defined]


def test_builder_robot_yaml_replaces_default_channels() -> None:
    defaults = {
        "channels": [{"type": "websocket", "port": 1111}],
        "control_plane": {"enabled": False},
    }
    robot_yaml = {
        "kernel": {"type": "echo"},
        "channels": [{"type": "websocket", "port": 2222}],
    }
    robot = build_robot_from_config(robot_yaml, defaults=defaults)
    channels = _channels_of(robot)
    assert len(channels) == 1
    assert channels[0]._port == 2222  # type: ignore[attr-defined]


def test_builder_rejects_missing_kernel() -> None:
    with pytest.raises(ConfigError, match="kernel"):
        build_robot_from_config(_cfg({"id": "x"}))


def test_builder_rejects_non_dict_top_level() -> None:
    with pytest.raises(ConfigError, match="mapping"):
        build_robot_from_config([])  # type: ignore[arg-type]


def test_builder_rejects_non_list_channels() -> None:
    with pytest.raises(ConfigError, match="channels"):
        build_robot_from_config(
            _cfg({"kernel": {"type": "echo"}, "channels": {"type": "websocket"}})
        )


def test_builder_propagates_identity_to_robot() -> None:
    robot = build_robot_from_config(
        _cfg(
            {
                "id": "alpha",
                "name": "Alpha",
                "enabled": False,
                "kernel": {"type": "echo"},
            }
        )
    )
    assert isinstance(robot, Robot)
    assert robot.identity.id == "alpha"
    assert robot.identity.name == "Alpha"


def test_builder_handles_missing_identity() -> None:
    robot = build_robot_from_config(_cfg({"kernel": {"type": "echo"}}))
    assert robot.identity.id is None
    assert robot.identity.name is None


def test_builder_loads_control_plane_token_from_workspace_env(
    tmp_path: Path,
) -> None:
    """If a workspace is given, the builder reads the .env for the token."""
    (tmp_path / ".env").write_text(
        "CEPHIX_CONTROL_PLANE_TOKEN=secret-token-xyz\n",
        encoding="utf-8",
    )
    robot = build_robot_from_config(
        _cfg({"id": "x", "kernel": {"type": "echo"}}),
        workspace=tmp_path,
    )
    assert robot._control_plane_token == "secret-token-xyz"  # type: ignore[attr-defined]


def test_builder_control_plane_config_overrides() -> None:
    robot = build_robot_from_config(
        {
            "kernel": {"type": "echo"},
            "control_plane": {
                "enabled": False,
                "host": "127.0.0.1",
                "port": 12345,
                "port_range": [12345, 12399],
                "path": "/admin",
            },
        }
    )
    cfg = robot._control_plane_config  # type: ignore[attr-defined]
    assert cfg.enabled is False
    assert cfg.port == 12345
    assert cfg.port_range == (12345, 12399)
    assert cfg.path == "/admin"


def test_builder_rejects_bad_port_range() -> None:
    with pytest.raises(ConfigError, match="port_range"):
        build_robot_from_config(
            {
                "kernel": {"type": "echo"},
                "control_plane": {"port_range": [9999, 1000]},
            }
        )
