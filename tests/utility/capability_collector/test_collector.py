"""Tests for :class:`CapabilityCollector`.

The collector aggregates the commands each component **self-announces**
via its :class:`ComponentLifecycle` (``ready``/``warn`` adds, ``shutdown``/
``failure`` retracts) into a retained :class:`HarnessCapabilities`.
"""

from __future__ import annotations

import asyncio

import src.bus  # prime package init (avoids partial-init import cycles)
from src.bus import (
    HARNESS_CAPABILITIES_TOPIC,
    AsyncioBus,
    ComponentInfo,
    ComponentLifecycle,
    HarnessCapabilities,
    component_lifecycle_topic,
)
from src.utility.capability_collector import CapabilityCollector


def _info(name: str, *, commands: list[dict] | None = None) -> ComponentInfo:
    metadata: dict = {}
    if commands is not None:
        metadata["provides_commands"] = commands
    return ComponentInfo(category="kernel", name=name, metadata=metadata)


def _lifecycle(
    name: str, *, phase: str = "ready", commands: list[dict] | None = None
) -> ComponentLifecycle:
    return ComponentLifecycle(
        topic=component_lifecycle_topic(name),
        principal="component:" + name,
        source=name,
        run_id="",
        phase=phase,
        info=_info(name, commands=commands),
    )


async def _capture(bus: AsyncioBus) -> list[HarnessCapabilities]:
    received: list[HarnessCapabilities] = []

    async def handler(event) -> None:
        if isinstance(event, HarnessCapabilities):
            received.append(event)

    bus.subscribe_broadcast(HARNESS_CAPABILITIES_TOPIC, handler)
    return received


async def test_publishes_commands_from_component_ready() -> None:
    bus = AsyncioBus()
    collector = CapabilityCollector()
    received = await _capture(bus)

    await bus.start()
    try:
        # Command-providing components boot *after* the collector, so
        # their self-announced ready is caught live.
        await collector.start(bus)
        await bus.publish_broadcast(
            _lifecycle("kernel", commands=[{"action": "chat.session.new"}]),
            retain=True,
        )
        await asyncio.sleep(0.03)
    finally:
        await collector.stop()
        await bus.stop()

    assert len(received) == 1
    assert received[0].commands == ({"action": "chat.session.new"},)


async def test_manifest_is_retained_for_late_subscriber() -> None:
    bus = AsyncioBus()
    collector = CapabilityCollector()

    await bus.start()
    try:
        await collector.start(bus)
        await bus.publish_broadcast(
            _lifecycle("kernel", commands=[{"action": "a"}]), retain=True
        )
        await asyncio.sleep(0.03)

        # A channel that connects *after* the manifest was built still
        # gets it from the retained slot.
        received = await _capture(bus)
        await asyncio.sleep(0.03)
    finally:
        await collector.stop()
        await bus.stop()

    assert len(received) == 1
    assert received[0].commands == ({"action": "a"},)


async def test_ordering_is_first_seen() -> None:
    bus = AsyncioBus()
    collector = CapabilityCollector()
    received = await _capture(bus)

    await bus.start()
    try:
        await collector.start(bus)
        await bus.publish_broadcast(
            _lifecycle("first", commands=[{"action": "one"}, {"action": "two"}]),
            retain=True,
        )
        await asyncio.sleep(0.02)
        await bus.publish_broadcast(
            _lifecycle("second", commands=[{"action": "three"}]), retain=True
        )
        await asyncio.sleep(0.02)
    finally:
        await collector.stop()
        await bus.stop()

    actions = [c["action"] for c in received[-1].commands]
    assert actions == ["one", "two", "three"]


async def test_identical_manifest_not_republished() -> None:
    bus = AsyncioBus()
    collector = CapabilityCollector()
    received = await _capture(bus)

    await bus.start()
    try:
        await collector.start(bus)
        await bus.publish_broadcast(
            _lifecycle("kernel", commands=[{"action": "a"}]), retain=True
        )
        await asyncio.sleep(0.02)
        # A second ready with the same commands -> no second publish.
        await bus.publish_broadcast(
            _lifecycle("kernel", phase="ready", commands=[{"action": "a"}]),
            retain=True,
        )
        await asyncio.sleep(0.02)
    finally:
        await collector.stop()
        await bus.stop()

    assert len(received) == 1


async def test_changed_manifest_is_republished() -> None:
    bus = AsyncioBus()
    collector = CapabilityCollector()
    received = await _capture(bus)

    await bus.start()
    try:
        await collector.start(bus)
        await bus.publish_broadcast(
            _lifecycle("kernel", commands=[{"action": "a"}]), retain=True
        )
        await asyncio.sleep(0.02)
        await bus.publish_broadcast(
            _lifecycle("kernel", commands=[{"action": "a"}, {"action": "b"}]),
            retain=True,
        )
        await asyncio.sleep(0.02)
    finally:
        await collector.stop()
        await bus.stop()

    assert len(received) == 2
    assert [c["action"] for c in received[-1].commands] == ["a", "b"]


async def test_shutdown_retracts_commands() -> None:
    bus = AsyncioBus()
    collector = CapabilityCollector()
    received = await _capture(bus)

    await bus.start()
    try:
        await collector.start(bus)
        await bus.publish_broadcast(
            _lifecycle("kernel", commands=[{"action": "a"}]), retain=True
        )
        await asyncio.sleep(0.02)
        # The kernel goes offline cleanly -> its capability is retracted.
        await bus.publish_broadcast(
            _lifecycle("kernel", phase="shutdown"), retain=True
        )
        await asyncio.sleep(0.02)
    finally:
        await collector.stop()
        await bus.stop()

    assert len(received) == 2
    assert received[-1].commands == ()


async def test_failure_retracts_commands() -> None:
    bus = AsyncioBus()
    collector = CapabilityCollector()
    received = await _capture(bus)

    await bus.start()
    try:
        await collector.start(bus)
        await bus.publish_broadcast(
            _lifecycle("kernel", commands=[{"action": "a"}]), retain=True
        )
        await asyncio.sleep(0.02)
        # A crash bridged by the owner as failure also retracts.
        await bus.publish_broadcast(
            _lifecycle("kernel", phase="failure"), retain=True
        )
        await asyncio.sleep(0.02)
    finally:
        await collector.stop()
        await bus.stop()

    assert received[-1].commands == ()


async def test_command_free_component_does_not_publish() -> None:
    bus = AsyncioBus()
    collector = CapabilityCollector()
    received = await _capture(bus)

    await bus.start()
    try:
        await collector.start(bus)
        # An observer with no commands announces ready: nothing to add.
        await bus.publish_broadcast(_lifecycle("audit"), retain=True)
        await asyncio.sleep(0.02)
    finally:
        await collector.stop()
        await bus.stop()

    assert received == []
