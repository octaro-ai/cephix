"""Tests for the Robot composition root."""

from __future__ import annotations

import asyncio
import logging

import pytest

from src.bus import AsyncioBus
from src.robot import Robot


class _RecordingPart:
    """Minimal fake bus component used to verify lifecycle order."""

    def __init__(self, name: str, log: list[str]) -> None:
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


async def test_robot_starts_bus_kernel_then_channels_in_order() -> None:
    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingPart("kernel", log)
    ch_a = _RecordingPart("ch-a", log)
    ch_b = _RecordingPart("ch-b", log)

    robot = Robot(bus=bus, kernel=kernel, channels=[ch_a, ch_b], shutdown_grace=0.0)
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
    kernel = _RecordingPart("kernel", log)
    ch_a = _RecordingPart("ch-a", log)
    ch_bad = _RecordingPart("ch-bad", log)
    ch_bad.fail_on_start = True

    robot = Robot(bus=bus, kernel=kernel, channels=[ch_a, ch_bad], shutdown_grace=0.0)

    try:
        await robot.start()
    except RuntimeError as exc:
        assert "ch-bad" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

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
    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingPart("kernel", log)
    robot = Robot(bus=bus, kernel=kernel, shutdown_grace=0.0)

    await robot.start()
    runner = asyncio.create_task(robot.run_forever())

    await asyncio.sleep(0.01)
    assert not runner.done()

    await robot.stop()
    await asyncio.wait_for(runner, timeout=1.0)


async def test_robot_injects_bus_into_components_at_start() -> None:
    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingPart("kernel", log)
    ch = _RecordingPart("ch", log)

    robot = Robot(bus=bus, kernel=kernel, channels=[ch], shutdown_grace=0.0)

    assert kernel.injected_bus is None
    assert ch.injected_bus is None

    async with robot:
        assert kernel.injected_bus is bus
        assert ch.injected_bus is bus

    assert kernel.injected_bus is None
    assert ch.injected_bus is None


async def test_robot_exposes_components() -> None:
    bus = AsyncioBus()
    log: list[str] = []
    kernel = _RecordingPart("kernel", log)
    ch = _RecordingPart("ch", log)

    robot = Robot(bus=bus, kernel=kernel, channels=[ch], shutdown_grace=0.0)

    assert robot.bus is bus
    assert robot.kernel is kernel
    assert robot.channels == (ch,)


async def test_robot_logs_boot_and_shutdown_narrative(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingPart("kernel", log)
    ch = _RecordingPart("ch", log)
    robot = Robot(bus=bus, kernel=kernel, channels=[ch], shutdown_grace=0.0)

    with caplog.at_level(logging.INFO, logger="src.robot"):
        async with robot:
            pass

    messages = [rec.message for rec in caplog.records if rec.name == "src.robot"]
    assert "starting..." in messages
    assert "AsyncioBus started" in messages
    assert any(m.startswith("_RecordingPart attached") for m in messages)
    assert "robot online (Ctrl-C to stop)" in messages
    assert any(m.startswith("_RecordingPart detached") for m in messages)
    assert "AsyncioBus stopped" in messages
    assert "robot offline" in messages

    online_idx = messages.index("robot online (Ctrl-C to stop)")
    offline_idx = messages.index("robot offline")
    assert online_idx < offline_idx


async def test_robot_logs_lifecycle_with_identity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingPart("kernel", log)
    robot = Robot(
        bus=bus,
        kernel=kernel,
        robot_id="dreamgirl",
        robot_name="Dreamgirl",
        shutdown_grace=0.0,
    )

    with caplog.at_level(logging.INFO, logger="src.robot"):
        async with robot:
            pass

    messages = [rec.message for rec in caplog.records if rec.name == "src.robot"]
    assert "starting robot 'Dreamgirl' (dreamgirl)..." in messages
    assert "robot 'Dreamgirl' (dreamgirl) online (Ctrl-C to stop)" in messages
    assert "robot 'Dreamgirl' (dreamgirl) offline" in messages
    # the anonymous wording must NOT appear when an identity is set
    assert "starting..." not in messages
    assert "robot online (Ctrl-C to stop)" not in messages
    assert "robot offline" not in messages


async def test_robot_publishes_robot_ready_on_lifecycle_topic() -> None:
    """The robot announces itself with a retained RobotReady broadcast."""
    from src.bus import LIFECYCLE_TOPIC, RobotReady

    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingPart("kernel", log)
    ch = _RecordingPart("ch", log)

    robot = Robot(
        bus=bus,
        kernel=kernel,
        channels=[ch],
        robot_id="alpha",
        robot_name="Alpha",
        shutdown_grace=0.0,
    )

    async with robot:
        retained = bus.retained(LIFECYCLE_TOPIC)
        assert isinstance(retained, RobotReady)
        assert retained.robot_id == "alpha"
        assert retained.robot_name == "Alpha"
        assert retained.boot_id.startswith("boot-")
        assert retained.run_id == retained.boot_id
        # bus, kernel, one channel -- three components in the snapshot
        categories = [info.category for info in retained.components]
        assert "bus" in categories
        # _RecordingPart is not a RobotComponent, so it shows up as 'unknown'
        assert categories.count("unknown") == 2


async def test_robot_publishes_robot_shutdown_on_stop() -> None:
    """The robot announces a retained RobotShutdown before it tears down."""
    from src.bus import LIFECYCLE_TOPIC, RobotEvent, RobotShutdown

    received: list[RobotEvent] = []

    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingPart("kernel", log)
    robot = Robot(
        bus=bus,
        kernel=kernel,
        robot_id="alpha",
        robot_name="Alpha",
        shutdown_grace=0.0,
    )

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


async def test_robot_grace_period_runs_before_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The robot waits shutdown_grace seconds before stopping components."""
    sleeps: list[float] = []

    real_sleep = asyncio.sleep

    async def tracking_sleep(seconds: float, *args, **kwargs):  # type: ignore[no-untyped-def]
        if seconds > 0:
            sleeps.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr("src.robot.asyncio.sleep", tracking_sleep)

    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingPart("kernel", log)
    robot = Robot(bus=bus, kernel=kernel, shutdown_grace=2.5)

    async with robot:
        pass

    assert 2.5 in sleeps


async def test_robot_label_with_only_id(caplog: pytest.LogCaptureFixture) -> None:
    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingPart("kernel", log)
    robot = Robot(bus=bus, kernel=kernel, robot_id="dreamgirl", shutdown_grace=0.0)

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
    kernel = _RecordingPart("kernel", log)
    ch_bad = _RecordingPart("ch-bad", log)
    ch_bad.fail_on_start = True
    robot = Robot(bus=bus, kernel=kernel, channels=[ch_bad], shutdown_grace=0.0)

    with caplog.at_level(logging.INFO, logger="src.robot"):
        with pytest.raises(RuntimeError):
            await robot.start()

    messages = [rec.message for rec in caplog.records if rec.name == "src.robot"]
    assert "startup failed, rolling back" in messages
    assert "robot online (Ctrl-C to stop)" not in messages
