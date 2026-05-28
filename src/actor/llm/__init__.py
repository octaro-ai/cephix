"""LLM subsystem: catalog, ports, types, drivers.

The LLM stack is laid out along two axes:

**Off-bus utilities** (boot priority :attr:`UTILITY` = 5):

- :class:`ModelCatalog` -- the read-side view of model specs and
  pricing. Plain :class:`RobotComponent`, no bus interaction. Backed
  by a :class:`ModelDataSource`. The future ``LLMKernel`` (Phase 2)
  takes this as a constructor argument; today it is a one-consumer
  utility that runs at the side and is consulted on demand.

**Drivers** (boot priority :attr:`ACTOR` = 8):

- :class:`LLMActorBase` -- shared scaffolding. Handles context
  shaping (curated dict -> :class:`ChatMessage` list), run/stream
  symmetry (subclasses override one direction, the other is
  synthesised), :class:`ActorResponse` / :class:`ActorChunk`
  assembly, and provider-error translation.
- :class:`MockLLMActor` -- catalog-aware offline driver. Real-acting:
  computes real token counts and (with a catalog) real cost; only
  the content generation is templated.
- :class:`LLMActorOpenAI` -- driver for OpenAI-compatible chat
  endpoints. ``base_url`` retargets the same driver at OpenRouter,
  Groq, Together, vLLM, Ollama and other OpenAI-shaped servers.
- Future: ``LLMActorAnthropic`` (Anthropic SDK), ...

**Sources** (private to the catalog):

- :class:`LLMPriceKitSource` -- adapter over the ``llmprice`` lib
  (pip: ``llmprice-kit``). The default, offline by default,
  optional 24h auto-refresh against upstream LiteLLM mirror.
"""

from src.actor.llm.actor_base import LLMActorBase
from src.actor.llm.catalog import ModelCatalog
from src.actor.llm.mock_actor import MockLLMActor
from src.actor.llm.openai_actor import LLMActorOpenAI
from src.actor.llm.ports import (
    LLMActorPort,
    ModelCatalogPort,
    ModelDataSource,
)
from src.actor.llm.sources import LLMPriceKitSource
from src.actor.llm.types import (
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
    "ChatMessage",
    "LLMActorBase",
    "LLMActorOpenAI",
    "LLMActorPort",
    "LLMDelta",
    "LLMPriceKitSource",
    "LLMReply",
    "LLMUsage",
    "MockLLMActor",
    "ModelCatalog",
    "ModelCatalogPort",
    "ModelDataSource",
    "ModelPricing",
    "ModelSpec",
]
