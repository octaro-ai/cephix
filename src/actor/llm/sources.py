"""Concrete :class:`~src.llm.ports.ModelDataSource` implementations.

Today: :class:`LLMPriceKitSource`, a thin adapter over the
``llmprice`` lib (pip package: ``llmprice-kit``) that converts
its :class:`llmprice.ModelPrice` value type into our internal
:class:`~src.llm.types.ModelSpec` and
:class:`~src.llm.types.ModelPricing`.

Two reasons we wrap the lib instead of using its types directly:

- **Domain isolation**: nothing in cephix outside this file should
  import ``llmprice``. If we ever swap to a different upstream
  (LiteLLM JSON parsed by hand, an internal mirror) the rest of
  the codebase doesn't change.
- **Unit normalisation**: ``llmprice`` reports cost
  per-million-tokens, our :class:`ModelPricing` is per-token. The
  conversion happens once, at the boundary.

The lib is offline-first: the bundled snapshot ships with the
package, no network call needed. ``auto_update=True`` flips on a
24h refresh against the upstream LiteLLM-style mirror -- we expose
that as a constructor flag and audit the refresh through the
catalog (Iteration 1b will wire that audit path).
"""

from __future__ import annotations

import logging
from typing import Any

from src.actor.llm.types import ModelPricing, ModelSpec

logger = logging.getLogger(__name__)


# Conversion factor: ``llmprice`` reports cost per 1M tokens, we
# store cost per token. Centralised here so the unit boundary is
# unambiguous.
_PER_MILLION = 1_000_000.0


class LLMPriceKitSource:
    """Adapter over the ``llmprice`` lib.

    Constructor:

    - ``auto_update`` -- forwarded to :class:`llmprice.LLMPrice`.
      ``False`` (default): use bundled data, never touch the
      network. ``True``: refresh upstream when the bundled copy is
      older than 24h. We default to ``False`` so a vanilla cephix
      boot is fully offline; auto-refresh is a deliberate opt-in
      that the catalog audits when it happens.
    - ``data_path`` -- forwarded to :class:`llmprice.LLMPrice`. Lets
      tests pin a known snapshot file.

    Lookups:

    The lib indexes models by their canonical name alone (e.g.
    ``"gpt-4o-mini"``, ``"claude-sonnet-4-6"``); the upstream entry
    carries a ``provider`` field that we cross-check. If the caller
    asks for a ``(model_id, provider)`` pair where the provider
    doesn't match the lib's record, we treat that as not-found
    rather than silently returning the wrong row.

    The lib raises :class:`KeyError` on unknown models; we catch
    and translate to ``None`` so the port contract holds.
    """

    def __init__(
        self,
        *,
        auto_update: bool = False,
        data_path: str | None = None,
    ) -> None:
        # Imported lazily so cephix doesn't hard-fail at module load
        # if the lib isn't installed in some minimal environment.
        from llmprice import LLMPrice  # type: ignore[import-not-found]

        self._client = LLMPrice(auto_update=auto_update, data_path=data_path)
        self._auto_update = auto_update
        self._data_path = data_path

    @property
    def snapshot_id(self) -> str:
        """Lib-reported snapshot age plus a stable component prefix."""
        # The lib doesn't expose a hash; use ``data_age`` text plus
        # total_models as a poor-man's snapshot id.  Audit notes
        # only need to detect *change*, not be cryptographically
        # robust.
        return f"llmprice:{self._client.total_models}:{self._client.data_age()}"

    def load_spec(self, model_id: str, provider: str) -> ModelSpec | None:
        row = self._lookup_row(model_id, provider)
        if row is None:
            return None
        extras = self._extras(row)
        return ModelSpec(
            model_id=row.name,
            provider=row.provider,
            context_window_tokens=int(row.max_input_tokens),
            max_output_tokens=int(row.max_output_tokens),
            supports_function_calling=bool(row.supports_function_calling),
            supports_vision=bool(row.supports_vision),
            supports_response_schema=bool(row.supports_response_schema),
            # ``llmprice`` doesn't track ``supports_system_messages``;
            # default True (almost every modern chat model does).
            supports_system_messages=True,
            extras=extras,
        )

    def load_pricing(
        self, model_id: str, provider: str
    ) -> ModelPricing | None:
        row = self._lookup_row(model_id, provider)
        if row is None:
            return None
        return ModelPricing(
            model_id=row.name,
            provider=row.provider,
            input_cost_per_token=float(row.input_cost_per_1m) / _PER_MILLION,
            output_cost_per_token=float(row.output_cost_per_1m) / _PER_MILLION,
            extras={
                "cache_read_cost_per_token": (
                    float(row.cache_read_cost_per_1m) / _PER_MILLION
                ),
                "cache_write_cost_per_token": (
                    float(row.cache_write_cost_per_1m) / _PER_MILLION
                ),
            },
        )

    def refresh(self) -> None:
        """Fetch the latest pricing data from upstream.

        Forwarded to :meth:`llmprice.LLMPrice.update`. Synchronous
        network IO -- the catalog wraps this in an audit-tracked
        path. Iteration 1b makes that path bus-emitting.
        """
        self._client.update()

    # ---- internals --------------------------------------------------------

    def _lookup_row(self, model_id: str, provider: str) -> Any:
        try:
            row = self._client.get(model_id)
        except KeyError:
            logger.debug(
                "LLMPriceKitSource: model %r not found in upstream snapshot",
                model_id,
            )
            return None
        if provider and row.provider != provider:
            logger.debug(
                "LLMPriceKitSource: provider mismatch for %r: "
                "asked %r, snapshot says %r",
                model_id,
                provider,
                row.provider,
            )
            return None
        return row

    @staticmethod
    def _extras(row: Any) -> dict[str, Any]:
        """Pass-through capabilities the spec doesn't first-class."""
        return {
            "mode": row.mode,
            "supports_prompt_caching": bool(row.supports_prompt_caching),
            "supports_reasoning": bool(row.supports_reasoning),
            "supports_audio_input": bool(row.supports_audio_input),
            "supports_audio_output": bool(row.supports_audio_output),
            "supports_web_search": bool(row.supports_web_search),
            "deprecation_date": row.deprecation_date,
        }
