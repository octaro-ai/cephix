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
    ErrorInfo,
    KernelPhase,
    MountEvent,
    RobotEvent,
    RobotInput,
    RobotOutput,
    component_mount_topic,
)
from src.kernel.base import BaseKernel
from src.kernel.run import RunPhase


# ---------------------------------------------------------------------------
# Test actors
# ---------------------------------------------------------------------------


class _FailingActor(ActorPort):
    """Actor that always returns status='error'."""

    component_name = "failing"

    async def run(self, actor_context: dict[str, Any]) -> ActorResponse:
        del actor_context
        return ActorResponse(
            status="error",
            error=ErrorInfo(code="internal", message="actor blew up"),
        )


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
        return ActorResponse(message="stub-response", status="ok")


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
                    message="hello",
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
    assert out.message == "echo: hello"
    assert out.status == "ok"
    assert out.error is None
    assert out.topic == "output.message"
    assert out.source == "kernel.base"
    assert out.source_id == kernel.instance_id
    assert out.run_id == "run-1"
    assert out.principal == "user-1"


async def test_base_kernel_emits_mount_event_for_actor_on_start() -> None:
    """The kernel announces which actor it wired in via MountEvent.

    Subscribers learn 'kernel.base now has the echo actor mounted'
    without having to cross-reference boot order, and the snapshot
    carries the actor's instance id so two BaseKernel instances
    can be told apart.
    """
    bus = AsyncioBus()
    mounts: list[MountEvent] = []

    async def collect(event: RobotEvent) -> None:
        if isinstance(event, MountEvent):
            mounts.append(event)

    bus.subscribe(component_mount_topic("base"), collect)

    actor = EchoActor()
    await bus.start()
    try:
        kernel = await _start_kernel(bus, actor=actor)
        await asyncio.sleep(0)
        try:
            assert len(mounts) == 1
            mount = mounts[0]
            assert mount.phase == "mounted"
            assert mount.owner == "kernel.base"
            assert mount.slot == "actor"
            assert mount.source == "kernel.base"
            assert mount.source_id == kernel.instance_id
            assert mount.mounted is not None
            assert mount.mounted.name == "echo"
            assert mount.mounted.metadata["instance_id"] == actor.instance_id
            assert mount.mounted.metadata["kernel_instance_id"] == kernel.instance_id
        finally:
            await kernel.stop()
            await actor.stop()
            await asyncio.sleep(0)
    finally:
        await bus.stop()

    # On stop, a matching unmounted event closes the slot.
    unmounted = [m for m in mounts if m.phase == "unmounted"]
    assert len(unmounted) == 1
    assert unmounted[0].mounted is None
    assert unmounted[0].slot == "actor"


async def test_base_kernel_phase_events_carry_kernel_instance_id() -> None:
    """KernelPhase telemetry must identify the publishing kernel instance.

    With two BaseKernel instances on one robot, a query like
    ``"phase":"acting","status":"error"`` must still pin down
    *which* kernel suffered the failure.
    """
    bus = AsyncioBus()
    phases: list[KernelPhase] = []

    async def collect(event: RobotEvent) -> None:
        if isinstance(event, KernelPhase):
            phases.append(event)

    bus.subscribe(KERNEL_PHASE_TOPIC, collect)

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
                    run_id="run-id-1",
                    message="hi",
                )
            )
            await asyncio.sleep(0.05)
        finally:
            await kernel.stop()
            await actor.stop()
    finally:
        await bus.stop()

    assert phases  # at least one phase event captured
    for ev in phases:
        assert ev.source == "kernel.base"
        assert ev.source_id == kernel.instance_id
    # The acting phase carries the actor's id in its details so
    # queries can correlate kernel and actor across the wide-event log.
    acting = [p for p in phases if p.phase == RunPhase.ACTING.value]
    assert acting, "expected at least one acting phase event"
    assert acting[0].details.get("actor_instance_id") == actor.instance_id


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
                    message="hi",
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
        assert p.status == "ok"
        assert p.error is None


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
                    message="hello",
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
    assert obs["input_message_len"] == len("hello")
    assert "input_event_id" in obs
    assert "phase_duration_ms" in obs

    plan = by_phase[RunPhase.PLANNING.value]
    assert plan["context_keys"] == ["input"]

    act = by_phase[RunPhase.ACTING.value]
    assert act["actor_name"] == "echo"
    assert act["actor_status"] == "ok"
    assert isinstance(act["actor_duration_ms"], (int, float))

    fin = by_phase[RunPhase.FINALIZING.value]
    assert fin["path"] == "output"
    assert fin["is_tool_call"] is False

    resp = by_phase[RunPhase.RESPONDING.value]
    assert resp["output_message_len"] == len("echo: hello")
    assert "output_event_id" in resp

    done = by_phase[RunPhase.DONE.value]
    assert done["outcome"] == "ok"
    assert done["iterations"] == 1
    assert isinstance(done["run_duration_ms"], (int, float))
    assert isinstance(done["total_actor_ms"], (int, float))


