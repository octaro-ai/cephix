"""Assemble a :class:`Robot` from a ``robot.yaml`` mapping.

The builder is the bridge between configuration and runtime:

- It deep-merges global defaults from ``cephix.yaml#defaults`` with the
  bot-specific ``robot.yaml`` (bot wins).
- It resolves the ``bus``, ``kernel`` and ``channels`` blocks via the
  component registry.
- It propagates the bot's identity (``id``, ``name``) into the
  ``Robot`` so its lifecycle log narrates which bot is starting and
  stopping.

The ``enabled`` flag stays out of the builder: it's a CLI-layer
filter for the smart-default and the future ``--all`` filter, and has
no runtime meaning once a bot is actually being started.
"""

from __future__ import annotations

import logging
from typing import Any

from src.bus.ports import BusPort
from src.channels.ports import ChannelPort
from src.configuration import deep_merge
from src.kernel.ports import KernelPort
from src.registry import ConfigError, build
from src.robot import Robot

logger = logging.getLogger(__name__)

_DEFAULT_BUS_SPEC: dict[str, Any] = {"type": "asyncio"}


def build_robot_from_config(
    robot_yaml: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
) -> Robot:
    """Build a :class:`Robot` from a parsed ``robot.yaml`` mapping.

    ``defaults`` typically comes from
    :func:`src.configuration.home_defaults`. The merge is deep, with
    ``robot_yaml`` winning on conflicts. Channel lists are not merged
    element-by-element: if ``robot_yaml`` provides a ``channels`` list,
    it fully replaces the default list.
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

    kernel_spec = cfg.get("kernel")
    if not kernel_spec:
        raise ConfigError("robot.yaml must declare a kernel section")
    kernel = build(kernel_spec)
    if not isinstance(kernel, KernelPort):
        raise ConfigError(
            f"kernel component {type(kernel).__name__} does not implement KernelPort"
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
        channels.append(component)

    robot_id_raw = robot_yaml.get("id")
    robot_name_raw = robot_yaml.get("name")
    return Robot(
        bus=bus,
        kernel=kernel,
        channels=channels,
        robot_id=str(robot_id_raw) if robot_id_raw else None,
        robot_name=str(robot_name_raw) if robot_name_raw else None,
    )
