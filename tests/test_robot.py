"""Tests for the consolidated Robot class."""

from __future__ import annotations

import asyncio
import logging

import pytest

from src.bus import AsyncioBus
from src.components import BusComponent, ComponentCategory, RobotComponent
from src.robot import ControlPlaneConfig, Robot, RobotIdentity


class _RecordingComponent(BusComponent):
    """Userspace component used to verify lifecycle ordering and bus injection."""

    component_description = "test fixture"

    def __init__(
        self,
        name: str,
        log: list[str],
        *,
        category: ComponentCategory = ComponentCategory.CHANNEL,
    ) -> None:
        # Per-instance metadata so the manifest carries unique types
        # (the registry never sees these fakes; we just need uniqueness).
        self.component_name = name
        self.component_category = category
        self.name = name
        self._log = log
        self.fail_on_start = False
        self.fail_on_stop = False
        self.started = False
        self.injected_bus: object | None = None

    async def start(self, bus: object) -> None:
        if self.fail_on_start:
            self._log.append(f"start-fail:{self.name}")
            raise RuntimeError(f"start failed: {self.name}")
        self._log.append(f"start:{self.name}")
        self.injected_bus = bus
        self.started = True

    async def stop(self) -> None:
        if self.fail_on_stop:
            self._log.append(f"stop-fail:{self.name}")
            raise RuntimeError(f"stop failed: {self.name}")
        self._log.append(f"stop:{self.name}")
        self.started = False
        self.injected_bus = None


def _make_robot(
    *,
    bus: AsyncioBus | None = None,
    kernel: _RecordingComponent | None = None,
    channels: list[_RecordingComponent] | None = None,
    robot_id: str | None = None,
    robot_name: str | None = None,
    shutdown_grace: float = 0.0,
) -> Robot:
    """Build a Robot with control plane disabled (the default for tests)."""
    bus = bus if bus is not None else AsyncioBus()
    log_proxy: list[str] = []
    kernel = (
        kernel
        if kernel is not None
        else _RecordingComponent("kernel", log_proxy, category=ComponentCategory.KERNEL)
    )
    channels = channels if channels is not None else []
    components: list[RobotComponent] = [bus, kernel, *channels]
    return Robot(
        identity=RobotIdentity(id=robot_id, name=robot_name),
        components=components,
        control_plane_config=ControlPlaneConfig(enabled=False),
        shutdown_grace=shutdown_grace,
    )


def test_component_manifest_roster_is_capability_free() -> None:
    """The roster is pure "who exists" -- no commands leak into it.

    Capabilities are component-driven and travel on each component's
    self-published ``ComponentLifecycle`` (aggregated by the
    CapabilityCollector), not on the boot roster.
    """
    from src.command import CommandSpec

    log: list[str] = []
    kernel = _RecordingComponent("kernel", log, category=ComponentCategory.KERNEL)
    kernel.provides_commands = (
        CommandSpec(action="chat.session.new", handler="cmd_new", label="New chat"),
    )
    robot = _make_robot(kernel=kernel)

    manifest = {info.name: info for info in robot.component_manifest}
    assert all(
        "provides_commands" not in info.metadata for info in manifest.values()
    )


def test_component_info_serializes_provides_commands() -> None:
    from src.command import CommandSpec

    log: list[str] = []
    kernel = _RecordingComponent("kernel", log, category=ComponentCategory.KERNEL)
    kernel.provides_commands = (
        CommandSpec(action="chat.session.new", handler="cmd_new", label="New chat"),
    )

    info = kernel.component_info()
    entries = info.metadata["provides_commands"]
    assert entries[0]["action"] == "chat.session.new"
    assert entries[0]["owner_component"] == "kernel"
    assert entries[0]["owner_instance_id"] == kernel.instance_id


