"""Tests for :class:`MockLLMActor`.

The mock is the canonical end-to-end test driver. It must:

- consult an injected catalog when present (real cost computation)
- still work without a catalog (zero cost, but realistic tokens)
- stream word-by-word
- end-to-end work with the real :class:`AsyncioBus` +
  :class:`BaseKernel`
"""

from __future__ import annotations

import asyncio

import pytest

from src.actor.types import ActorResponse
from src.bus import AsyncioBus, RobotEvent, RobotInput, RobotOutput
from src.components import ComponentCategory
from src.kernel.base import BaseKernel
from src.actor.llm.catalog import ModelCatalog
from src.actor.llm.mock_actor import MockLLMActor
from src.actor.llm.types import ActorChunk, ChatMessage, ModelPricing, ModelSpec


class _FakeSource:
    def __init__(self, rows: dict[tuple[str, str], tuple]) -> None:
        self._rows = rows

    @property
    def snapshot_id(self) -> str:
        return "fake"

    def load_spec(self, model_id, provider):  # noqa: ANN001
        return self._rows.get((provider, model_id), (None, None))[0]

    def load_pricing(self, model_id, provider):  # noqa: ANN001
        return self._rows.get((provider, model_id), (None, None))[1]


def _catalog_with(model_id: str, provider: str) -> ModelCatalog:
    spec = ModelSpec(
        model_id=model_id,
        provider=provider,
        context_window_tokens=8192,
        max_output_tokens=4096,
    )
    pricing = ModelPricing(
        model_id=model_id,
        provider=provider,
        input_cost_per_token=0.001,
        output_cost_per_token=0.002,
    )
    return ModelCatalog(
        source=_FakeSource({(provider, model_id): (spec, pricing)})
    )


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


async def test_mock_run_without_catalog_returns_zero_cost() -> None:
    a = MockLLMActor()
    response = await a.run({"message": "hello world"})
    assert response.status == "ok"
    assert response.message == "[mock-reply] hello world"
    meta = response.metadata
    assert meta["provider"] == "mock"
    assert meta["model_id"] == "mock-echo"
    assert meta["tokens_in"] == 2  # "hello world"
    assert meta["tokens_out"] == 3  # "[mock-reply] hello world"
    assert meta["cost_usd"] == 0.0  # no catalog
    assert meta["finish_reason"] == "stop"


async def test_mock_run_with_catalog_computes_real_cost() -> None:
    catalog = _catalog_with("mock-echo", "mock")
    a = MockLLMActor(catalog=catalog)
    response = await a.run({"message": "hello world"})
    # input: 2 tokens * 0.001 + output: 3 * 0.002
    expected = 2 * 0.001 + 3 * 0.002
    assert response.metadata["cost_usd"] == pytest.approx(expected)


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
    actor = MockLLMActor(catalog=_catalog_with("mock-echo", "mock"))
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
    """The act-phase event surfaces provider, model and token counts."""
    from src.bus import KERNEL_PHASE_TOPIC, KernelPhase
    from src.kernel.run import RunPhase

    bus = AsyncioBus()
    actor = MockLLMActor(catalog=_catalog_with("mock-echo", "mock"))
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
    # Cost is computed from the catalog pricing.
    assert act["cost_usd"] == pytest.approx(1 * 0.001 + 2 * 0.002)
