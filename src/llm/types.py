"""Value types for the LLM subsystem.

Three groups of types live here:

- **Model metadata** (:class:`ModelSpec`, :class:`ModelPricing`):
  static facts about a chat model. Both share the composite key
  ``(model_id, provider)``. Spec carries capabilities and limits,
  pricing carries cost-per-token. Different consumers, different
  change cadences -- splitting them keeps the read-side concerns
  apart even though both come from the same upstream snapshot.

- **Actor IO** (:class:`ChatMessage`, :class:`LLMReply`,
  :class:`LLMDelta`, :class:`LLMUsage`): the in-process domain
  types each :class:`~src.llm.actor_base.LLMActorBase` subclass
  builds for itself. Stay independent of any provider SDK: each
  driver subclass converts its native shape into these types.

- **Stream output** (:class:`ActorChunk`): the chunks an
  :class:`~src.llm.ports.LLMActorPort` yields from its ``stream``
  method. Last chunk carries ``final=True`` and the aggregated
  :class:`~src.actor.types.ActorResponse` for the closing
  observation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.actor.types import ActorResponse


# ---------------------------------------------------------------------------
# Model metadata: spec + pricing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelSpec:
    """Static capabilities and limits of a chat model.

    Identity: ``(model_id, provider)`` is the composite key. The same
    ``model_id`` accessed via different providers (e.g. ``gpt-5`` over
    OpenAI direct vs. via OpenRouter) is treated as *different*
    ModelSpecs because pricing tables, latency characteristics and
    capability flags can differ.

    Pricing intentionally lives on a separate :class:`ModelPricing`
    dataclass: pricing changes more often than capabilities, and the
    consumers are different (kernels and limits monitors read specs;
    cost calculators read pricing).

    Fields:

    - ``model_id`` -- canonical model identifier (e.g. ``"gpt-5"``,
      ``"claude-sonnet-4-6"``).
    - ``provider`` -- provider identifier (``"openai"``,
      ``"anthropic"``, ``"openrouter"``, ``"ollama"``, ...).
    - ``context_window_tokens`` -- maximum input tokens per request.
    - ``max_output_tokens`` -- upper bound on completion tokens.
    - ``supports_function_calling`` -- model accepts tool / function
      definitions.
    - ``supports_vision`` -- model accepts image inputs.
    - ``supports_response_schema`` -- model honours JSON-Schema
      structured-output requests.
    - ``supports_system_messages`` -- model accepts a system role.
    - ``extras`` -- pass-through for source-specific fields not yet
      first-class (e.g. ``supports_prompt_caching``,
      ``supports_reasoning``, ``supports_audio_input``,
      ``deprecation_date``). Keeps the dataclass shape stable when
      upstream adds new columns.
    """

    model_id: str
    provider: str
    context_window_tokens: int
    max_output_tokens: int
    supports_function_calling: bool = False
    supports_vision: bool = False
    supports_response_schema: bool = False
    supports_system_messages: bool = True
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model_id:
            raise ValueError("ModelSpec requires a non-empty model_id")
        if not self.provider:
            raise ValueError("ModelSpec requires a non-empty provider")
        if self.context_window_tokens < 0:
            raise ValueError(
                f"ModelSpec.context_window_tokens must be >= 0, "
                f"got {self.context_window_tokens}"
            )
        if self.max_output_tokens < 0:
            raise ValueError(
                f"ModelSpec.max_output_tokens must be >= 0, "
                f"got {self.max_output_tokens}"
            )


@dataclass(frozen=True)
class ModelPricing:
    """Cost-per-token of a chat model.

    Same composite key ``(model_id, provider)`` as :class:`ModelSpec`.
    Costs are in USD *per token*, even though most upstream sources
    report per-million-tokens; the ``ModelDataSource`` adapter is
    responsible for the unit conversion so consumers always see the
    same scale.

    Both costs may be ``0.0`` for free local models (Ollama, LM
    Studio) and self-hosted vLLM deployments.

    ``extras`` mirrors the field on :class:`ModelSpec`: pass-through
    for upstream-provided pricing nuances we don't first-class
    (``cache_read_cost_per_token``, ``cache_write_cost_per_token``,
    ``input_cost_per_token_above_128k``).
    """

    model_id: str
    provider: str
    input_cost_per_token: float = 0.0
    output_cost_per_token: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model_id:
            raise ValueError("ModelPricing requires a non-empty model_id")
        if not self.provider:
            raise ValueError("ModelPricing requires a non-empty provider")
        if self.input_cost_per_token < 0:
            raise ValueError(
                f"ModelPricing.input_cost_per_token must be >= 0, "
                f"got {self.input_cost_per_token}"
            )
        if self.output_cost_per_token < 0:
            raise ValueError(
                f"ModelPricing.output_cost_per_token must be >= 0, "
                f"got {self.output_cost_per_token}"
            )


# ---------------------------------------------------------------------------
# Actor IO: chat messages, replies, deltas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatMessage:
    """One message in a conversation handed to an LLM.

    SDK-agnostic on purpose: every concrete driver
    (:class:`~src.llm.mock_actor.MockLLMActor`, future
    ``LLMActorOpenAI``, ``LLMActorAnthropic``) converts this to its
    native shape (OpenAI's ``{"role": ..., "content": ...}``,
    Anthropic's content blocks, ...).

    Roles follow the OpenAI convention:

    - ``"system"`` -- system instructions / persona.
    - ``"user"`` -- input from the user / kernel.
    - ``"assistant"`` -- prior model reply (for multi-turn).
    - ``"tool"`` -- tool result handed back to the model.
    """

    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in ("system", "user", "assistant", "tool"):
            raise ValueError(
                f"ChatMessage.role must be one of "
                f"system/user/assistant/tool, got {self.role!r}"
            )


@dataclass(frozen=True)
class LLMUsage:
    """Token counts and derived cost from one model invocation.

    All three numeric fields are non-negative. A driver that does
    not have a count from the SDK leaves it at ``0`` rather than
    raising -- consumers that need a guarantee should sanity-check
    via :class:`ModelSpec` or fall back to a tokenizer-based
    estimate.
    """

    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    def __post_init__(self) -> None:
        if self.tokens_in < 0:
            raise ValueError("LLMUsage.tokens_in must be >= 0")
        if self.tokens_out < 0:
            raise ValueError("LLMUsage.tokens_out must be >= 0")
        if self.cost_usd < 0:
            raise ValueError("LLMUsage.cost_usd must be >= 0")


@dataclass(frozen=True)
class LLMReply:
    """Aggregated reply from a non-streaming call.

    What a driver subclass returns from
    :meth:`LLMActorBase._chat_native`. The base actor turns it into
    an :class:`~src.actor.types.ActorResponse`.

    Fields:

    - ``text`` -- the assistant message body. ``None`` if the model
      produced only structured output (rare for the chat path).
    - ``finish_reason`` -- canonical reason: ``"stop"``,
      ``"length"``, ``"tool_calls"``, ``"content_filter"``,
      ``"error"``. Free-form provider extensions go in ``extras``.
    - ``usage`` -- token counts and cost.
    - ``request_id`` -- provider-side request id, useful for
      after-the-fact debugging in the provider's dashboard.
    - ``extras`` -- pass-through for provider-specific fields the
      caller wants to surface in audit notes
      (``system_fingerprint``, ``cached_prompt_tokens``, ...).
    """

    text: str | None = None
    finish_reason: str = "stop"
    usage: LLMUsage = field(default_factory=LLMUsage)
    request_id: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMDelta:
    """One chunk yielded by a driver's streaming path.

    What :meth:`LLMActorBase._stream_native` yields on each
    iteration. Most chunks carry just ``text`` -- the new piece
    since the previous chunk -- and trailing chunks may carry the
    ``finish_reason`` / ``usage`` totals.

    The base actor accumulates deltas into a final
    :class:`LLMReply`-equivalent state and re-emits them as
    :class:`ActorChunk` objects to the kernel.
    """

    text: str = ""
    finish_reason: str | None = None
    usage: LLMUsage | None = None
    extras: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Stream output: ActorChunk
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActorChunk:
    """One element of an :meth:`LLMActorPort.stream` iterator.

    Two kinds of chunks:

    - **Intermediate** (``final=False``): carries an incremental
      ``delta`` of new text. ``response`` is ``None``. The kernel
      can forward this as a partial output to the channel.
    - **Final** (``final=True``): the stream-closing chunk. Carries
      the aggregated :class:`~src.actor.types.ActorResponse` with
      the full message, the final metadata (provider, model, token
      counts, cost) and the final status. ``delta`` is empty
      (the text has been delivered as a series of intermediate
      chunks).

    Invariant (enforced in :meth:`__post_init__`):
    ``final == True`` iff ``response is not None``.
    """

    delta: str = ""
    final: bool = False
    response: ActorResponse | None = None

    def __post_init__(self) -> None:
        if self.final and self.response is None:
            raise ValueError(
                "ActorChunk(final=True) requires a non-None response"
            )
        if not self.final and self.response is not None:
            raise ValueError(
                "ActorChunk(final=False) must not carry a response"
            )