async def test_robot_starts_bus_kernel_then_channels_in_priority_order() -> None:
    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingComponent("kernel", log, category=ComponentCategory.KERNEL)
    ch_a = _RecordingComponent("ch-a", log)
    ch_b = _RecordingComponent("ch-b", log)

    robot = _make_robot(bus=bus, kernel=kernel, channels=[ch_a, ch_b])

    async with robot:
        assert bus.is_running
        assert kernel.started
        assert ch_a.started and ch_b.started

    assert not bus.is_running
    assert not kernel.started
    assert not ch_a.started and not ch_b.started

    start_order = [entry for entry in log if entry.startswith("start:")]
    stop_order = [entry for entry in log if entry.startswith("stop:")]
    assert start_order == ["start:kernel", "start:ch-a", "start:ch-b"]
    assert stop_order == ["stop:ch-b", "stop:ch-a", "stop:kernel"]


async def test_robot_rolls_back_on_partial_startup() -> None:
    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingComponent("kernel", log, category=ComponentCategory.KERNEL)
    ch_a = _RecordingComponent("ch-a", log)
    ch_bad = _RecordingComponent("ch-bad", log)
    ch_bad.fail_on_start = True

    robot = _make_robot(bus=bus, kernel=kernel, channels=[ch_a, ch_bad])

    with pytest.raises(RuntimeError, match="ch-bad"):
        await robot.start()

    assert not bus.is_running
    assert not kernel.started
    assert not ch_a.started

    assert log == [
        "start:kernel",
        "start:ch-a",
        "start-fail:ch-bad",
        "stop:ch-a",
        "stop:kernel",
    ]


async def test_robot_run_forever_blocks_until_stop() -> None:
    robot = _make_robot()

    await robot.start()
    runner = asyncio.create_task(robot.run_forever())

    await asyncio.sleep(0.01)
    assert not runner.done()

    await robot.stop()
    await asyncio.wait_for(runner, timeout=1.0)


async def test_robot_injects_bus_into_components_at_start() -> None:
    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingComponent("kernel", log, category=ComponentCategory.KERNEL)
    ch = _RecordingComponent("ch", log)

    robot = _make_robot(bus=bus, kernel=kernel, channels=[ch])

    assert kernel.injected_bus is None
    assert ch.injected_bus is None

    async with robot:
        assert kernel.injected_bus is bus
        assert ch.injected_bus is bus

    assert kernel.injected_bus is None
    assert ch.injected_bus is None


async def test_robot_exposes_components_in_boot_order() -> None:
    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingComponent("kernel", log, category=ComponentCategory.KERNEL)
    ch = _RecordingComponent("ch", log)

    robot = _make_robot(bus=bus, kernel=kernel, channels=[ch])

    # Before start: components are configured but the bus pointer is
    # not active yet -- robot.bus reflects the *running* bus.
    assert robot.components == (bus, kernel, ch)
    assert robot.bus is None

    async with robot:
        assert robot.bus is bus

    assert robot.bus is None


async def test_robot_logs_boot_and_shutdown_narrative(
    caplog: pytest.LogCaptureFixture,
) -> None:
    robot = _make_robot()

    with caplog.at_level(logging.INFO, logger="src.robot"):
        async with robot:
            pass

    messages = [rec.message for rec in caplog.records if rec.name == "src.robot"]
    assert "starting..." in messages
    # Component log lines now carry the per-instance id in
    # parentheses ("AsyncioBus (a3f7c2b1d4e9) started"), so the
    # narrative assertions check for the prefix instead of the
    # exact line. The id itself is verified by the dedicated
    # test_robot_logs_include_instance_ids below.
    assert any(m.startswith("AsyncioBus ") and m.endswith("started") for m in messages)
    assert any(m.startswith("_RecordingComponent ") and m.endswith("attached") for m in messages)
    assert "robot online (Ctrl-C to stop)" in messages
    assert any(m.startswith("_RecordingComponent ") and m.endswith("detached") for m in messages)
    assert any(m.startswith("AsyncioBus ") and m.endswith("stopped") for m in messages)
    assert "robot offline" in messages

    online_idx = messages.index("robot online (Ctrl-C to stop)")
    offline_idx = messages.index("robot offline")
    assert online_idx < offline_idx

    # Boot-level "Entering" markers introduce every category that
    # boots. The matching closing markers (``... complete``,
    # ``Leaving ...``, ``shutdown complete``) are intentionally
    # silenced for readability -- the next "Entering" line and the
    # final "robot offline" already bracket the section. The robot
    # under test has BUS (priority 1) and KERNEL (priority 11).
    assert any("Entering Boot Level 1 (BUS)" in m for m in messages)
    assert any("Entering Boot Level 11 (KERNEL)" in m for m in messages)
    # Closing markers are off by convention; assert they stay quiet.
    assert not any("Boot Level 1 (BUS) complete" in m for m in messages)
    assert not any("Leaving Boot Level 1 (BUS)" in m for m in messages)
    assert not any("Boot Level 1 (BUS) shutdown complete" in m for m in messages)


