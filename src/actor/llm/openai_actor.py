"""``LLMActorOpenAI`` -- driver for OpenAI-compatible chat endpoints.

The first real LLM driver. Talks to OpenAI's chat completions API
through the official ``openai>=1.0`` Python SDK. Because the SDK
respects ``base_url``, the same driver works against every
OpenAI-compatible endpoint without code changes:

- OpenAI direct (``https://api.openai.com/v1``, the default).
- OpenAI-compatible aggregators: OpenRouter, Together, Groq,
  DeepInfra, Fireworks.
- Self-hosted OpenAI-shaped servers: vLLM, Ollama
  (``/v1/chat/completions`` shim), LM Studio.

For Anthropic and Google native APIs we ship dedicated drivers
later -- their wire shapes are too different to retrofit.

Driver responsibilities (everything else lives in
:class:`~src.actor.llm.actor_base.LLMActorBase`):

- Lazily build an :class:`openai.AsyncOpenAI` client at first use,
  with the configured ``api_key`` / ``base_url`` /
  ``organization``.
- Translate :class:`~src.actor.llm.types.ChatMessage` into the SDK's
  message dicts.
- Translate the SDK's chat completion (or streamed deltas) back
  into :class:`~src.actor.llm.types.LLMReply` /
  :class:`~src.actor.llm.types.LLMDelta` -- including ``usage`` so
  the catalog-driven cost calculation in
  :class:`~src.actor.llm.actor_base.LLMActorBase` works against real
  numbers.

Token counting: the OpenAI SDK does not expose a tokenizer (the
canonical one is ``tiktoken``). We keep the inherited
4-character heuristic; a follow-up can plug in ``tiktoken`` when
we ship the kernel-side context-window-aware planner. Token
counts in the *response* are always real -- the SDK reports them
in the ``usage`` block.

Credentials: the actor accepts ``api_key`` as a constructor
keyword. The builder resolves ``${OPENAI_KEY}`` (or any other
``${...}`` reference the user wrote in YAML) *before*
construction, so the value the actor sees is always a plain
string. A resolved-but-empty key fails fast at construction.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from src.actor.llm.actor_base import LLMActorBase
from src.actor.llm.types import ChatMessage, LLMDelta, LLMReply, LLMUsage

logger = logging.getLogger(__name__)


class LLMActorOpenAI(LLMActorBase):
    """OpenAI-compatible LLM driver.

    Constructor:

    - ``model_id`` -- model identifier the SDK will see (e.g.
      ``"gpt-4o-mini"``, ``"gpt-5"``,
      ``"meta-llama/llama-4-scout"`` for OpenRouter).
    - ``api_key`` -- the bearer token. The builder substitutes
      ``${OPENAI_KEY}`` (or whatever the user named their secret)
      before this constructor runs, so this is always a literal
      string. Empty values fail fast.
    - ``provider`` -- audit / catalog identifier. Default
      ``"openai"``. Pass ``"openrouter"`` / ``"groq"`` /
      ``"ollama"`` / ... when pointing the driver at an
      OpenAI-compatible endpoint so the catalog lookup matches
      the right pricing row.
    - ``base_url`` -- optional override of the SDK's default
      ``https://api.openai.com/v1``. Required when targeting an
      OpenAI-compatible endpoint.
    - ``organization`` / ``project`` -- optional OpenAI org/project
      identifiers; passed through to the SDK client.
    - ``default_system_prompt`` -- forwarded to
      :class:`LLMActorBase`.

    The driver does **not** take a ``ModelCatalogPort``: the SDK's
    ``usage`` block already carries real ``prompt_tokens`` /
    ``completion_tokens`` per call, so token counts come from the
    provider, not from a catalog lookup. Cost-per-token reasoning
    (and any context-window-aware planning) lives in the future
    :class:`LLMKernel`, which holds the catalog reference -- the
    actor stays a thin driver.
    - ``timeout`` -- per-request timeout in seconds. Default 60.
    - ``max_retries`` -- the SDK's built-in retry count for
      transient network errors. Default 2 (the SDK default).
    """

    component_name = "llm.openai"
    component_description = (
        "OpenAI-compatible LLM driver. Talks to chat completions "
        "endpoints via the openai>=1.0 SDK. ``base_url`` lets the "
        "same driver target OpenRouter, Groq, Together, vLLM, "
        "Ollama and other OpenAI-shaped servers without changes."
    )

    def __init__(
        self,
        *,
        model_id: str,
        api_key: str,
        provider: str = "openai",
        base_url: str | None = None,
        organization: str | None = None,
        project: str | None = None,
        default_system_prompt: str = "",
        timeout: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        super().__init__(
            model_id=model_id,
            provider=provider,
            default_system_prompt=default_system_prompt,
        )
        if not api_key:
            raise ValueError(
                "LLMActorOpenAI requires a non-empty api_key; "
                "the builder substitutes ${OPENAI_API_KEY} (or your "
                "named credential) before construction"
            )
        if timeout <= 0:
            raise ValueError("timeout must be > 0")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")

        self._api_key = api_key
        self._base_url = base_url
        self._organization = organization
        self._project = project
        self._timeout = timeout
        self._max_retries = max_retries

        # Lazily constructed at start() so the SDK's HTTP client
        # is bound to the running loop, not the loop the builder
        # happened to run on (if any).
        self._client: Any = None

    # ---- Lifecycle --------------------------------------------------------

    async def start(self) -> None:
        """Build the lazy SDK client.

        The :class:`openai.AsyncOpenAI` constructor itself does
        no IO -- it just wires up the http client. We still do
        it here (not in ``__init__``) so the underlying
        ``httpx.AsyncClient`` lifecycle stays pinned to the
        running event loop.
        """
        if self._client is not None:
            return
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            organization=self._organization,
            project=self._project,
            timeout=self._timeout,
            max_retries=self._max_retries,
        )

    async def stop(self) -> None:
        """Close the SDK client and release the underlying HTTP pool."""
        if self._client is None:
            return
        try:
            await self._client.close()
        except Exception:
            logger.exception(
                "LLMActorOpenAI failed to close its SDK client"
            )
        self._client = None

    # ---- Native chat / stream --------------------------------------------

    async def _chat_native(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMReply:
        client = self._require_client()
        kwargs = self._call_kwargs(messages, max_output_tokens, temperature)
        completion = await client.chat.completions.create(**kwargs)

        choice = completion.choices[0]
        text = choice.message.content if choice.message else None
        finish_reason = choice.finish_reason or "stop"
        usage = self._usage_from_completion(completion)
        request_id = getattr(completion, "id", "") or ""

        extras: dict[str, Any] = {}
        fingerprint = getattr(completion, "system_fingerprint", None)
        if fingerprint:
            extras["system_fingerprint"] = fingerprint

        return LLMReply(
            text=text,
            finish_reason=finish_reason,
            usage=usage,
            request_id=request_id,
            extras=extras,
        )

    async def _stream_native(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[LLMDelta]:
        client = self._require_client()
        kwargs = self._call_kwargs(messages, max_output_tokens, temperature)
        kwargs["stream"] = True
        # Ask for usage in the final chunk; the OpenAI SDK supports
        # this via ``stream_options`` and returns ``usage`` on the
        # last chunk only.
        kwargs["stream_options"] = {"include_usage": True}

        stream = await client.chat.completions.create(**kwargs)
        async for chunk in stream:
            text = ""
            finish_reason: str | None = None
            usage: LLMUsage | None = None
            extras: dict[str, Any] = {}

            if chunk.choices:
                delta = chunk.choices[0].delta
                if delta is not None and getattr(delta, "content", None):
                    text = delta.content or ""
                finish_reason = chunk.choices[0].finish_reason

            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = self._usage_from_sdk(chunk_usage)

            if text or finish_reason or usage or extras:
                yield LLMDelta(
                    text=text,
                    finish_reason=finish_reason,
                    usage=usage,
                    extras=extras,
                )

    # ---- Helpers ----------------------------------------------------------

    def _require_client(self) -> Any:
        if self._client is None:
            raise RuntimeError(
                f"LLMActorOpenAI({self._model_id!r}) used before start(); "
                "the robot's lifecycle should have started this actor."
            )
        return self._client

    def _call_kwargs(
        self,
        messages: list[ChatMessage],
        max_output_tokens: int | None,
        temperature: float | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._model_id,
            "messages": [
                {"role": m.role, "content": m.content} for m in messages
            ],
        }
        if max_output_tokens is not None and max_output_tokens > 0:
            kwargs["max_tokens"] = int(max_output_tokens)
        if temperature is not None:
            kwargs["temperature"] = float(temperature)
        return kwargs

    @classmethod
    def _usage_from_completion(cls, completion: Any) -> LLMUsage:
        usage = getattr(completion, "usage", None)
        if usage is None:
            return LLMUsage()
        return cls._usage_from_sdk(usage)

    @staticmethod
    def _usage_from_sdk(usage: Any) -> LLMUsage:
        """Translate an OpenAI SDK ``usage`` object into :class:`LLMUsage`.

        OpenAI surfaces cache and reasoning counts in nested
        ``*_tokens_details`` blocks; both blocks (and their fields)
        may be missing depending on model and request. We read
        defensively so older SDK versions and minimal mocks still
        produce a valid :class:`LLMUsage`. ``cache_write_tokens``
        stays ``0`` on this driver -- OpenAI has no equivalent of
        Anthropic's ``cache_creation_input_tokens``. ``cost_usd``
        is the kernel's responsibility (catalog-driven), not the
        driver's.
        """
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        prompt_details = getattr(usage, "prompt_tokens_details", None)
        cache_read = int(
            getattr(prompt_details, "cached_tokens", 0) or 0
        ) if prompt_details is not None else 0
        completion_details = getattr(usage, "completion_tokens_details", None)
        reasoning = int(
            getattr(completion_details, "reasoning_tokens", 0) or 0
        ) if completion_details is not None else 0
        return LLMUsage(
            tokens_in=prompt,
            tokens_out=completion,
            cost_usd=0.0,
            cache_read_tokens=cache_read,
            cache_write_tokens=0,
            reasoning_tokens=reasoning,
        )
