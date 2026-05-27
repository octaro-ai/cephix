"""Tests for :class:`BaseKernel` and the per-run state machine.

The base kernel is exercised end-to-end with a real bus: every test
boots an :class:`AsyncioBus`, attaches the kernel paired with an
actor (injected at construction, *not* on the bus), publishes a
:class:`RobotInput`, and asserts on the resulting bus traffic. Phase
telemetry is observed through the kernel-phase topic.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.actor.echo import EchoActor
from src.actor.ports import ActorPort
from src.actor.types import ActorResponse
from src.bus import (
    AsyncioBus,
    KERNEL_PHASE_TOPIC,
    KernelPhase,
    RobotEvent,
    RobotInput,
    RobotOutput,
)
from src.kernel.base import BaseKernel
from src.kernel.run import RunPhase


# ---------------------------------------------------------------------------
# Test actors
# ---------------------------------------------------------------------------


class _FailingActor(ActorPort):
    """Actor that always returns ok=False."""

    component_name = "failing"

    async def run(self, actor_context: dict[str, Any]) -> ActorResponse:
        del actor_context
        return ActorResponse(ok=False, error="actor blew up")


class _HangingActor(ActorPort):
    """Actor whose run never resolves -- forces a kernel timeout."""

    component_name = "hanging"

    async def run(self, actor_context: dict[str, Any]) -> ActorResponse:
        del actor_context
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _RecordingActor(ActorPort):
    """Captures every actor context it was handed."""

    component_name = "recording"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, actor_context: dict[str, Any]) -> ActorResponse:
        self.calls.append(actor_context)
        return ActorResponse(text="stub-response", ok=True)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _start_kernel(
    bus: AsyncioBus,
    *,
    actor: ActorPort,
    actor_timeout: float | None = 1.0,
    output_topic: str = "output.message",
) -> BaseKernel:
    kernel = BaseKernel(
        actor=actor,
        actor_timeout=actor_timeout,
        output_topic=output_topic,
    )
    await actor.start()
    await kernel.start(bus)
    return kernel


# ---------------------------------------------------------------------------
# End-to-end happy path
# ---------------------------------------------------------------------------


async def test_base_kernel_with_echo_actor_produces_echoed_output() -> None:
    bus = AsyncioBus()
    outputs: list[RobotOutput] = []

    async def collect_outputs(event: RobotEvent) -> None:
        if isinstance(event, RobotOutput):
            outputs.append(event)

    bus.subscribe("output.message", collect_outputs)

    actor = EchoActor()
    await bus.start()
    try:
        kernel = await _start_kernel(bus, actor=actor)
        try:
            await bus.publish(
                RobotInput(
                    topic="input.message",
                    principal="user-1",
                    source="channel.test",
                    run_id="run-1",
                    text="hello",
                    payload={"session_id": "abc"},
                )
            )
            await asyncio.sleep(0.05)
        finally:
            await kernel.stop()
            await actor.stop()
    finally:
        await bus.stop()

    assert len(outputs) == 1
    out = outputs[0]
    assert out.text == "echo: hello"
    assert out.topic == "output.message"
    assert out.source == "kernel.base"
    assert out.run_id == "run-1"
    assert out.principal == "user-1"


async def test_base_kernel_emits_one_phase_event_per_phase() -> None:
    bus = AsyncioBus()
    phases: list[KernelPhase] = []

    async def collect_phases(event: RobotEvent) -> None:
        if isinstance(event, KernelPhase):
            phases.append(event)

    bus.subscribe(KERNEL_PHASE_TOPIC, collect_phases)

    actor = EchoActor()
    await bus.start()
    try:
        kernel = await _start_kernel(bus, actor=actor)
        try:
            await bus.publish(
                RobotInput(
                    topic="input.message",
                    principal="user",
                    source="channel.test",
                    run_id="run-phase",
                    text="hi",
                )
            )
            await asyncio.sleep(0.05)
        finally:
            await kernel.stop()
            await actor.stop()
    finally:
        await bus.stop()

    seen = [p.phase for p in phases]
    assert seen == [
        RunPhase.OBSERVING.value,
        RunPhase.PLANNING.value,
        RunPhase.ACTING.value,
        RunPhase.FINALIZING.value,
        RunPhase.RESPONDING.value,
        RunPhase.DONE.value,
    ]
    for p in phases:
        assert p.kernel == "base"
        assert p.run_id == "run-phase"
        assert p.iteration == 0
        assert p.error == ""


async def test_base_kernel_phase_events_carry_wide_event_details() -> None:
    """Each phase event carries phase-specific analytics in ``details``.

    The base kernel populates a small set of always-on wide-event
    fields (input length, actor name+latency, output length, run
    aggregates) so the JSONL telemetry file alone can answer
    "how long did this run take?" / "did the actor succeed?" /
    "show me all empty outputs" without correlating other events.
    """
    bus = AsyncioBus()
    phases: list[KernelPhase] = []

    async def collect_phases(event: RobotEvent) -> None:
        if isinstance(event, KernelPhase):
            phases.append(event)

    bus.subscribe(KERNEL_PHASE_TOPIC, collect_phases)

    actor = EchoActor()
    await bus.start()
    try:
        kernel = await _start_kernel(bus, actor=actor)
        try:
            await bus.publish(
                RobotInput(
                    topic="input.message",
                    principal="user",
                    source="channel.test",
                    run_id="run-wide",
                    text="hello",
                )
            )
            await asyncio.sleep(0.05)
        finally:
            await kernel.stop()
            await actor.stop()
    finally:
        await bus.stop()

    by_phase = {p.phase: p.details for p in phases}

    obs = by_phase[RunPhase.OBSERVING.value]
    assert obs["input_text_len"] == len("hello")
    assert "input_event_id" in obs
    assert "phase_duration_ms" in obs

    plan = by_phase[RunPhase.PLANNING.value]
    assert plan["context_keys"] == ["input"]

    act = by_phase[RunPhase.ACTING.value]
    assert act["actor_name"] == "echo"
    assert act["actor_ok"] is True
    assert isinstance(act["actor_duration_ms"], (int, float))

    fin = by_phase[RunPhase.FINALIZING.value]
    assert fin["path"] == "output"
    assert fin["is_tool_call"] is False

    resp = by_phase[RunPhase.RESPONDING.value]
    assert resp["output_text_len"] == len("echo: hello")
    assert "output_event_id" in resp

    done = by_phase[RunPhase.DONE.value]
    assert done["outcome"] == "ok"
    assert done["iterations"] == 1
    assert isinstance(done["run_duration_ms"], (int, float))
    assert isinstance(done["total_actor_ms"], (int, float))


async def test_base_kernel_error_phase_carries_failed_phase_details() -> None:
    """When a phase raises, the error event names the failure type."""
    bus = AsyncioBus()
    phases: list[KernelPhase] = []

    async def collect(event: RobotEvent) -> None:
        if isinstance(event, KernelPhase):
            phases.append(event)

    bus.subscribe(KERNEL_PHASE_TOPIC, collect)

    actor = _FailingActor()
    await bus.start()
    try:
        kernel = await _start_kernel(bus, actor=actor)
        try:
            await bus.publish(
                RobotInput(
                    topic="input.message",
                    principal="user",
                    source="channel.test",
                    run_id="run-err",
                    text="boom",
                )
            )
            await asyncio.sleep(0.05)
        finally:
            await kernel.stop()
            await actor.stop()
    finally:
        await bus.stop()

    by_phase = {p.phase: p for p in phases}
    err = by_phase[RunPhase.ERROR.value]
    assert err.error.startswith("RuntimeError")
    assert err.details["error_type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# Plan: actor context shape
# ---------------------------------------------------------------------------


async def test_base_kernel_hands_actor_the_curated_actor_context() -> None:
    """The default ``plan`` builds the actor context and the kernel passes it on."""
    bus = AsyncioBus()
    actor = _RecordingActor()

    await bus.start()
    try:
        kernel = await _start_kernel(bus, actor=actor)
        try:
            await bus.publish(
                RobotInput(
                    topic="input.message",
                    principal="user",
                    source="channel.test",
                    run_id="run-context",
                    text="hello-context",
                    payload={"k": "v"},
                )
            )
            await asyncio.sleep(0.05)
        finally:
            await kernel.stop()
            await actor.stop()
    finally:
        await bus.stop()

    assert len(actor.calls) == 1
    actor_ctx = actor.calls[0]
    assert actor_ctx["input"]["text"] == "hello-context"
    assert actor_ctx["input"]["principal"] == "user"
    assert actor_ctx["input"]["run_id"] == "run-context"
    assert actor_ctx["input"]["payload"] == {"k": "v"}


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_base_kernel_emits_error_phase_when_actor_fails() -> None:
    bus = AsyncioBus()
    phases: list[KernelPhase] = []

    async def collect_phases(event: RobotEvent) -> None:
        if isinstance(event, KernelPhase):
            phases.append(event)

    bus.subscribe(KERNEL_PHASE_TOPIC, collect_phases)

    actor = _FailingActor()
    await bus.start()
    try:
        kernel = await _start_kernel(bus, actor=actor)
        try:
            await bus.publish(
                RobotInput(
                    topic="input.message",
                    principal="user",
                    source="channel.test",
                    run_id="run-fail",
                    text="boom",
                )
            )
            await asyncio.sleep(0.05)
        finally:
            await kernel.stop()
            await actor.stop()
    finally:
        await bus.stop()

    error_phases = [p for p in phases if p.phase == RunPhase.ERROR.value]
    assert len(error_phases) == 1
    assert "actor blew up" in error_phases[0].error
    assert not any(p.phase == RunPhase.DONE.value for p in phases)


async def test_base_kernel_times_out_when_actor_hangs() -> None:
    bus = AsyncioBus()
    phases: list[KernelPhase] = []
    outputs: list[RobotOutput] = []

    async def collect(event: RobotEvent) -> None:
        if isinstance(event, KernelPhase):
            phases.append(event)
        elif isinstance(event, RobotOutput):
            outputs.append(event)

    bus.subscribe(KERNEL_PHASE_TOPIC, collect)
    bus.subscribe("output.message", collect)

    actor = _HangingActor()
    await bus.start()
    try:
        kernel = await _start_kernel(bus, actor=actor, actor_timeout=0.05)
        try:
            await bus.publish(
                RobotInput(
                    topic="input.message",
                    principal="user",
                    source="channel.test",
                    run_id="run-timeout",
                    text="hello",
                )
            )
            await asyncio.sleep(0.2)
        finally:
            await kernel.stop()
            await actor.stop()
    finally:
        await bus.stop()

    error_phases = [p for p in phases if p.phase == RunPhase.ERROR.value]
    assert len(error_phases) == 1
    assert "timed out" in error_phases[0].error
    assert outputs == []


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_base_kernel_unsubscribes_on_stop() -> None:
    bus = AsyncioBus()
    actor = EchoActor()
    outputs: list[RobotOutput] = []

    async def collect(event: RobotEvent) -> None:
        if isinstance(event, RobotOutput):
            outputs.append(event)

    bus.subscribe("output.message", collect)

    await bus.start()
    try:
        kernel = BaseKernel(actor=actor, actor_timeout=0.5)
        await actor.start()
        await kernel.start(bus)
        await bus.publish(
            RobotInput(
                topic="input.message",
                principal="user",
                source="channel.test",
                run_id="run-1",
                text="first",
            )
        )
        await asyncio.sleep(0.1)
        await kernel.stop()
        await bus.publish(
            RobotInput(
                topic="input.message",
                principal="user",
                source="channel.test",
                run_id="run-2",
                text="ignored",
            )
        )
        await asyncio.sleep(0.1)
        await actor.stop()
    finally:
        await bus.stop()

    texts = [out.text for out in outputs]
    assert texts == ["echo: first"]


def test_base_kernel_requires_an_actor() -> None:
    with pytest.raises(TypeError, match="ActorPort"):
        BaseKernel(actor="not-an-actor")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Subclassing: a concrete kernel can override individual phases without
# touching the loop.
# ---------------------------------------------------------------------------


class _UpperKernel(BaseKernel):
    """Toy override: every reply text is upper-cased in finalize."""

    component_name = "upper"

    async def finalize(self, ctx: Any) -> None:  # noqa: ANN401 -- test stub
        await super().finalize(ctx)
        if ctx.output_text is not None:
            ctx.output_text = ctx.output_text.upper()


async def test_subclass_can_override_a_single_phase() -> None:
    bus = AsyncioBus()
    actor = EchoActor()
    outputs: list[RobotOutput] = []

    async def collect(event: RobotEvent) -> None:
        if isinstance(event, RobotOutput):
            outputs.append(event)

    bus.subscribe("output.message", collect)

    await bus.start()
    try:
        kernel = _UpperKernel(actor=actor, actor_timeout=0.5)
        await actor.start()
        await kernel.start(bus)
        try:
            await bus.publish(
                RobotInput(
                    topic="input.message",
                    principal="user",
                    source="channel.test",
                    run_id="run-upper",
                    text="hello",
                )
            )
            await asyncio.sleep(0.1)
        finally:
            await kernel.stop()
            await actor.stop()
    finally:
        await bus.stop()

    assert len(outputs) == 1
    assert outputs[0].text == "ECHO: HELLO"
    assert outputs[0].source == "kernel.upper"


# ---------------------------------------------------------------------------
# Defensive: handler must fail loudly when invoked without a bus.
# ---------------------------------------------------------------------------


async def test_respond_raises_when_not_started() -> None:
    actor = EchoActor()
    kernel = BaseKernel(actor=actor)
    from src.kernel.run import RunContext

    ctx = RunContext(run_id="r")
    ctx.input = RobotInput(
        topic="input.message",
        principal="user",
        source="t",
        run_id="r",
        text="x",
    )
    with pytest.raises(RuntimeError, match="not started"):
        await kernel.respond(ctx)