async def test_robot_uses_symmetric_boot_and_shutdown_verbs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``BusComponent`` -> attached/detached, plain -> started/stopped.

    Asymmetric pairs ("started" but never "stopped" on shutdown)
    confuse a log reader scanning a restart loop. The verb a
    component uses on boot must mirror the verb on shutdown.
    """

    class _PlainActorLike(RobotComponent):
        component_name = "plain-actor"
        component_category = ComponentCategory.ACTOR

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    bus = AsyncioBus()
    plain = _PlainActorLike()
    bus_attached = _RecordingComponent(
        "ws", [], category=ComponentCategory.CHANNEL
    )

    robot = Robot(
        identity=RobotIdentity(),
        components=[bus, plain, bus_attached],
        control_plane_config=ControlPlaneConfig(enabled=False),
        shutdown_grace=0.0,
    )

    with caplog.at_level(logging.INFO, logger="src.robot"):
        async with robot:
            pass

    messages = [rec.message for rec in caplog.records if rec.name == "src.robot"]

    # Plain RobotComponent: ``started`` on boot, ``stopped`` on shutdown.
    assert any(m.startswith("AsyncioBus ") and m.endswith("started") for m in messages)
    assert any(m.startswith("AsyncioBus ") and m.endswith("stopped") for m in messages)
    assert not any(m.startswith("AsyncioBus ") and m.endswith("detached") for m in messages)
    assert any(m.startswith("_PlainActorLike ") and m.endswith("started") for m in messages)
    assert any(m.startswith("_PlainActorLike ") and m.endswith("stopped") for m in messages)
    assert not any(m.startswith("_PlainActorLike ") and m.endswith("detached") for m in messages)

    # BusComponent: ``attached`` on boot, ``detached`` on shutdown.
    assert any(m.startswith("_RecordingComponent ") and m.endswith("attached") for m in messages)
    assert any(m.startswith("_RecordingComponent ") and m.endswith("detached") for m in messages)
    assert not any(m.startswith("_RecordingComponent ") and m.endswith("stopped") for m in messages)


async def test_robot_log_lines_include_component_instance_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each component log line carries its 12-char instance id.

    Without the suffix two ``BaseKernel`` instances would log the
    same line. The test asserts the format
    ``<ClassName> (<id>) <verb>`` is consistently used both on
    boot and on shutdown.
    """
    import re

    robot = _make_robot()

    with caplog.at_level(logging.INFO, logger="src.robot"):
        async with robot:
            pass

    messages = [rec.message for rec in caplog.records if rec.name == "src.robot"]
    pattern = re.compile(
        r"^(AsyncioBus|_RecordingComponent) "
        r"\(([0-9a-f]{12})\) "
        r"(started|attached|stopped|detached)$"
    )
    matched = [m for m in messages if pattern.match(m)]
    # Two components, two start lines + two stop lines = four matches.
    assert len(matched) == 4
    # Each line's id round-trips: same 12 hex chars in the same shape.
    for m in matched:
        match = pattern.match(m)
        assert match is not None
        assert len(match.group(2)) == 12


async def test_robot_logs_lifecycle_with_identity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    robot = _make_robot(robot_id="dreamgirl", robot_name="Dreamgirl")

    with caplog.at_level(logging.INFO, logger="src.robot"):
        async with robot:
            pass

    messages = [rec.message for rec in caplog.records if rec.name == "src.robot"]
    assert "starting robot 'Dreamgirl' (dreamgirl)..." in messages
    assert "robot 'Dreamgirl' (dreamgirl) online (Ctrl-C to stop)" in messages
    assert "robot 'Dreamgirl' (dreamgirl) offline" in messages
    assert "starting..." not in messages
    assert "robot online (Ctrl-C to stop)" not in messages
    assert "robot offline" not in messages


