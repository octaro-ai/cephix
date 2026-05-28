"""Tests for :class:`MockLLMActor`.

The mock is the canonical end-to-end test driver. It must:

- compute realistic token counts via its own ``count_tokens``
- surface them on the ``ActorResponse.metadata`` exactly the way
  the OpenAI driver surfaces SDK-reported usage
- leave ``cost_usd`` at ``0.0`` (cost is the kernel's job, not the
  driver's -- the kernel holds the model catalog and turns tokens
  into money)
- stream word-by-word
- end-to-end work with the real :class:`AsyncioBus` +
  :class:`BaseKernel`
"""

from __future__ import annotations

import asyncio

from src.bus import AsyncioBus, RobotEvent, RobotInput, RobotOutput
from src.components import ComponentCategory
from src.kernel.base import BaseKernel
from src.actor.llm.mock_actor import MockLLMActor
from src.actor.llm.types import ActorChunk


# ---------------------------------------------------------------------------
# Identity / metadata
# ---------------------------------------------------------------------------


def test_mock_metadata() -> None:
    assert MockLLMActor.component_name == "llm.mock"
    assert MockLLMActor.component_category is ComponentCategory.ACTOR


async def test_mock_identity_defaults() -> None:
    a = MockLLMActor()
    assert a.model_id == "mock-echo"
    assert a.provider == "mock"
    await a.start()
    await a.stop()


# ---------------------------------------------------------------------------
# Token counting (whitespace words)
# ---------------------------------------------------------------------------


def test_mock_count_tokens_whitespace_words() -> None:
    a = MockLLMActor()
    assert a.count_tokens("") == 0
    assert a.count_tokens("hi") == 1
    assert a.count_tokens("one two three four") == 4


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


async def test_mock_run_reports_tokens_and_zero_cost() -> None:
    """Driver reports token counts; cost stays the kernel's concern."""
    a = MockLLMActor()
    response = await a.run({"message": "hello world"})
    assert response.status == "ok"
    assert response.message == "[mock-reply] hello world"
    meta = response.metadata
    assert meta["provider"] == "mock"
    assert meta["model_id"] == "mock-echo"
    assert meta["tokens_in"] == 2  # "hello world"
    assert meta["tokens_out"] == 3  # "[mock-reply] hello world"
    assert meta["cost_usd"] == 0.0
    assert meta["finish_reason"] == "stop"


async def test_mock_run_truncates_to_max_output_tokens() -> None:
    a = MockLLMActor(
        responder=lambda _msgs: "one two three four five six",
    )
    response = await a.run({"message": "x", "max_output_tokens": 3})
    assert response.message == "one two three"


async def test_mock_run_uses_custom_responder() -> None:
    a = MockLLMActor(
        responder=lambda msgs: f"got {len(msgs)} messages",
    )
    response = await a.run({"message": "hi"})
    # 1 user message (no system prompt)
    assert response.message == "got 1 messages"


# ---------------------------------------------------------------------------
# stream()
# ---------------------------------------------------------------------------


async def test_mock_streams_word_by_word_then_finalises() -> None:
    a = MockLLMActor()
    chunks: list[ActorChunk] = []
    async for chunk in a.stream({"message": "ping"}):
        chunks.append(chunk)
    intermediates = [c for c in chunks if not c.final]
    finals = [c for c in chunks if c.final]
    assert len(finals) == 1
    text = "".join(c.delta for c in intermediates)
    assert text == "[mock-reply] ping"
    assert finals[0].response is not None
    assert finals[0].response.metadata["finish_reason"] == "stop"


async def test_mock_stream_groups_words_via_chunk_words() -> None:
    a = MockLLMActor(chunk_words=2)
    chunks: list[ActorChunk] = []
    async for chunk in a.stream(
        {"message": "four word user message"}
    ):
        chunks.append(chunk)
    intermediates = [c for c in chunks if not c.final]
    text = "".join(c.delta for c in intermediates)
    assert text == "[mock-reply] four word user message"


# ---------------------------------------------------------------------------
# End-to-end with BaseKernel
# ---------------------------------------------------------------------------


async def test_basekernel_with_mock_llm_actor_produces_output() -> None:
    bus = AsyncioBus()
    actor = MockLLMActor()
    kernel = BaseKernel(actor=actor, actor_timeout=2.0)

    outputs: list[RobotOutput] = []

    async def collect_outputs(event: RobotEvent) -> None:
        if isinstance(event, RobotOutput):
            outputs.append(event)

    bus.subscribe("output.message", collect_outputs)

    await bus.start()
    try:
        await actor.start()
        await kernel.start(bus)
        await bus.publish(
            RobotInput(
                topic="input.message",
                principal="user-1",
                source="channel.test",
                run_id="run-mock-1",
                message="how are you",
                payload={"session_id": "abc"},
            )
        )
        await asyncio.sleep(0.1)
    finally:
        await kernel.stop()
        await actor.stop()
        await bus.stop()

    assert len(outputs) == 1
    out = outputs[0]
    assert out.status == "ok"
    assert out.message == "[mock-reply] how are you"
    assert out.run_id == "run-mock-1"


async def test_basekernel_phase_event_carries_mock_metadata() -> None:
    """The act-phase event surfaces provider, model and token counts.

    ``cost_usd`` is left at ``0.0`` -- the BaseKernel does not (yet)
    compute cost. When the LLMKernel lands it will look up pricing
    via :class:`~src.utility.model_catalog.ports.ModelCatalogPort`
    and override this value.
    """
    from src.bus import KERNEL_PHASE_TOPIC, KernelPhase
    from src.kernel.run import RunPhase

    bus = AsyncioBus()
    actor = MockLLMActor()
    kernel = BaseKernel(actor=actor, actor_timeout=2.0)

    phases: list[KernelPhase] = []

    async def collect_phases(event: RobotEvent) -> None:
        if isinstance(event, KernelPhase):
            phases.append(event)

    bus.subscribe(KERNEL_PHASE_TOPIC, collect_phases)

    await bus.start()
    try:
        await actor.start()
        await kernel.start(bus)
        await bus.publish(
            RobotInput(
                topic="input.message",
                principal="user",
                source="channel.test",
                run_id="run-meta",
                message="hi",
            )
        )
        await asyncio.sleep(0.1)
    finally:
        await kernel.stop()
        await actor.stop()
        await bus.stop()

    by_phase = {p.phase: p.details for p in phases}
    act = by_phase[RunPhase.ACTING.value]
    assert act["actor_name"] == "llm.mock"
    assert act["actor_status"] == "ok"
    assert act["provider"] == "mock"
    assert act["model_id"] == "mock-echo"
    assert act["tokens_in"] == 1  # "hi"
    assert act["tokens_out"] == 2  # "[mock-reply] hi"
    assert act["finish_reason"] == "stop"
    # Cost stays at 0.0 -- catalog-driven cost computation is the
    # future LLMKernel's job, not the BaseKernel's.
    assert act["cost_usd"] == 0.0
