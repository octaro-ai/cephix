"""Tests for :class:`LLMActor` and end-to-end with :class:`BaseKernel`.

The actor is the bridge between the kernel-actor contract
(plain :class:`ActorPort`) and the LLM provider stack. Three layers:

- direct :meth:`run` invocations against a catalog-backed mock;
- direct :meth:`stream` invocations and chunk shape;
- end-to-end with the real :class:`AsyncioBus` + :class:`BaseKernel`,
  exactly as a deployed robot would wire it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.actor.types import ActorResponse
from src.bus import AsyncioBus, RobotEvent, RobotInput, RobotOutput
from src.bus.messages import ErrorInfo
from src.components import ComponentCategory
from src.kernel.base import BaseKernel
from src.llm.actor import LLMActor
from src.llm.metadata_service import ModelMetadataService
from src.llm.providers.mock import MockLLMProvider
from src.llm.types import ActorChunk, ChatMessage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _build_stack() -> tuple[
    AsyncioBus, ModelMetadataService, MockLLMProvider, LLMActor
]:
    bus = AsyncioBus()
    await bus.start()
    service = ModelMetadataService()
    await service.start(bus)
    provider = MockLLMProvider(
        catalog=service.as_catalog_port(),
        pricing=service.as_pricing_port(),
        model_id="echo",
        provider="mock",
    )
    actor = LLMActor(
        provider=provider,
        catalog=service.as_catalog_port(),
    )
    await actor.start()
    return bus, service, provider, actor


async def _teardown_stack(
    bus: AsyncioBus,
    service: ModelMetadataService,
    actor: LLMActor,
) -> None:
    await actor.stop()
    await service.stop()
    await bus.stop()


# ---------------------------------------------------------------------------
# Identity / lifecycle
# ---------------------------------------------------------------------------


async def test_llm_actor_identity_proxies_to_provider() -> None:
    bus, service, _provider, actor = await _build_stack()
    try:
        assert actor.model_id == "echo"
        assert actor.provider == "mock"
        assert actor.count_tokens("a b c") == 3
    finally:
        await _teardown_stack(bus, service, actor)


def test_llm_actor_metadata() -> None:
    assert LLMActor.component_name == "llm"
    assert LLMActor.component_category is ComponentCategory.ACTOR


async def test_llm_actor_start_validates_against_catalog() -> None:
    bus = AsyncioBus()
    await bus.start()
    service = ModelMetadataService()
    await service.start(bus)
    provider = MockLLMProvider(
        catalog=service.as_catalog_port(),
        model_id="ghost",
        provider="mock",
    )
    actor = LLMActor(
        provider=provider,
        catalog=service.as_catalog_port(),
    )
    try:
        with pytest.raises(LookupError):
            await actor.start()
    finally:
        await service.stop()
        await bus.stop()


async def test_llm_actor_per_instance_component_name() -> None:
    """Two actors with different identities can share one robot."""
    bus, service, _provider, actor_default = await _build_stack()
    try:
        provider_b = MockLLMProvider(
            catalog=service.as_catalog_port(),
            model_id="echo",
            provider="mock",
        )
        actor_b = LLMActor(
            provider=provider_b,
            catalog=service.as_catalog_port(),
            component_name="llm.fast",
        )
        await actor_b.start()
        try:
            assert actor_default.component_name == "llm"
            assert actor_b.component_name == "llm.fast"
        finally:
            await actor_b.stop()
    finally:
        await _teardown_stack(bus, service, actor_default)


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


async def test_llm_actor_run_returns_actor_response_with_metadata() -> None:
    bus, service, _provider, actor = await _build_stack()
    try:
        response = await actor.run({"message": "hi"})
        assert isinstance(response, ActorResponse)
        assert response.status == "ok"
        assert response.message == "[mock-reply] hi"
        meta = response.metadata
        assert meta["provider"] == "mock"
        assert meta["model_id"] == "echo"
        assert meta["tokens_in"] == 1
        assert meta["tokens_out"] == 2
        assert meta["finish_reason"] == "stop"
        assert "cost_usd" in meta
    finally:
        await _teardown_stack(bus, service, actor)


async def test_llm_actor_run_reads_nested_input_message() -> None:
    bus, service, _provider, actor = await _build_stack()
    try:
        response = await actor.run({"input": {"message": "from-kernel"}})
        assert response.message == "[mock-reply] from-kernel"
    finally:
        await _teardown_stack(bus, service, actor)


async def test_llm_actor_run_returns_error_on_empty_context() -> None:
    bus, service, _provider, actor = await _build_stack()
    try:
        response = await actor.run({})
        assert response.status == "error"
        assert response.error is not None
        assert response.error.code == "actor.context.empty"
        # Identity metadata still present so the kernel can still
        # publish provider/model on the error phase event.
        assert response.metadata["provider"] == "mock"
        assert response.metadata["model_id"] == "echo"
    finally:
        await _teardown_stack(bus, service, actor)


async def test_llm_actor_run_translates_provider_error_into_actor_error() -> None:
    bus = AsyncioBus()
    await bus.start()
    service = ModelMetadataService()
    await service.start(bus)

    class _ExplodingProvider(MockLLMProvider):
        async def _chat_impl(
            self,
            messages: list[ChatMessage],
            *,
            max_output_tokens: int | None = None,
            temperature: float | None = None,
            extra: dict[str, Any] | None = None,
        ):
            del messages, max_output_tokens, temperature, extra
            raise RuntimeError("provider blew up")

    provider = _ExplodingProvider(
        catalog=service.as_catalog_port(),
        model_id="echo",
        provider="mock",
    )
    actor = LLMActor(
        provider=provider,
        catalog=service.as_catalog_port(),
    )
    await actor.start()
    try:
        response = await actor.run({"message": "hello"})
        assert response.status == "error"
        assert response.error is not None
        assert response.error.code == "provider.error"
        assert "provider blew up" in response.error.message
        assert response.error.details == {"exception_type": "RuntimeError"}
    finally:
        await actor.stop()
        await service.stop()
        await bus.stop()


async def test_llm_actor_includes_default_system_prompt() -> None:
    bus, service, _provider, _ = await _build_stack()
    captured: list[list[ChatMessage]] = []

    class _CapturingProvider(MockLLMProvider):
        async def _chat_impl(
            self,
            messages: list[ChatMessage],
            *,
            max_output_tokens: int | None = None,
            temperature: float | None = None,
            extra: dict[str, Any] | None = None,
        ):
            captured.append(list(messages))
            return await super()._chat_impl(
                messages,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                extra=extra,
            )

    capturing = _CapturingProvider(
        catalog=service.as_catalog_port(),
        model_id="echo",
        provider="mock",
    )
    actor_b = LLMActor(
        provider=capturing,
        catalog=service.as_catalog_port(),
        default_system_prompt="You are a helpful assistant.",
    )
    await actor_b.start()
    try:
        await actor_b.run({"message": "ping"})
    finally:
        await actor_b.stop()
        await service.stop()
        await bus.stop()

    assert len(captured) == 1
    msgs = captured[0]
    assert len(msgs) == 2
    assert msgs[0].role == "system"
    assert msgs[0].content == "You are a helpful assistant."
    assert msgs[1].role == "user"
    assert msgs[1].content == "ping"


async def test_llm_actor_runtime_system_overrides_default() -> None:
    bus, service, _provider, _ = await _build_stack()
    captured: list[list[ChatMessage]] = []

    class _CapturingProvider(MockLLMProvider):
        async def _chat_impl(self, messages, **kwargs):  # noqa: ANN001
            captured.append(list(messages))
            return await super()._chat_impl(messages, **kwargs)

    capturing = _CapturingProvider(
        catalog=service.as_catalog_port(),
        model_id="echo",
        provider="mock",
    )
    actor_b = LLMActor(
        provider=capturing,
        catalog=service.as_catalog_port(),
        default_system_prompt="Default.",
    )
    await actor_b.start()
    try:
        await actor_b.run({"message": "hi", "system": "Custom."})
    finally:
        await actor_b.stop()
        await service.stop()
        await bus.stop()

    msgs = captured[0]
    assert msgs[0].role == "system"
    assert msgs[0].content == "Custom."


# ---------------------------------------------------------------------------
# stream()
# ---------------------------------------------------------------------------


async def test_llm_actor_stream_yields_intermediate_then_final() -> None:
    bus, service, _provider, actor = await _build_stack()
    try:
        chunks: list[ActorChunk] = []
        async for chunk in actor.stream({"message": "hello world"}):
            chunks.append(chunk)
    finally:
        await _teardown_stack(bus, service, actor)

    intermediates = [c for c in chunks if not c.final]
    finals = [c for c in chunks if c.final]
    assert len(finals) == 1
    assert len(intermediates) >= 1

    # Concatenation reconstructs the full text.
    assert "".join(c.delta for c in intermediates) == "[mock-reply] hello world"

    final = finals[0]
    assert final.response is not None
    assert final.response.status == "ok"
    assert final.response.message == "[mock-reply] hello world"
    assert final.response.metadata["provider"] == "mock"
    assert final.response.metadata["tokens_out"] == 3
    assert final.response.metadata["finish_reason"] == "stop"


async def test_llm_actor_stream_propagates_provider_error_as_final_chunk() -> None:
    bus = AsyncioBus()
    await bus.start()
    service = ModelMetadataService()
    await service.start(bus)

    class _ExplodingStreamProvider(MockLLMProvider):
        async def _stream_impl(
            self,
            messages,  # noqa: ANN001
            *,
            max_output_tokens=None,  # noqa: ANN001
            temperature=None,  # noqa: ANN001
            extra=None,  # noqa: ANN001
        ):
            yield  # placate the parser - real generator
            raise RuntimeError("stream failed")

    provider = _ExplodingStreamProvider(
        catalog=service.as_catalog_port(),
        model_id="echo",
        provider="mock",
    )
    actor = LLMActor(
        provider=provider,
        catalog=service.as_catalog_port(),
    )
    await actor.start()
    try:
        chunks: list[ActorChunk] = []
        async for chunk in actor.stream({"message": "hi"}):
            chunks.append(chunk)
    finally:
        await actor.stop()
        await service.stop()
        await bus.stop()

    assert chunks[-1].final is True
    assert chunks[-1].response is not None
    assert chunks[-1].response.status == "error"
    assert chunks[-1].response.error is not None
    assert chunks[-1].response.error.code == "provider.error"


# ---------------------------------------------------------------------------
# End-to-end: BaseKernel + LLMActor
# ---------------------------------------------------------------------------


async def test_base_kernel_with_llm_actor_produces_mocked_output() -> None:
    """The whole stack: bus + metadata + LLMActor + BaseKernel.

    Proves that the LLMActor satisfies the plain :class:`ActorPort`
    contract end-to-end, that audit-relevant metadata reaches the
    kernel's phase events, and that a RobotOutput is published with
    the model's reply.
    """
    bus = AsyncioBus()
    await bus.start()
    service = ModelMetadataService()
    await service.start(bus)
    provider = MockLLMProvider(
        catalog=service.as_catalog_port(),
        pricing=service.as_pricing_port(),
        model_id="echo",
        provider="mock",
    )
    actor = LLMActor(
        provider=provider,
        catalog=service.as_catalog_port(),
    )
    kernel = BaseKernel(actor=actor, actor_timeout=2.0)

    outputs: list[RobotOutput] = []

    async def collect_outputs(event: RobotEvent) -> None:
        if isinstance(event, RobotOutput):
            outputs.append(event)

    bus.subscribe("output.message", collect_outputs)

    try:
        await actor.start()
        await kernel.start(bus)
        await bus.publish(
            RobotInput(
                topic="input.message",
                principal="user-1",
                source="channel.test",
                run_id="run-llm-1",
                message="how are you",
                payload={"session_id": "abc"},
            )
        )
        await asyncio.sleep(0.1)
    finally:
        await kernel.stop()
        await actor.stop()
        await service.stop()
        await bus.stop()

    assert len(outputs) == 1
    out = outputs[0]
    assert out.status == "ok"
    assert out.message == "[mock-reply] how are you"
    assert out.run_id == "run-llm-1"
    assert out.principal == "user-1"


async def test_base_kernel_phase_event_carries_llm_actor_metadata() -> None:
    """The act-phase event surfaces provider, model and token counts.

    The BaseKernel merges :attr:`ActorResponse.metadata` into
    ``ctx.phase_details`` during the act phase. With an LLMActor
    that means the next KernelPhase event carries provider, model,
    tokens_in, tokens_out and cost_usd -- the wide-event contract.
    """
    from src.bus import KERNEL_PHASE_TOPIC, KernelPhase
    from src.kernel.run import RunPhase

    bus = AsyncioBus()
    await bus.start()
    service = ModelMetadataService()
    await service.start(bus)
    provider = MockLLMProvider(
        catalog=service.as_catalog_port(),
        pricing=service.as_pricing_port(),
        model_id="echo",
        provider="mock",
    )
    actor = LLMActor(
        provider=provider,
        catalog=service.as_catalog_port(),
    )
    kernel = BaseKernel(actor=actor, actor_timeout=2.0)

    phases: list[KernelPhase] = []

    async def collect_phases(event: RobotEvent) -> None:
        if isinstance(event, KernelPhase):
            phases.append(event)

    bus.subscribe(KERNEL_PHASE_TOPIC, collect_phases)

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
        await service.stop()
        await bus.stop()

    by_phase = {p.phase: p.details for p in phases}
    act = by_phase[RunPhase.ACTING.value]
    assert act["actor_name"] == "llm"
    assert act["actor_status"] == "ok"
    # Metadata merged from the actor response onto phase details:
    assert act["provider"] == "mock"
    assert act["model_id"] == "echo"
    assert act["tokens_in"] == 1
    assert act["tokens_out"] == 2
    assert act["finish_reason"] == "stop"