async def test_robot_publishes_lifecycle_boot_then_ready_with_manifest() -> None:
    """The robot announces itself with retained ``boot`` then ``ready`` lifecycle events.

    The first event (``phase="boot"``) is published before the
    kernel and channels attach, so they can pick up identity from
    the retained slot. The second event (``phase="ready"``)
    overrides the retained slot once everything is up; it carries
    the same manifest so a late subscriber still learns the full
    composition.
    """
    from src.bus import LIFECYCLE_TOPIC, RobotLifecycle

    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingComponent("kernel", log, category=ComponentCategory.KERNEL)
    ch = _RecordingComponent("ch", log)

    robot = _make_robot(
        bus=bus, kernel=kernel, channels=[ch], robot_id="alpha", robot_name="Alpha"
    )

    async with robot:
        retained = bus.retained(LIFECYCLE_TOPIC)
        assert isinstance(retained, RobotLifecycle)
        assert retained.phase == "ready"
        assert retained.robot_id == "alpha"
        assert retained.robot_name == "Alpha"
        assert retained.boot_id.startswith("boot-")
        assert retained.run_id == retained.boot_id
        categories = [info.category for info in retained.components]
        assert categories == ["bus", "kernel", "channel"]


async def test_robot_publishes_lifecycle_shutdown_on_stop() -> None:
    """The robot announces a retained ``shutdown`` lifecycle event before it tears down."""
    from src.bus import LIFECYCLE_TOPIC, RobotEvent, RobotLifecycle

    received: list[RobotEvent] = []

    bus = AsyncioBus()
    robot = _make_robot(bus=bus, robot_id="alpha", robot_name="Alpha")

    await robot.start()
    bus.subscribe_broadcast(LIFECYCLE_TOPIC, lambda evt: _record(received, evt))
    await asyncio.sleep(0)  # let the consumer drain the retained ``ready`` event
    received.clear()
    await robot.stop()
    await asyncio.sleep(0)

    shutdowns = [
        evt
        for evt in received
        if isinstance(evt, RobotLifecycle) and evt.phase == "shutdown"
    ]
    assert len(shutdowns) == 1
    assert shutdowns[0].robot_id == "alpha"
    assert shutdowns[0].message == "Robot shutting down"


async def test_robot_publishes_lifecycle_shutdown_with_custom_message() -> None:
    """Operators can override the shutdown message via ``stop(message=...)``."""
    from src.bus import LIFECYCLE_TOPIC, RobotEvent, RobotLifecycle

    received: list[RobotEvent] = []

    bus = AsyncioBus()
    robot = _make_robot(bus=bus, robot_id="alpha", robot_name="Alpha")

    await robot.start()
    bus.subscribe_broadcast(LIFECYCLE_TOPIC, lambda evt: _record(received, evt))
    await asyncio.sleep(0)
    received.clear()
    await robot.stop(message="Maintenance window 03:00")
    await asyncio.sleep(0)

    shutdowns = [
        evt
        for evt in received
        if isinstance(evt, RobotLifecycle) and evt.phase == "shutdown"
    ]
    assert len(shutdowns) == 1
    assert shutdowns[0].message == "Maintenance window 03:00"


async def _record(target: list, event) -> None:  # type: ignore[no-untyped-def]
    target.append(event)


async def test_robot_does_not_block_shutdown_for_default_drainers() -> None:
    """Default drain() returns immediately -> no waste of the grace window."""
    import time

    robot = _make_robot(shutdown_grace=5.0)  # would be visible if blind sleep

    await robot.start()
    started = time.monotonic()
    await robot.stop()
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, f"shutdown took {elapsed:.2f}s with default drains"


