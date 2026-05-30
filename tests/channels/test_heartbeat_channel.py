"""Tests for :class:`HeartbeatChannel`.

Three groups:

- identity + parsing (CHANNEL category, parser accepts good
  configs, rejects malformed ones per-entry without breaking the
  rest, principal defaulting works);
- emit builders (``component_request`` and ``robot_input`` events
  carry the right fields from the YAML);
- live tick on the bus (with a fast cron, one publish per tick,
  fire-and-forget -- no response awaited).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import src.bus  # noqa: F401

from src.bus.asyncio_bus import AsyncioBus
from src.bus.messages import ComponentRequest, RobotEvent, RobotInput
from src.channels.heartbeat import (
    EmitType,
    HeartbeatChannel,
    HeartbeatConfig,
)
from src.components import ComponentCategory
from src.utility.config_store.ports import ConfigStorePort


class _FakeConfigStore(ConfigStorePort):
    """In-memory config store for tests."""

    component_name = "config-store"
    instance_id = "fake-store-1"

    def __init__(self, data: dict[str, list[dict[str, Any]]]) -> None:
        self._data = data

    def configs(self, key: str) -> list[dict[str, Any]]:
        return list(self._data.get(key, []))

    async def refresh(self) -> None:
        return None


def _make_channel(
    entries: list[dict[str, Any]] | None = None,
    *,
    default_principal: str = "robot:heartbeat",
) -> HeartbeatChannel:
    store = _FakeConfigStore({"heartbeats": entries or []})
    return HeartbeatChannel(
        config_store=store, default_principal=default_principal
    )


# ---- identity ----------------------------------------------------------------


def test_metadata() -> None:
    assert HeartbeatChannel.component_name == "heartbeat"
    assert HeartbeatChannel.component_category is ComponentCategory.CHANNEL


def test_rejects_non_port_store() -> None:
    with pytest.raises(TypeError):
        HeartbeatChannel(config_store=object())  # type: ignore[arg-type]


def test_rejects_empty_principal() -> None:
    store = _FakeConfigStore({"heartbeats": []})
    with pytest.raises(ValueError):
        HeartbeatChannel(config_store=store, default_principal="")


# ---- parsing -----------------------------------------------------------------


def _parse_one(channel: HeartbeatChannel, raw: dict[str, Any]) -> HeartbeatConfig:
    return channel._parse_entry(raw)


def test_parse_valid_component_request_entry() -> None:
    channel = _make_channel()
    cfg = _parse_one(
        channel,
        {
            "id": "mail-poll",
            "cron": "*/5 * * * *",
            "emit": {
                "type": "component_request",
                "topic": "tool.invoke",
                "action": "mailbox.fetch_unread",
                "payload": {"limit": 5},
            },
        },
    )
    assert cfg.id == "mail-poll"
    assert cfg.emit_type is EmitType.COMPONENT_REQUEST
    assert cfg.emit["principal"] == "robot:heartbeat"  # filled from default


def test_explicit_principal_overrides_default() -> None:
    channel = _make_channel(default_principal="robot:heartbeat")
    cfg = _parse_one(
        channel,
        {
            "id": "summary",
            "cron": "0 8 * * *",
            "emit": {
                "type": "robot_input",
                "topic": "input.heartbeat",
                "message": "summary time",
                "principal": "user:owner",
            },
        },
    )
    assert cfg.emit["principal"] == "user:owner"


def test_parse_rejects_missing_id() -> None:
    channel = _make_channel()
    with pytest.raises(ValueError):
        _parse_one(channel, {"cron": "* * * * *", "emit": {}})


def test_parse_rejects_invalid_cron() -> None:
    channel = _make_channel()
    with pytest.raises(ValueError):
        _parse_one(
            channel,
            {
                "id": "x",
                "cron": "not a cron",
                "emit": {"type": "component_request", "topic": "t", "action": "a"},
            },
        )


def test_parse_rejects_unknown_emit_type() -> None:
    channel = _make_channel()
    with pytest.raises(ValueError):
        _parse_one(
            channel,
            {
                "id": "x",
                "cron": "* * * * *",
                "emit": {"type": "telegram", "topic": "t"},
            },
        )


def test_build_rejects_component_request_without_action() -> None:
    channel = _make_channel()
    cfg_no_action = HeartbeatConfig(
        id="x",
        cron="* * * * *",
        emit_type=EmitType.COMPONENT_REQUEST,
        emit={"topic": "tool.invoke", "principal": "robot:heartbeat"},
    )
    with pytest.raises(ValueError):
        channel._build_component_request(cfg_no_action, run_id="r-1")


def test_bad_entries_do_not_block_good_ones() -> None:
    channel = _make_channel(
        [
            {"this is": "broken"},
            {
                "id": "good",
                "cron": "* * * * *",
                "emit": {
                    "type": "component_request",
                    "topic": "tool.invoke",
                    "action": "x",
                },
            },
        ]
    )
    parsed = channel._parse_entries(channel._config_store.configs("heartbeats"))
    assert len(parsed) == 1
    assert parsed[0].id == "good"


# ---- emit builders -----------------------------------------------------------


def test_build_component_request_event() -> None:
    channel = _make_channel()
    cfg = HeartbeatConfig(
        id="mp",
        cron="* * * * *",
        emit_type=EmitType.COMPONENT_REQUEST,
        emit={
            "type": "component_request",
            "topic": "tool.invoke",
            "action": "mailbox.fetch_unread",
            "payload": {"limit": 5},
            "principal": "robot:heartbeat",
        },
    )
    event = channel._build_component_request(cfg, run_id="hb-mp-00000001")
    assert isinstance(event, ComponentRequest)
    assert event.topic == "tool.invoke"
    assert event.action == "mailbox.fetch_unread"
    assert event.payload == {"limit": 5}
    assert event.principal == "robot:heartbeat"
    assert event.source == "heartbeat"
    assert event.run_id == "hb-mp-00000001"
    assert event.correlation_id is not None
    assert event.correlation_id.startswith("hb-mp-")


def test_build_robot_input_event() -> None:
    channel = _make_channel()
    cfg = HeartbeatConfig(
        id="summary",
        cron="0 8 * * *",
        emit_type=EmitType.ROBOT_INPUT,
        emit={
            "type": "robot_input",
            "topic": "input.heartbeat",
            "message": "summary time",
            "payload": {"flag": True},
            "principal": "robot:heartbeat",
        },
    )
    event = channel._build_robot_input(cfg, run_id="hb-summary-00000001")
    assert isinstance(event, RobotInput)
    assert event.topic == "input.heartbeat"
    assert event.message == "summary time"
    assert event.payload == {"flag": True}
    assert event.principal == "robot:heartbeat"
    assert event.source == "heartbeat"
    assert event.run_id == "hb-summary-00000001"


# ---- live tick on the bus ---------------------------------------------------


async def test_fire_publishes_to_bus_fire_and_forget() -> None:
    """``_fire`` puts one event on the configured topic and does not wait.

    The cron loop is bypassed entirely so the test is deterministic.
    Driving ``_fire`` directly tests the only path that matters --
    build event, publish, return immediately -- without depending
    on wall-clock timing.
    """
    bus = AsyncioBus()
    await bus.start()
    received: list[RobotEvent] = []

    async def _capture(event: RobotEvent) -> None:
        received.append(event)

    bus.subscribe("tool.invoke", _capture)

    cfg = HeartbeatConfig(
        id="fast",
        cron="* * * * *",
        emit_type=EmitType.COMPONENT_REQUEST,
        emit={
            "type": "component_request",
            "topic": "tool.invoke",
            "action": "ping",
            "principal": "robot:heartbeat",
        },
    )
    channel = HeartbeatChannel(
        config_store=_FakeConfigStore({"heartbeats": []}),
        default_principal="robot:heartbeat",
    )
    channel._bus = bus
    try:
        await channel._fire(cfg, tick=1)
        # Give the bus a chance to deliver to the subscriber.
        for _ in range(20):
            await asyncio.sleep(0.01)
            if received:
                break
        assert received, "expected one ComponentRequest on tool.invoke"
        first = received[0]
        assert isinstance(first, ComponentRequest)
        assert first.action == "ping"
        assert first.source == "heartbeat"
        assert first.run_id == "hb-fast-00000001"
    finally:
        channel._bus = None
        await bus.stop()