async def test_done_event_aggregates_all_phase_details() -> None:
    """``done`` is the wide-event row: it carries every key every phase wrote.

    Subscribers that only care about the run-as-a-whole should be
    able to listen exclusively on ``done`` and reconstruct the
    full picture from a single event. Per-phase
    ``phase_duration_ms`` is namespaced as ``<phase>_duration_ms``
    so each phase's timing survives the merge.
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
                    run_id="run-aggregate",
                    message="hello",
                )
            )
            await asyncio.sleep(0.05)
        finally:
            await kernel.stop()
            await actor.stop()
    finally:
        await bus.stop()

    done = next(p for p in phases if p.phase == RunPhase.DONE.value)
    details = done.details

    # Run-level totals stay intact.
    assert details["outcome"] == "ok"
    assert details["iterations"] == 1
    assert isinstance(details["run_duration_ms"], (int, float))
    assert isinstance(details["total_actor_ms"], (int, float))

    # Per-phase keys are rolled up onto done.
    assert details["input_message_len"] == len("hello")
    assert "input_event_id" in details
    assert details["actor_name"] == "echo"
    assert details["actor_status"] == "ok"
    assert details["path"] == "output"
    assert details["is_tool_call"] is False
    assert details["output_message_len"] == len("echo: hello")
    assert "output_event_id" in details

    # Per-phase durations survive the merge under namespaced keys.
    assert "phase_duration_ms" not in details
    for namespaced in (
        "observing_duration_ms",
        "planning_duration_ms",
        "acting_duration_ms",
        "finalizing_duration_ms",
        "responding_duration_ms",
    ):
        assert namespaced in details, namespaced
        assert isinstance(details[namespaced], (int, float))


async def test_done_event_carries_partial_details_on_failure() -> None:
    """A failed run still rolls up everything the surviving phases produced.

    The wide-event row is most valuable for debugging failures, so
    the loop must absorb each phase's details into the run
    accumulator even when a later phase raises. The failing phase
    itself also contributes whatever it managed to write before
    the exception.
    """
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
                    run_id="run-aggregate-fail",
                    message="boom",
                )
            )
            await asyncio.sleep(0.05)
        finally:
            await kernel.stop()
            await actor.stop()
    finally:
        await bus.stop()

    done = next(p for p in phases if p.phase == RunPhase.DONE.value)
    details = done.details

    # Error totals are correct.
    assert details["outcome"] == "error"
    assert details["iterations"] == 1
    # Surviving phases still rolled up.
    assert details["input_message_len"] == len("boom")
    assert "observing_duration_ms" in details
    assert "planning_duration_ms" in details
    # Failing phase's pre-exception scratch survives too.
    assert "acting_duration_ms" in details


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
                    message="hello-context",
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
    assert actor_ctx["input"]["message"] == "hello-context"
    assert actor_ctx["input"]["principal"] == "user"
    assert actor_ctx["input"]["run_id"] == "run-context"
    assert actor_ctx["input"]["payload"] == {"k": "v"}


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_base_kernel_emits_failed_phase_then_done_when_actor_fails() -> None:
    """Failure surfaces as ``acting`` phase event with ``status='error'`` plus a trailing ``done``."""
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
                    message="boom",
                )
            )
            await asyncio.sleep(0.05)
        finally:
            await kernel.stop()
            await actor.stop()
    finally:
        await bus.stop()

    seen = [(p.phase, p.status) for p in phases]
    # observe / plan succeed, acting fails, then a closing done event.
    assert seen == [
        (RunPhase.OBSERVING.value, "ok"),
        (RunPhase.PLANNING.value, "ok"),
        (RunPhase.ACTING.value, "error"),
        (RunPhase.DONE.value, "error"),
    ]

    failing = next(p for p in phases if p.phase == RunPhase.ACTING.value)
    assert failing.error is not None
    assert failing.error.code == "internal"
    assert "actor blew up" in failing.error.message
    assert failing.error.details["failed_phase"] == "acting"

    done = next(p for p in phases if p.phase == RunPhase.DONE.value)
    assert done.error is not None
    assert done.error.code == "internal"
    assert done.details["outcome"] == "error"


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
                    message="hello",
                )
            )
            await asyncio.sleep(0.2)
        finally:
            await kernel.stop()
            await actor.stop()
    finally:
        await bus.stop()

    failed = [p for p in phases if p.status == "error"]
    # Both the failing phase and the trailing done event report status='error'.
    assert len(failed) == 2
    acting = next(p for p in failed if p.phase == RunPhase.ACTING.value)
    assert acting.error is not None
    assert acting.error.code == "timeout"
    assert "timed out" in acting.error.message
    # The base kernel does NOT auto-publish a user-facing error output
    # -- that is the kernel author's choice. See module docstring.
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
                message="first",
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
                message="ignored",
            )
        )
        await asyncio.sleep(0.1)
        await actor.stop()
    finally:
        await bus.stop()

    texts = [out.message for out in outputs]
    assert texts == ["echo: first"]


def test_base_kernel_requires_an_actor() -> None:
    with pytest.raises(TypeError, match="ActorPort"):
        BaseKernel(actor="not-an-actor")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Subclassing: a concrete kernel can override individual phases without
# touching the loop.
# ---------------------------------------------------------------------------


class _UpperKernel(BaseKernel):
    """Toy override: every reply message is upper-cased in finalize."""

    component_name = "upper"

    async def finalize(self, ctx: Any) -> None:  # noqa: ANN401 -- test stub
        await super().finalize(ctx)
        if ctx.output_message is not None:
            ctx.output_message = ctx.output_message.upper()


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
                    message="hello",
                )
            )
            await asyncio.sleep(0.1)
        finally:
            await kernel.stop()
            await actor.stop()
    finally:
        await bus.stop()

    assert len(outputs) == 1
    assert outputs[0].message == "ECHO: HELLO"
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
        message="x",
    )
    with pytest.raises(RuntimeError, match="not started"):
        await kernel.respond(ctx)