async def test_robot_caps_shutdown_at_grace_when_drain_hangs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A drain() that doesn't return is capped at the per-component grace."""

    class _SilentDrainer(BusComponent):
        component_name = "silent-drainer"
        component_category = ComponentCategory.CHANNEL
        component_description = "test fixture"

        def __init__(self) -> None:
            self.started = False

        async def start(self, bus: object) -> None:
            self.started = True

        async def stop(self) -> None:
            self.started = False

        async def drain(self) -> None:
            await asyncio.sleep(60.0)  # never returns within the grace window

    silent = _SilentDrainer()
    robot = _make_robot(channels=[silent], shutdown_grace=0.05)

    with caplog.at_level(logging.WARNING, logger="src.robot"):
        await robot.start()
        await robot.stop()

    warnings = [
        rec.message for rec in caplog.records if rec.levelno >= logging.WARNING
    ]
    assert any(
        "_SilentDrainer" in m and "elapsed" in m and "forcing stop" in m
        for m in warnings
    ), f"expected drain timeout warning, got: {warnings}"


async def test_robot_returns_immediately_when_drain_returns_fast() -> None:
    """A drain() that returns quickly does not eat the grace window."""
    import time

    class _FastDrainer(BusComponent):
        component_name = "fast-drainer"
        component_category = ComponentCategory.CHANNEL
        component_description = "test fixture"

        def __init__(self) -> None:
            self.started = False
            self.drained = False

        async def start(self, bus: object) -> None:
            self.started = True

        async def stop(self) -> None:
            self.started = False

        async def drain(self) -> None:
            await asyncio.sleep(0)
            self.drained = True

    drainer = _FastDrainer()
    robot = _make_robot(channels=[drainer], shutdown_grace=10.0)

    await robot.start()
    started = time.monotonic()
    await robot.stop()
    elapsed = time.monotonic() - started

    assert drainer.drained, "drain() was not awaited"
    assert elapsed < 1.0, f"shutdown took {elapsed:.2f}s although drain returned fast"


async def test_robot_logs_drain_exception_without_aborting_shutdown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A drain() that raises is logged but doesn't break the shutdown path."""

    class _RaisingDrainer(BusComponent):
        component_name = "raising-drainer"
        component_category = ComponentCategory.CHANNEL
        component_description = "test fixture"

        def __init__(self) -> None:
            self.stopped = False

        async def start(self, bus: object) -> None:
            pass

        async def stop(self) -> None:
            self.stopped = True

        async def drain(self) -> None:
            raise RuntimeError("drain went sideways")

    drainer = _RaisingDrainer()
    robot = _make_robot(channels=[drainer], shutdown_grace=1.0)

    with caplog.at_level(logging.WARNING, logger="src.robot"):
        await robot.start()
        await robot.stop()

    assert drainer.stopped, "stop() must run even when drain() raised"
    errors = [
        r.message
        for r in caplog.records
        if r.levelno >= logging.WARNING and "drain hook" in r.message
    ]
    assert errors, "expected a drain-failure log entry"


async def test_robot_label_with_only_id(caplog: pytest.LogCaptureFixture) -> None:
    robot = _make_robot(robot_id="dreamgirl")

    with caplog.at_level(logging.INFO, logger="src.robot"):
        async with robot:
            pass

    messages = [rec.message for rec in caplog.records if rec.name == "src.robot"]
    assert "robot (dreamgirl) online (Ctrl-C to stop)" in messages
    assert "robot (dreamgirl) offline" in messages


async def test_robot_logs_rollback_on_failed_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingComponent("kernel", log, category=ComponentCategory.KERNEL)
    ch_bad = _RecordingComponent("ch-bad", log)
    ch_bad.fail_on_start = True
    robot = _make_robot(bus=bus, kernel=kernel, channels=[ch_bad])

    with caplog.at_level(logging.INFO, logger="src.robot"):
        with pytest.raises(RuntimeError):
            await robot.start()

    messages = [rec.message for rec in caplog.records if rec.name == "src.robot"]
    assert "startup failed, rolling back" in messages
    assert "robot online (Ctrl-C to stop)" not in messages


