"""Concrete :class:`~src.llm.ports.LLMProviderPort` implementations.

Today: :class:`BaseLLMProvider` (scaffolding) and
:class:`MockLLMProvider` (offline, catalog-aware fake).

Tomorrow (Iteration 1b): :class:`OpenAICompatProvider` -- a single
provider that talks to any OpenAI-compatible endpoint by setting
``base_url``, covering OpenAI, Groq, Together, OpenRouter, vLLM,
Ollama, ... in one adapter.
"""

from src.llm.providers.base import BaseLLMProvider
from src.llm.providers.mock import MockLLMProvider

__all__ = ["BaseLLMProvider", "MockLLMProvider"]
