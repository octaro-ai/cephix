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

    robot = Robot(bus=bus, kernel=kernel, channels=[ch_a, ch_b])
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

    robot = Robot(bus=bus, kernel=kernel, channels=[ch_a, ch_bad])

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
    robot = Robot(bus=bus, kernel=kernel)

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

    robot = Robot(bus=bus, kernel=kernel, channels=[ch])

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

    robot = Robot(bus=bus, kernel=kernel, channels=[ch])

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
    robot = Robot(bus=bus, kernel=kernel, channels=[ch])

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


async def test_robot_logs_rollback_on_failed_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log: list[str] = []
    bus = AsyncioBus()
    kernel = _RecordingPart("kernel", log)
    ch_bad = _RecordingPart("ch-bad", log)
    ch_bad.fail_on_start = True
    robot = Robot(bus=bus, kernel=kernel, channels=[ch_bad])

    with caplog.at_level(logging.INFO, logger="src.robot"):
        with pytest.raises(RuntimeError):
            await robot.start()

    messages = [rec.message for rec in caplog.records if rec.name == "src.robot"]
    assert "startup failed, rolling back" in messages
    assert "robot online (Ctrl-C to stop)" not in messages
