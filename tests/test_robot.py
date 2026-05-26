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
        self.component_type = name
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
    assert "AsyncioBus started" in messages
    assert any(m.startswith("_RecordingComponent attached") for m in messages)
    assert "robot online (Ctrl-C to stop)" in messages
    assert any(m.startswith("_RecordingComponent detached") for m in messages)
    assert "AsyncioBus stopped" in messages
    assert "robot offline" in messages

    online_idx = messages.index("robot online (Ctrl-C to stop)")
    offline_idx = messages.index("robot offline")
    assert online_idx < offline_idx


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


async def test_robot_publishes_robot_boot_then_robot_ready_with_manifest() -> None:
    """The robot announces itself with retained RobotBoot then RobotReady.

    The first event (RobotBoot) is published before the kernel and
    channels attach, so they can pick up identity from the retained
    slot. The second event (RobotReady) overrides the retained slot
    once everything is up; it carries the same manifest so a late
    subscriber still learns the full composition.
    """
    from src.bus import LIFECYCLE_TOPIC, RobotReady

    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingComponent("kernel", log, category=ComponentCategory.KERNEL)
    ch = _RecordingComponent("ch", log)

    robot = _make_robot(
        bus=bus, kernel=kernel, channels=[ch], robot_id="alpha", robot_name="Alpha"
    )

    async with robot:
        retained = bus.retained(LIFECYCLE_TOPIC)
        assert isinstance(retained, RobotReady)
        assert retained.robot_id == "alpha"
        assert retained.robot_name == "Alpha"
        assert retained.boot_id.startswith("boot-")
        assert retained.run_id == retained.boot_id
        categories = [info.category for info in retained.components]
        assert categories == ["bus", "kernel", "channel"]


async def test_robot_publishes_robot_shutdown_on_stop() -> None:
    """The robot announces a retained RobotShutdown before it tears down."""
    from src.bus import LIFECYCLE_TOPIC, RobotEvent, RobotShutdown

    received: list[RobotEvent] = []

    bus = AsyncioBus()
    robot = _make_robot(bus=bus, robot_id="alpha", robot_name="Alpha")

    await robot.start()
    bus.subscribe_broadcast(LIFECYCLE_TOPIC, lambda evt: _record(received, evt))
    await asyncio.sleep(0)  # let the consumer drain the retained RobotReady
    received.clear()
    await robot.stop()
    await asyncio.sleep(0)

    shutdowns = [evt for evt in received if isinstance(evt, RobotShutdown)]
    assert len(shutdowns) == 1
    assert shutdowns[0].robot_id == "alpha"
    assert shutdowns[0].reason == "lifecycle.stop"


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
        component_type = "silent-drainer"
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
        component_type = "fast-drainer"
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
        component_type = "raising-drainer"
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


async def test_telemetry_component_starts_before_robot_boot_is_published() -> None:
    """A TELEMETRY component subscribes via subscribe_all in Phase 2,
    so it must witness RobotBoot live -- otherwise the very first
    lifecycle event is missing from the recording.
    """
    from src.bus import RobotBoot, RobotReady
    from src.bus.messages import RobotEvent
    from src.bus.ports import BusPort, Subscription

    seen: list[RobotEvent] = []

    class _MiniRecorder(BusComponent):
        component_type = "mini-recorder"
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
    # been online when RobotBoot was published.
    boots = [evt for evt in seen if isinstance(evt, RobotBoot)]
    readys = [evt for evt in seen if isinstance(evt, RobotReady)]
    assert len(boots) == 1, (
        "telemetry must witness RobotBoot live, but it was missing"
    )
    assert len(readys) == 1
    # Order: RobotBoot precedes RobotReady in the recording.
    boot_idx = next(i for i, evt in enumerate(seen) if isinstance(evt, RobotBoot))
    ready_idx = next(i for i, evt in enumerate(seen) if isinstance(evt, RobotReady))
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