async def test_telemetry_component_starts_before_lifecycle_boot_is_published() -> None:
    """A TELEMETRY component subscribes via subscribe_all in Phase 2,
    so it must witness the lifecycle ``boot`` event live -- otherwise
    the very first lifecycle event is missing from the recording.
    """
    from src.bus import RobotLifecycle
    from src.bus.messages import RobotEvent
    from src.bus.ports import BusPort, Subscription

    seen: list[RobotEvent] = []

    class _MiniRecorder(BusComponent):
        component_name = "mini-recorder"
        component_category = ComponentCategory.TELEMETRY
        component_description = "test fixture"

        def __init__(self) -> None:
            self._subscription: Subscription | None = None

        async def start(self, bus: BusPort) -> None:
            self._subscription = bus.subscribe_all(self._record)

        async def stop(self) -> None:
            if self._subscription is not None:
                await self._subscription.unsubscribe()
                self._subscription = None

        async def _record(self, event: RobotEvent) -> None:
            seen.append(event)

    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingComponent("kernel", log, category=ComponentCategory.KERNEL)
    recorder = _MiniRecorder()

    robot = Robot(
        identity=RobotIdentity(id="alpha", name="Alpha"),
        components=[bus, kernel, recorder],
        control_plane_config=ControlPlaneConfig(enabled=False),
        shutdown_grace=0.0,
    )

    async with robot:
        await asyncio.sleep(0.01)

    # The recorder, as a TELEMETRY/skeleton component, must have
    # been online when the lifecycle ``boot`` event was published.
    boots = [
        evt
        for evt in seen
        if isinstance(evt, RobotLifecycle) and evt.phase == "boot"
    ]
    readys = [
        evt
        for evt in seen
        if isinstance(evt, RobotLifecycle) and evt.phase == "ready"
    ]
    assert len(boots) == 1, (
        "telemetry must witness the lifecycle 'boot' event live, but it was missing"
    )
    assert len(readys) == 1
    # Order: ``boot`` precedes ``ready`` in the recording.
    boot_idx = next(
        i
        for i, evt in enumerate(seen)
        if isinstance(evt, RobotLifecycle) and evt.phase == "boot"
    )
    ready_idx = next(
        i
        for i, evt in enumerate(seen)
        if isinstance(evt, RobotLifecycle) and evt.phase == "ready"
    )
    assert boot_idx < ready_idx


async def test_telemetry_starts_before_userspace_components() -> None:
    """A TELEMETRY skeleton component starts before any KERNEL/CHANNEL."""
    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingComponent("kernel", log, category=ComponentCategory.KERNEL)
    channel = _RecordingComponent("ch-1", log, category=ComponentCategory.CHANNEL)
    telemetry = _RecordingComponent(
        "tele", log, category=ComponentCategory.TELEMETRY
    )

    robot = Robot(
        identity=RobotIdentity(),
        components=[bus, channel, telemetry, kernel],
        control_plane_config=ControlPlaneConfig(enabled=False),
        shutdown_grace=0.0,
    )

    async with robot:
        pass

    starts = [entry for entry in log if entry.startswith("start:")]
    stops = [entry for entry in log if entry.startswith("stop:")]
    # Telemetry boots before kernel/channel; on shutdown it stops last
    # (after kernel and channel are gone, but before the bus -- the
    # bus is not in `log` because the recording component is the
    # AsyncioBus, which doesn't write to this list).
    assert starts == ["start:tele", "start:kernel", "start:ch-1"]
    assert stops == ["stop:ch-1", "stop:kernel", "stop:tele"]


async def test_robot_sorts_components_by_boot_priority() -> None:
    """Components handed to the constructor in any order get sorted."""
    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingComponent("kernel", log, category=ComponentCategory.KERNEL)
    ch = _RecordingComponent("ch", log)

    # Note: bus last, channel before kernel -- robot must reorder.
    components: list[RobotComponent] = [ch, kernel, bus]

    robot = Robot(
        identity=RobotIdentity(),
        components=components,
        control_plane_config=ControlPlaneConfig(enabled=False),
        shutdown_grace=0.0,
    )

    assert robot.components == (bus, kernel, ch)
