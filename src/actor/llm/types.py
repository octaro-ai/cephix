"""Value types for LLM actor IO.

Two groups:

- **Actor IO** (:class:`ChatMessage`, :class:`LLMReply`,
  :class:`LLMDelta`, :class:`LLMUsage`): the in-process domain
  types each :class:`~src.actor.llm.actor_base.LLMActorBase`
  subclass builds for itself. Stay independent of any provider
  SDK: each driver subclass converts its native shape into these
  types.

- **Stream output** (:class:`ActorChunk`): the chunks an
  :class:`~src.actor.llm.ports.LLMActorPort` yields from its
  ``stream`` method. Last chunk carries ``final=True`` and the
  aggregated :class:`~src.actor.types.ActorResponse` for the
  closing observation.

Model metadata (:class:`ModelSpec`, :class:`ModelPricing`) lives
with the catalog in :mod:`src.utility.model_catalog.types` -- it is
not actor-specific and the catalog is a UTILITY component, not an
ACTOR. Drivers consult the catalog through
:class:`~src.utility.model_catalog.ports.ModelCatalogPort` and never
import the model-metadata types directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.actor.types import ActorResponse


# ---------------------------------------------------------------------------
# Actor IO: chat messages, replies, deltas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatMessage:
    """One message in a conversation handed to an LLM.

    SDK-agnostic on purpose: every concrete driver
    (:class:`~src.actor.llm.mock_actor.MockLLMActor`,
    :class:`~src.actor.llm.openai_actor.LLMActorOpenAI`, future
    ``LLMActorAnthropic``) converts this to its native shape
    (OpenAI's ``{"role": ..., "content": ...}``, Anthropic's
    content blocks, ...).

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

    All numeric fields are non-negative. A driver that does not
    have a count from the SDK leaves it at ``0`` rather than
    raising -- consumers that need a guarantee should sanity-check
    via :class:`~src.utility.model_catalog.types.ModelSpec` or fall
    back to a tokenizer-based estimate.

    Internal Cephix names; the mapping to the OCF ``usage`` field
    names (``input``, ``output``, ``thinking``, ``cache_read``,
    ``cache_write``, ``total``) happens at the persistence boundary
    when the kernel builds a
    :class:`~src.utility.session_store.types.SessionMessage`. Driver
    mapping per provider:

    - OpenAI: ``usage.prompt_tokens`` -> ``tokens_in``;
      ``usage.completion_tokens`` -> ``tokens_out``;
      ``usage.prompt_tokens_details.cached_tokens`` ->
      ``cache_read_tokens``; ``usage.completion_tokens_details.``
      ``reasoning_tokens`` -> ``reasoning_tokens``; ``cache_write``
      stays ``0`` (no Anthropic-style write metric).
    - Anthropic (future): ``cache_read_input_tokens`` ->
      ``cache_read_tokens``; ``cache_creation_input_tokens`` ->
      ``cache_write_tokens``; ``thinking_tokens`` ->
      ``reasoning_tokens``.

    ``cost_usd`` is filled by the **kernel** (via
    :class:`~src.utility.model_catalog.ports.ModelCatalogPort`), not
    by the driver: the driver reports counts, the kernel knows the
    pricing. Drivers therefore leave it at the default ``0.0``.
    """

    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0

    def __post_init__(self) -> None:
        if self.tokens_in < 0:
            raise ValueError("LLMUsage.tokens_in must be >= 0")
        if self.tokens_out < 0:
            raise ValueError("LLMUsage.tokens_out must be >= 0")
        if self.cost_usd < 0:
            raise ValueError("LLMUsage.cost_usd must be >= 0")
        if self.cache_read_tokens < 0:
            raise ValueError("LLMUsage.cache_read_tokens must be >= 0")
        if self.cache_write_tokens < 0:
            raise ValueError("LLMUsage.cache_write_tokens must be >= 0")
        if self.reasoning_tokens < 0:
            raise ValueError("LLMUsage.reasoning_tokens must be >= 0")


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
