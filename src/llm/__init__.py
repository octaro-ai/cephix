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
- Future: ``LLMActorOpenAI`` (OpenAI-compatible endpoints, including
  Groq / Together / OpenRouter / Ollama / vLLM via ``base_url``),
  ``LLMActorAnthropic`` (Anthropic SDK), ...

**Sources** (private to the catalog):

- :class:`LLMPriceKitSource` -- adapter over the ``llmprice`` lib
  (pip: ``llmprice-kit``). The default, offline by default,
  optional 24h auto-refresh against upstream LiteLLM mirror.
"""

from src.llm.actor_base import LLMActorBase
from src.llm.catalog import ModelCatalog
from src.llm.mock_actor import MockLLMActor
from src.llm.ports import (
    LLMActorPort,
    ModelCatalogPort,
    ModelDataSource,
)
from src.llm.sources import LLMPriceKitSource
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
    "ChatMessage",
    "LLMActorBase",
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
