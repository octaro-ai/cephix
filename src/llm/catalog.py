"""Lightweight model catalog parsed from the litellm pricing JSON.

This module downloads and caches the public ``model_prices_and_context_window.json``
from the litellm GitHub repository.  No litellm dependency is required.

The cache is stored at ``~/.cephix/model_catalog.json`` and refreshed at most
once per day.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.request import urlopen, Request

_CATALOG_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
_CACHE_MAX_AGE_SECONDS = 86_400  # 24 h

# Providers we surface in the picker (ordered).
_PREFERRED_PROVIDERS = [
    "anthropic",
    "openai",
    "gemini",
    "deepseek",
    "mistral",
    "groq",
    "xai",
]

_DISPLAY_LABELS: dict[str, str] = {
    "anthropic": "Anthropic (Claude)",
    "openai": "OpenAI (GPT)",
    "gemini": "Google (Gemini)",
    "deepseek": "DeepSeek",
    "mistral": "Mistral AI",
    "groq": "Groq",
    "xai": "xAI (Grok)",
    "openrouter": "OpenRouter",
    "together_ai": "Together AI",
    "fireworks_ai": "Fireworks AI",
    "bedrock": "AWS Bedrock",
    "vertex_ai": "Google Vertex AI",
    "ollama": "Ollama (local)",
}

_API_KEY_ENVS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
    "xai": "XAI_API_KEY",
}


@dataclass
class ModelInfo:
    id: str
    provider: str
    context_window: int = 0
    max_output_tokens: int = 0
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0
    supports_tools: bool = False

    @property
    def cost_label(self) -> str:
        if not self.input_cost_per_mtok:
            return ""
        return f"${self.input_cost_per_mtok:.1f}/${self.output_cost_per_mtok:.1f}"

    @property
    def context_label(self) -> str:
        if not self.context_window:
            return "-"
        return f"{self.context_window // 1000}k"


@dataclass
class ProviderInfo:
    id: str
    label: str
    api_key_env: str
    models: list[ModelInfo] = field(default_factory=list)


class ModelCatalog:
    """Model catalog backed by the litellm pricing JSON."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir or Path.home() / ".cephix"
        self._raw: dict[str, Any] | None = None

    # -- Public API -----------------------------------------------------------

    def list_providers(self, *, preferred_only: bool = False) -> list[ProviderInfo]:
        """Return available providers, preferred ones first."""
        raw = self._load()
        providers_seen: dict[str, list[ModelInfo]] = {}
        for model_id, entry in raw.items():
            if model_id.startswith("sample_spec"):
                continue
            provider = entry.get("litellm_provider", "")
            if not provider:
                continue
            if provider not in providers_seen:
                providers_seen[provider] = []
            providers_seen[provider].append(self._parse_model(model_id, entry))

        result: list[ProviderInfo] = []
        for pid in _PREFERRED_PROVIDERS:
            if pid in providers_seen:
                result.append(ProviderInfo(
                    id=pid,
                    label=_DISPLAY_LABELS.get(pid, pid),
                    api_key_env=_API_KEY_ENVS.get(pid, ""),
                    models=self._sort_models(providers_seen[pid]),
                ))

        if not preferred_only:
            for pid in sorted(providers_seen.keys()):
                if pid not in _PREFERRED_PROVIDERS:
                    result.append(ProviderInfo(
                        id=pid,
                        label=_DISPLAY_LABELS.get(pid, pid),
                        api_key_env=_API_KEY_ENVS.get(pid, ""),
                        models=self._sort_models(providers_seen[pid]),
                    ))

        return result

    def list_models(self, provider: str) -> list[ModelInfo]:
        """Return models for a specific provider, sorted by relevance."""
        raw = self._load()
        models: list[ModelInfo] = []
        for model_id, entry in raw.items():
            if model_id.startswith("sample_spec"):
                continue
            if entry.get("litellm_provider", "") == provider:
                models.append(self._parse_model(model_id, entry))
        return self._sort_models(models)

    # -- Loading / Caching ----------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if self._raw is not None:
            return self._raw

        cache_path = self._cache_dir / "model_catalog.json"

        # Try cached file first.
        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age < _CACHE_MAX_AGE_SECONDS:
                self._raw = json.loads(cache_path.read_text(encoding="utf-8"))
                return self._raw

        # Fetch from GitHub.
        try:
            self._raw = self._fetch()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(self._raw, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            # Fallback to stale cache or empty.
            if cache_path.exists():
                self._raw = json.loads(cache_path.read_text(encoding="utf-8"))
            else:
                self._raw = {}

        return self._raw

    @staticmethod
    def _fetch() -> dict[str, Any]:
        req = Request(_CATALOG_URL, headers={"User-Agent": "cephix-drp/0.1"})
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # -- Parsing / Sorting ----------------------------------------------------

    @staticmethod
    def _parse_model(model_id: str, entry: dict[str, Any]) -> ModelInfo:
        input_cost = float(entry.get("input_cost_per_token", 0))
        output_cost = float(entry.get("output_cost_per_token", 0))
        return ModelInfo(
            id=model_id,
            provider=entry.get("litellm_provider", ""),
            context_window=int(entry.get("max_input_tokens", 0) or entry.get("max_tokens", 0)),
            max_output_tokens=int(entry.get("max_output_tokens", 0)),
            input_cost_per_mtok=input_cost * 1_000_000,
            output_cost_per_mtok=output_cost * 1_000_000,
            supports_tools=bool(entry.get("supports_function_calling", False)),
        )

    @staticmethod
    def _sort_models(models: list[ModelInfo]) -> list[ModelInfo]:
        """Sort: tool-supporting first, then by context window desc, then alpha."""
        return sorted(
            models,
            key=lambda m: (
                not m.supports_tools,
                -m.context_window,
                m.id,
            ),
        )
