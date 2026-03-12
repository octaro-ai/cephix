"""LLM provider factory — resolves provider from configuration."""

from __future__ import annotations

import logging
import os
from typing import Any

from src.llm.models import LLMCompletion, LLMMessage
from src.llm.ports import LLMPort

logger = logging.getLogger(__name__)


def create_llm_provider(
    config: dict[str, Any],
    *,
    secret_resolver: Any | None = None,
) -> LLMPort | None:
    """Create an LLM provider from a config dict.

    Expected config shape (from robot.yaml):

        llm:
          provider: anthropic  # or: openai, litellm, stub
          model: claude-sonnet-4-20250514
          api_key_env: ANTHROPIC_API_KEY  # env var name
          temperature: 0.7
          max_tokens: 4096
          thinking_budget_tokens: 1024  # optional, for extended thinking
          base_url: https://...  # optional, for OpenAI-compatible endpoints

    If provider is missing or empty, returns None (keyword fallback).
    """
    llm_config = config.get("llm", {})
    if not llm_config:
        return None

    provider_name = str(llm_config.get("provider", "")).strip().lower()
    if not provider_name:
        return None

    model = llm_config.get("model")
    api_key_env = str(llm_config.get("api_key_env", ""))
    api_key = _resolve_api_key(api_key_env, provider_name, secret_resolver)
    base_url = llm_config.get("base_url")

    # Cloud providers need an API key — fall back to keyword mode without one.
    _REQUIRES_API_KEY = {"anthropic", "openai"}
    if provider_name in _REQUIRES_API_KEY and not api_key:
        logger.warning("No API key found for %s (env var: %s) — falling back to keyword mode", provider_name, api_key_env)
        return None

    if provider_name == "anthropic":
        from src.llm.anthropic import AnthropicProvider

        kwargs: dict[str, Any] = {"api_key": api_key}
        if model:
            kwargs["default_model"] = model
        if llm_config.get("max_tokens"):
            kwargs["default_max_tokens"] = int(llm_config["max_tokens"])
        if llm_config.get("thinking_budget_tokens"):
            kwargs["thinking_budget_tokens"] = int(llm_config["thinking_budget_tokens"])
        return AnthropicProvider(**kwargs)

    if provider_name == "openai":
        from src.llm.openai import OpenAIProvider

        kwargs = {"api_key": api_key}
        if model:
            kwargs["default_model"] = model
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAIProvider(**kwargs)

    if provider_name == "litellm":
        from src.llm.litellm import LiteLLMProvider

        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key
        if model:
            kwargs["default_model"] = model
        if base_url:
            kwargs["api_base"] = base_url
        return LiteLLMProvider(**kwargs)

    if provider_name == "stub":
        from src.llm.stub import StubLLMProvider
        return StubLLMProvider()

    raise ValueError(f"Unknown LLM provider: {provider_name!r}")


def _resolve_api_key(
    api_key_env: str,
    provider_name: str,
    secret_resolver: Any | None = None,
) -> str:
    """Resolve API key from explicit env var name, or standard defaults.

    If *secret_resolver* is provided it is called as
    ``secret_resolver(env_var_name) -> str`` and should implement the
    layered lookup (instance .env → global .env → OS env).  Without one
    the function falls back to ``os.environ``.
    """
    # Standard env var names per provider.
    defaults = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    env_var = api_key_env or defaults.get(provider_name, "")
    if not env_var:
        return ""

    if secret_resolver is not None:
        return secret_resolver(env_var)
    return os.environ.get(env_var, "")
