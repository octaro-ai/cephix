"""Assemble a :class:`Robot` from a ``robot.yaml`` mapping.

The builder is the bridge between configuration and runtime:

- It deep-merges global defaults from ``cephix.yaml#defaults`` with the
  bot-specific ``robot.yaml`` (bot wins).
- It resolves the ``bus``, ``kernel`` and ``channels`` blocks via the
  component registry.
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

from src.bus.ports import BusPort
from src.channels.ports import ChannelPort
from src.components import RobotComponent
from src.configuration import (
    CONTROL_PLANE_TOKEN_ENV,
    deep_merge,
    load_robot_env,
)
from src.kernel.ports import KernelPort
from src.registry import ConfigError, build
from src.robot import ControlPlaneConfig, Robot, RobotIdentity

logger = logging.getLogger(__name__)

_DEFAULT_BUS_SPEC: dict[str, Any] = {"type": "asyncio"}


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

    kernel_spec = cfg.get("kernel")
    if not kernel_spec:
        raise ConfigError("robot.yaml must declare a kernel section")
    kernel = build(kernel_spec)
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

    components: list[RobotComponent] = [bus, kernel, *channels]

    return Robot(
        identity=identity,
        components=components,
        control_plane_config=control_plane_config,
        control_plane_token=control_plane_token,
    )


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
