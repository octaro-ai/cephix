"""LLM actor subsystem: drivers + actor IO types.

LLM-side actor stack laid out as drivers around a shared base. All
boot at :attr:`~src.components.ComponentCategory.ACTOR` (boot
priority 8).

**Drivers**:

- :class:`LLMActorBase` -- shared scaffolding. Handles context
  shaping (curated dict -> :class:`ChatMessage` list), run/stream
  symmetry (subclasses override one direction, the other is
  synthesised), :class:`~src.actor.types.ActorResponse` /
  :class:`ActorChunk` assembly, and provider-error translation.
- :class:`MockLLMActor` -- catalog-aware offline driver. Real-acting:
  computes real token counts and (with a catalog) real cost; only
  the content generation is templated.
- :class:`LLMActorOpenAI` -- driver for OpenAI-compatible chat
  endpoints. ``base_url`` retargets the same driver at OpenRouter,
  Groq, Together, vLLM, Ollama and other OpenAI-shaped servers.
- Future: ``LLMActorAnthropic`` (Anthropic SDK), ...

**Actor IO types**: :class:`ChatMessage`, :class:`LLMReply`,
:class:`LLMDelta`, :class:`LLMUsage`, :class:`ActorChunk`. SDK-
agnostic, every driver converts its native shape to these.

Catalog-side concerns (model spec / pricing / data source) live in
:mod:`src.utility.model_catalog`; drivers consume the catalog
through that package's
:class:`~src.utility.model_catalog.ports.ModelCatalogPort`.
"""

from src.actor.llm.actor_base import LLMActorBase
from src.actor.llm.mock_actor import MockLLMActor
from src.actor.llm.openai_actor import LLMActorOpenAI
from src.actor.llm.ports import LLMActorPort
from src.actor.llm.types import (
    ActorChunk,
    ChatMessage,
    LLMDelta,
    LLMReply,
    LLMUsage,
)

__all__ = [
    "ActorChunk",
    "ChatMessage",
    "LLMActorBase",
    "LLMActorOpenAI",
    "LLMActorPort",
    "LLMDelta",
    "LLMReply",
    "LLMUsage",
    "MockLLMActor",
]
