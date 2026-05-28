"""Tests for :class:`LLMActorOpenAI` using a stubbed OpenAI SDK client.

The driver itself is thin: the heavy lifting (context shaping,
run/stream symmetry, error translation, ActorResponse assembly)
lives in :class:`LLMActorBase`. These tests focus on what
:class:`LLMActorOpenAI` is uniquely responsible for:

- translating :class:`ChatMessage` into the SDK's wire shape;
- forwarding ``model_id`` / ``temperature`` / ``max_output_tokens``
  to the SDK;
- mapping the SDK's chat completion response back into
  :class:`LLMReply`;
- mapping the SDK's streaming deltas into :class:`LLMDelta`;
- lifecycle: :meth:`start` constructs a client; :meth:`stop`
  closes it; :meth:`run` before :meth:`start` errors loudly.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.actor.types import ActorResponse
from src.actor.llm.openai_actor import LLMActorOpenAI


# ---------------------------------------------------------------------------
# Fakes that mimic the openai>=1.0 SDK shapes
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(
        self,
        content: str | None = None,
        finish_reason: str | None = "stop",
        delta: Any = None,
    ) -> None:
        self.message = _FakeMessage(content) if content is not None else None
        self.finish_reason = finish_reason
        self.delta = delta


class _FakeUsage:
    def __init__(self, prompt: int = 0, completion: int = 0) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion


class _FakeCompletion:
    def __init__(
        self,
        *,
        text: str | None,
        usage: _FakeUsage | None = None,
        request_id: str = "fake-req-1",
        finish_reason: str = "stop",
        fingerprint: str | None = None,
    ) -> None:
        self.choices = [
            _FakeChoice(content=text, finish_reason=finish_reason)
        ]
        self.usage = usage
        self.id = request_id
        self.system_fingerprint = fingerprint


class _FakeStreamChunk:
    """One streaming chunk from the SDK."""

    def __init__(
        self,
        *,
        delta_text: str = "",
        finish_reason: str | None = None,
        usage: _FakeUsage | None = None,
    ) -> None:
        delta = type(
            "Delta", (), {"content": delta_text or None}
        )()
        self.choices = [
            _FakeChoice(finish_reason=finish_reason, delta=delta)
        ] if (delta_text or finish_reason) else []
        self.usage = usage


class _FakeAsyncStream:
    """Async-iterable wrapper around a list of chunks."""

    def __init__(self, chunks: list[_FakeStreamChunk]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> "_FakeAsyncStream":
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self) -> _FakeStreamChunk:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _FakeChatCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.next_return: Any = None

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.next_return


class _FakeChat:
    def __init__(self, completions: _FakeChatCompletions) -> None:
        self.completions = completions


class _FakeAsyncOpenAI:
    """Pretends to be ``openai.AsyncOpenAI`` for the duration of one test."""

    last_init_kwargs: dict[str, Any] | None = None

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_init_kwargs = kwargs
        self._completions = _FakeChatCompletions()
        self.chat = _FakeChat(self._completions)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def patched_openai(monkeypatch: pytest.MonkeyPatch):
    """Patch ``openai.AsyncOpenAI`` to our fake."""
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)
    yield _FakeAsyncOpenAI


# ---------------------------------------------------------------------------
# Construction-time validation
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_requires_non_empty_api_key(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            LLMActorOpenAI(model_id="gpt-4o-mini", api_key="")

    def test_default_provider_is_openai(self) -> None:
        actor = LLMActorOpenAI(model_id="gpt-4o-mini", api_key="sk-x")
        assert actor.provider == "openai"

    def test_custom_provider(self) -> None:
        actor = LLMActorOpenAI(
            model_id="meta-llama/llama-4",
            api_key="sk-x",
            provider="openrouter",
        )
        assert actor.provider == "openrouter"

    def test_rejects_non_positive_timeout(self) -> None:
        with pytest.raises(ValueError, match="timeout"):
            LLMActorOpenAI(model_id="x", api_key="k", timeout=0)

    def test_rejects_negative_max_retries(self) -> None:
        with pytest.raises(ValueError, match="max_retries"):
            LLMActorOpenAI(model_id="x", api_key="k", max_retries=-1)

    def test_component_metadata(self) -> None:
        from src.components import ComponentCategory

        assert LLMActorOpenAI.component_name == "llm.openai"
        assert LLMActorOpenAI.component_category is ComponentCategory.ACTOR


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_start_constructs_sdk_client_with_credentials(
        self, patched_openai
    ) -> None:
        actor = LLMActorOpenAI(
            model_id="gpt-4o-mini",
            api_key="sk-test",
            base_url="https://router.example.com/v1",
            organization="my-org",
            project="my-project",
            timeout=30.0,
            max_retries=3,
        )
        await actor.start()
        try:
            assert _FakeAsyncOpenAI.last_init_kwargs is not None
            kwargs = _FakeAsyncOpenAI.last_init_kwargs
            assert kwargs["api_key"] == "sk-test"
            assert kwargs["base_url"] == "https://router.example.com/v1"
            assert kwargs["organization"] == "my-org"
            assert kwargs["project"] == "my-project"
            assert kwargs["timeout"] == 30.0
            assert kwargs["max_retries"] == 3
        finally:
            await actor.stop()

    async def test_start_is_idempotent(self, patched_openai) -> None:
        actor = LLMActorOpenAI(model_id="gpt-4o-mini", api_key="k")
        await actor.start()
        client_before = actor._client
        await actor.start()
        assert actor._client is client_before
        await actor.stop()

    async def test_stop_closes_client(self, patched_openai) -> None:
        actor = LLMActorOpenAI(model_id="gpt-4o-mini", api_key="k")
        await actor.start()
        client = actor._client
        await actor.stop()
        assert client.closed is True
        assert actor._client is None

    async def test_run_before_start_raises(self, patched_openai) -> None:
        actor = LLMActorOpenAI(model_id="gpt-4o-mini", api_key="k")
        # Don't start; the base class catches RuntimeError and turns
        # it into a status="error" response with code=provider.error.
        response = await actor.run({"message": "hi"})
        assert response.status == "error"
        assert response.error is not None
        assert response.error.code == "provider.error"


# ---------------------------------------------------------------------------
# Non-streaming path
# ---------------------------------------------------------------------------


class TestRun:
    async def test_run_translates_response(self, patched_openai) -> None:
        actor = LLMActorOpenAI(model_id="gpt-4o-mini", api_key="k")
        await actor.start()
        try:
            actor._client._completions.next_return = _FakeCompletion(
                text="Hello, world.",
                usage=_FakeUsage(prompt=12, completion=5),
                request_id="req-abc",
                fingerprint="fp_xyz",
            )
            response: ActorResponse = await actor.run(
                {"message": "Say hi.", "system": "You are friendly."}
            )
            assert response.status == "ok"
            assert response.message == "Hello, world."
            md = response.metadata
            assert md["provider"] == "openai"
            assert md["model_id"] == "gpt-4o-mini"
            assert md["tokens_in"] == 12
            assert md["tokens_out"] == 5
            assert md["finish_reason"] == "stop"
            assert md["request_id"] == "req-abc"
            assert md["provider_extras"]["system_fingerprint"] == "fp_xyz"
        finally:
            await actor.stop()

    async def test_run_forwards_message_list(self, patched_openai) -> None:
        actor = LLMActorOpenAI(model_id="gpt-4o-mini", api_key="k")
        await actor.start()
        try:
            actor._client._completions.next_return = _FakeCompletion(
                text="ok", usage=_FakeUsage(1, 1)
            )
            await actor.run({"message": "Hello.", "system": "You are X."})
            call = actor._client._completions.calls[0]
            assert call["model"] == "gpt-4o-mini"
            messages = call["messages"]
            assert {"role": "system", "content": "You are X."} in messages
            assert {"role": "user", "content": "Hello."} in messages
        finally:
            await actor.stop()

    async def test_run_forwards_temperature_and_max_tokens(
        self, patched_openai
    ) -> None:
        actor = LLMActorOpenAI(model_id="gpt-4o-mini", api_key="k")
        await actor.start()
        try:
            actor._client._completions.next_return = _FakeCompletion(
                text="ok", usage=_FakeUsage(1, 1)
            )
            await actor.run(
                {
                    "message": "x",
                    "temperature": 0.7,
                    "max_output_tokens": 100,
                }
            )
            call = actor._client._completions.calls[0]
            assert call["temperature"] == 0.7
            assert call["max_tokens"] == 100
        finally:
            await actor.stop()

    async def test_run_omits_optional_args_when_unset(
        self, patched_openai
    ) -> None:
        actor = LLMActorOpenAI(model_id="gpt-4o-mini", api_key="k")
        await actor.start()
        try:
            actor._client._completions.next_return = _FakeCompletion(
                text="ok", usage=_FakeUsage(1, 1)
            )
            await actor.run({"message": "x"})
            call = actor._client._completions.calls[0]
            assert "temperature" not in call
            assert "max_tokens" not in call
        finally:
            await actor.stop()

    async def test_sdk_exception_becomes_error_response(
        self, patched_openai
    ) -> None:
        actor = LLMActorOpenAI(model_id="gpt-4o-mini", api_key="k")
        await actor.start()
        try:
            class _Boom(Exception):
                pass

            async def _raise(**_: Any) -> Any:
                raise _Boom("rate limit exceeded")

            actor._client._completions.create = _raise  # type: ignore[assignment]

            response = await actor.run({"message": "hi"})
            assert response.status == "error"
            assert response.error is not None
            assert response.error.code == "provider.error"
            assert "rate limit exceeded" in response.error.message
        finally:
            await actor.stop()


# ---------------------------------------------------------------------------
# Streaming path
# ---------------------------------------------------------------------------


class TestStream:
    async def test_stream_yields_chunks_then_final(self, patched_openai) -> None:
        actor = LLMActorOpenAI(model_id="gpt-4o-mini", api_key="k")
        await actor.start()
        try:
            chunks = [
                _FakeStreamChunk(delta_text="Hello"),
                _FakeStreamChunk(delta_text=", world"),
                _FakeStreamChunk(delta_text="."),
                _FakeStreamChunk(
                    finish_reason="stop",
                    usage=_FakeUsage(prompt=8, completion=4),
                ),
            ]
            actor._client._completions.next_return = _FakeAsyncStream(chunks)

            collected: list[Any] = []
            async for chunk in actor.stream({"message": "hi"}):
                collected.append(chunk)

            text_chunks = [c for c in collected if not c.final]
            final_chunks = [c for c in collected if c.final]

            assert len(final_chunks) == 1
            assert (
                "".join(c.delta for c in text_chunks) == "Hello, world."
            )

            final = final_chunks[0]
            assert final.response is not None
            assert final.response.status == "ok"
            assert final.response.message == "Hello, world."
            md = final.response.metadata
            assert md["tokens_in"] == 8
            assert md["tokens_out"] == 4
            assert md["finish_reason"] == "stop"
        finally:
            await actor.stop()

    async def test_stream_requests_usage_in_final_chunk(
        self, patched_openai
    ) -> None:
        """The driver passes ``stream_options.include_usage`` to the SDK."""
        actor = LLMActorOpenAI(model_id="gpt-4o-mini", api_key="k")
        await actor.start()
        try:
            actor._client._completions.next_return = _FakeAsyncStream([
                _FakeStreamChunk(finish_reason="stop")
            ])

            async for _ in actor.stream({"message": "hi"}):
                pass

            call = actor._client._completions.calls[0]
            assert call["stream"] is True
            assert call["stream_options"] == {"include_usage": True}
        finally:
            await actor.stop()
