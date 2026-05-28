"""Ports for the LLM subsystem.

Five interfaces split along the lines we drew in the design
discussion:

- :class:`ModelDataSource` -- where the model metadata snapshot
  comes from. Implementations: a bundled JSON file (offline,
  default) or a live llmprice-kit / LiteLLM mirror fetcher (online,
  audit-tracked).
- :class:`ModelCatalogPort` -- read-side of model **specifications**
  (capabilities and limits). Consumed by kernels that plan
  context-window-aware, by limits monitors, by inspector tools.
- :class:`PricingPort` -- read-side of model **pricing**. Consumed
  by cost calculators and billing dashboards.
- :class:`LLMProviderPort` -- the actual chat-completion interface
  the actor talks to. Subclassed by :class:`BaseLLMProvider`,
  implemented by :class:`MockLLMProvider`, :class:`OpenAICompatProvider`
  (later iteration), :class:`AnthropicProvider` (later).
- :class:`LLMActorPort` -- the LLM-aware extension of
  :class:`~src.actor.ports.ActorPort`. Adds streaming as a
  mandatory capability and surface ``model_id`` / ``provider`` /
  ``count_tokens`` for context-management code.

Why two ports for one service: the
:class:`~src.llm.metadata_service.ModelMetadataService` implements
both :class:`ModelCatalogPort` and :class:`PricingPort`. Consumers
inject only the slice they need (CQRS-ish: one source of truth, two
read models). A kernel that does context management never has to
know what a token costs; a billing dashboard never has to know
whether a model supports vision.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from src.actor.ports import ActorPort
from src.llm.types import (
    ActorChunk,
    ChatMessage,
    LLMDelta,
    LLMReply,
    ModelPricing,
    ModelSpec,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Source: where the metadata snapshot comes from
# ---------------------------------------------------------------------------


@runtime_checkable
class ModelDataSource(Protocol):
    """Provides the raw model metadata snapshot to the metadata service.

    Implementations:

    - :class:`~src.llm.sources.BundledLiteLLMSource` -- reads a JSON
      file shipped with cephix. Offline, deterministic, audit-silent.
    - Future: an llmprice-kit-backed source that fetches the latest
      LiteLLM mirror over the network. Audit-loud (publishes a
      :class:`~src.bus.messages.RobotAuditNote` on every refresh).

    The contract is small: load returns a dict from composite key
    ``(provider, model_id)`` to a raw row dict (LiteLLM-shaped). The
    metadata service parses the raw rows into :class:`ModelSpec` and
    :class:`ModelPricing` lazily on lookup.

    ``snapshot_id`` is an opaque identifier (timestamp, git sha,
    file hash) that lets the metadata service detect whether a
    refresh changed anything; it is included in audit notes when a
    refresh actually replaces the data.
    """

    @property
    def snapshot_id(self) -> str:
        ...

    async def load(self) -> dict[tuple[str, str], dict[str, Any]]:
        ...


# ---------------------------------------------------------------------------
# Read sides: catalog + pricing
# ---------------------------------------------------------------------------


@runtime_checkable
class ModelCatalogPort(Protocol):
    """Sync lookup for :class:`ModelSpec`.

    Consumed by kernels and limits monitors. Returns ``None`` when
    the requested ``(model_id, provider)`` is unknown -- callers
    must handle the absence case explicitly (warn, fall back to a
    conservative default, refuse to start).
    """

    def lookup(self, model_id: str, provider: str) -> ModelSpec | None:
        ...


@runtime_checkable
class PricingPort(Protocol):
    """Sync lookup for :class:`ModelPricing`.

    Consumed by cost calculators and billing dashboards. Same
    contract as :class:`ModelCatalogPort`: returns ``None`` for
    unknown models.
    """

    def lookup(self, model_id: str, provider: str) -> ModelPricing | None:
        ...


# ---------------------------------------------------------------------------
# LLM provider interface
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMProviderPort(Protocol):
    """Provider-agnostic chat-completion interface.

    Two methods, exact symmetric semantics:

    - :meth:`chat` returns a single :class:`LLMReply` after the
      provider finished generating.
    - :meth:`stream_chat` yields :class:`LLMDelta` chunks as the
      provider produces them, terminating when the model says stop.

    A provider that natively supports both implements both directly
    (OpenAI, Anthropic via their SDKs). A provider that supports only
    one inherits from :class:`~src.llm.providers.base.BaseLLMProvider`
    and gets the other via the default adapter (collect-stream for
    ``chat`` from a streaming-only provider; single-yield for
    ``stream_chat`` from a non-streaming-only provider).

    Concurrency: providers are reusable across many concurrent
    invocations. Connection pooling and rate-limit handling are the
    provider's responsibility, not the actor's.

    Errors: providers should raise an exception on transport
    failures (network, auth) and on unrecoverable provider errors.
    The :class:`~src.llm.actor.LLMActor` catches these and
    translates them into ``ActorResponse(status="error", error=...)``.
    Soft issues (rate-limit retry-recovered, content-filter warning)
    can be reported via :attr:`LLMReply.extras` so the actor surfaces
    a ``status="warn"`` if appropriate.

    Lifecycle: providers can hold connections, async clients,
    background tasks. Implementations call ``open()`` lazily on
    first use or eagerly when their owning actor starts; ``close()``
    is called when the actor stops.
    """

    @property
    def model_id(self) -> str:
        ...

    @property
    def provider(self) -> str:
        ...

    async def open(self) -> None:
        ...

    async def close(self) -> None:
        ...

    def count_tokens(self, text: str) -> int:
        """Return the provider's tokenizer count for ``text``.

        Sync because it is a local computation (no IO). Used by the
        actor's :meth:`LLMActorPort.count_tokens` and -- once we have
        an :class:`LLMKernel` -- by context-window-aware planning.

        Providers without a real tokenizer can fall back to a
        ``len(text) // 4`` heuristic; the imprecision is documented
        and limits-checking code is expected to account for it via
        a safety margin.
        """
        ...

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> LLMReply:
        ...

    def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[LLMDelta]:
        ...


# ---------------------------------------------------------------------------
# LLM actor port
# ---------------------------------------------------------------------------


class LLMActorPort(ActorPort):
    """LLM-aware extension of :class:`~src.actor.ports.ActorPort`.

    Adds three things on top of the plain ``ActorPort`` contract:

    1. **Streaming as a mandatory capability**: every LLM actor
       implements :meth:`stream`. A non-streaming provider yields a
       single final chunk (via the
       :class:`~src.llm.providers.base.BaseLLMProvider` adapter).
    2. **Identity properties** (``model_id``, ``provider``) so a
       caller can route, audit and look up specs without inspecting
       a returned response.
    3. **Token counting** (``count_tokens``) so a context-window-aware
       kernel can plan its message list before the call. Why on the
       actor and not on a separate tokenizer port: the actor knows
       its provider, and the provider knows its tokenizer. Pulling
       the tokenizer out into a separate service would multiply the
       wiring without buying anything for the canonical case.

    A future ``LLMKernel`` will accept ``LLMActorPort`` instead of
    ``ActorPort`` in its constructor, making the LLM dependency
    explicit at the type-system level. The plain
    :class:`~src.kernel.base.BaseKernel` happily accepts any
    ``ActorPort``, so an :class:`~src.llm.actor.LLMActor` works
    end-to-end with the base kernel today.

    Audit attribution stays the kernel's job: actors do not publish
    on the bus; their bookkeeping rides on
    :attr:`~src.actor.types.ActorResponse.metadata`, and the kernel
    surfaces it in :class:`~src.bus.messages.KernelPhase` /
    :class:`~src.bus.messages.RobotAuditNote` events.
    """

    @property
    def model_id(self) -> str:
        raise NotImplementedError(
            f"{type(self).__name__}.model_id not implemented"
        )

    @property
    def provider(self) -> str:
        raise NotImplementedError(
            f"{type(self).__name__}.provider not implemented"
        )

    def count_tokens(self, text: str) -> int:
        raise NotImplementedError(
            f"{type(self).__name__}.count_tokens not implemented"
        )

    def stream(self, actor_context: dict[str, Any]) -> AsyncIterator[ActorChunk]:
        raise NotImplementedError(
            f"{type(self).__name__}.stream not implemented"
        )
