"""LLM subsystem: ports, types, providers, actor.

The LLM subsystem packages everything cephix needs to talk to a
language model:

- :class:`~src.llm.types.ModelSpec` and :class:`~src.llm.types.ModelPricing`
  -- canonical metadata for a chat model. Specs cover capabilities and
  limits (context window, max output, function calling, vision, ...).
  Pricing covers cost-per-token. Two separate dataclasses with the same
  ``(model_id, provider)`` composite key, because the two concerns have
  different consumers (kernels and limits monitors care about specs;
  cost calculators and billing dashboards care about pricing) and
  different change cadences.

- :class:`~src.llm.ports.ModelCatalogPort` and
  :class:`~src.llm.ports.PricingPort` -- read-only sync lookup
  interfaces for specs and pricing. Components inject only the port
  they need (DIP rein durchgezogen). Both ports are served by the
  single :class:`~src.llm.metadata_service.ModelMetadataService`
  bus component, which wraps an :class:`~src.llm.ports.ModelDataSource`
  (today: a bundled LiteLLM-style snapshot; later: an llmprice-kit
  client with an audit-tracked refresh path).

- :class:`~src.llm.ports.LLMProviderPort` plus
  :class:`~src.llm.providers.base.BaseLLMProvider` -- the provider
  abstraction that the actor talks to. Providers expose ``chat`` and
  ``stream_chat``; the base class offers default adapters so a
  provider that natively only supports one mode automatically gets
  the other for free (run-by-collecting-stream, or
  stream-by-yielding-single-chunk).

- :class:`~src.llm.ports.LLMActorPort` -- the LLM-aware extension of
  :class:`~src.actor.ports.ActorPort`. Adds streaming as a mandatory
  capability and exposes ``model_id`` / ``provider`` /
  ``count_tokens``. A future ``LLMKernel`` (Phase 2) will ask for an
  ``LLMActorPort`` in its constructor and consult the
  ``ModelCatalogPort`` for context-window-aware planning.

The :class:`~src.llm.actor.LLMActor` is the concrete actor that wires
everything together: it takes a provider, a model identity, and an
optional :class:`ModelCatalogPort`, and satisfies the LLM-actor
contract. The :class:`~src.llm.providers.mock.MockLLMProvider` lets
the whole stack run offline against the real metadata catalog --
useful for tests and for proving the architecture before the first
real provider goes online.
"""

from src.llm.actor import LLMActor
from src.llm.metadata_service import ModelMetadataService
from src.llm.ports import (
    LLMActorPort,
    LLMProviderPort,
    ModelCatalogPort,
    ModelDataSource,
    PricingPort,
)
from src.llm.providers.base import BaseLLMProvider
from src.llm.providers.mock import MockLLMProvider
from src.llm.sources import BundledLiteLLMSource
from src.llm.types import (
    ActorChunk,
    ChatMessage,
    LLMDelta,
    LLMReply,
    LLMUsage,
    ModelPricing,
    ModelSpec,
)

__all__ = [
    "ActorChunk",
    "BaseLLMProvider",
    "BundledLiteLLMSource",
    "ChatMessage",
    "LLMActor",
    "LLMActorPort",
    "LLMDelta",
    "LLMProviderPort",
    "LLMReply",
    "LLMUsage",
    "MockLLMProvider",
    "ModelCatalogPort",
    "ModelDataSource",
    "ModelMetadataService",
    "ModelPricing",
    "ModelSpec",
    "PricingPort",
]
