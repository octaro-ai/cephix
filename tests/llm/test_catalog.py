"""Tests for :class:`ModelCatalog` and :class:`LLMPriceKitSource`.

The catalog is the UTILITY-tier read-side view of model metadata.
It delegates all storage to the source; the source for production
is :class:`LLMPriceKitSource`, which wraps the ``llmprice`` lib.
"""

from __future__ import annotations

import pytest

from src.components import ComponentCategory, RobotComponent
from src.llm.catalog import ModelCatalog
from src.llm.ports import ModelCatalogPort, ModelDataSource
from src.llm.sources import LLMPriceKitSource
from src.llm.types import ModelPricing, ModelSpec


# ---------------------------------------------------------------------------
# ModelCatalog: identity + lifecycle
# ---------------------------------------------------------------------------


def test_model_catalog_metadata() -> None:
    assert ModelCatalog.component_name == "model-catalog"
    assert ModelCatalog.component_category is ComponentCategory.UTILITY


def test_model_catalog_is_plain_robot_component() -> None:
    """No bus interaction: stays a RobotComponent, not a BusComponent."""
    from src.components import BusComponent

    catalog = ModelCatalog(source=_FakeSource({}))
    assert isinstance(catalog, RobotComponent)
    assert not isinstance(catalog, BusComponent)


def test_model_catalog_implements_port() -> None:
    catalog = ModelCatalog(source=_FakeSource({}))
    assert isinstance(catalog, ModelCatalogPort)


async def test_model_catalog_lifecycle_is_noop() -> None:
    catalog = ModelCatalog(source=_FakeSource({}))
    await catalog.start()
    await catalog.stop()


# ---------------------------------------------------------------------------
# ModelCatalog: lookups via fake source
# ---------------------------------------------------------------------------


class _FakeSource:
    """Minimal :class:`ModelDataSource` for tests."""

    def __init__(
        self,
        rows: dict[tuple[str, str], tuple[ModelSpec | None, ModelPricing | None]],
        *,
        snapshot_id: str = "fake-1",
    ) -> None:
        self._rows = rows
        self._snapshot_id = snapshot_id

    @property
    def snapshot_id(self) -> str:
        return self._snapshot_id

    def load_spec(self, model_id: str, provider: str) -> ModelSpec | None:
        return self._rows.get((provider, model_id), (None, None))[0]

    def load_pricing(
        self, model_id: str, provider: str
    ) -> ModelPricing | None:
        return self._rows.get((provider, model_id), (None, None))[1]


def test_fake_source_satisfies_protocol() -> None:
    source = _FakeSource({})
    assert isinstance(source, ModelDataSource)


def test_catalog_returns_spec_and_pricing_from_source() -> None:
    spec = ModelSpec(
        model_id="m",
        provider="p",
        context_window_tokens=10,
        max_output_tokens=2,
    )
    pricing = ModelPricing(
        model_id="m",
        provider="p",
        input_cost_per_token=0.001,
        output_cost_per_token=0.002,
    )
    catalog = ModelCatalog(source=_FakeSource({("p", "m"): (spec, pricing)}))
    assert catalog.lookup_spec("m", "p") == spec
    assert catalog.lookup_pricing("m", "p") == pricing


def test_catalog_returns_none_for_unknown_model() -> None:
    catalog = ModelCatalog(source=_FakeSource({}))
    assert catalog.lookup_spec("ghost", "openai") is None
    assert catalog.lookup_pricing("ghost", "openai") is None


def test_catalog_snapshot_id_forwards_source() -> None:
    catalog = ModelCatalog(
        source=_FakeSource({}, snapshot_id="snapshot-xyz")
    )
    assert catalog.snapshot_id == "snapshot-xyz"


# ---------------------------------------------------------------------------
# LLMPriceKitSource: real lib, well-known models
# ---------------------------------------------------------------------------


def test_llmprice_kit_source_resolves_known_openai_model() -> None:
    """gpt-4o-mini ships in the bundled lib data; pricing is known."""
    source = LLMPriceKitSource()
    spec = source.load_spec("gpt-4o-mini", "openai")
    assert spec is not None
    assert spec.model_id == "gpt-4o-mini"
    assert spec.provider == "openai"
    assert spec.context_window_tokens > 0
    assert spec.max_output_tokens > 0
    assert spec.supports_vision is True
    assert spec.supports_function_calling is True

    pricing = source.load_pricing("gpt-4o-mini", "openai")
    assert pricing is not None
    # Per-token, derived from per-1M. The lib reports 0.15 / 0.6
    # USD per 1M tokens, so our per-token values are these / 1M.
    assert pricing.input_cost_per_token == pytest.approx(
        0.15 / 1_000_000, rel=1e-6
    )
    assert pricing.output_cost_per_token == pytest.approx(
        0.6 / 1_000_000, rel=1e-6
    )


def test_llmprice_kit_source_returns_none_for_unknown_model() -> None:
    source = LLMPriceKitSource()
    assert source.load_spec("does-not-exist-model-xyz", "openai") is None
    assert source.load_pricing("does-not-exist-model-xyz", "openai") is None


def test_llmprice_kit_source_returns_none_on_provider_mismatch() -> None:
    """A real model id with the wrong provider must be reported as missing.

    Prevents silent fallback to the lib's recorded provider when the
    caller asked for a different vendor (e.g. routed via OpenRouter).
    """
    source = LLMPriceKitSource()
    assert source.load_spec("gpt-4o-mini", "anthropic") is None


def test_llmprice_kit_source_snapshot_id_includes_total_models() -> None:
    source = LLMPriceKitSource()
    snap = source.snapshot_id
    assert snap.startswith("llmprice:")
    parts = snap.split(":")
    assert int(parts[1]) > 100  # the lib ships hundreds of models


def test_llmprice_kit_source_provides_extras() -> None:
    """Pass-through of secondary capabilities."""
    source = LLMPriceKitSource()
    spec = source.load_spec("gpt-4o-mini", "openai")
    assert spec is not None
    assert "supports_prompt_caching" in spec.extras
    assert "mode" in spec.extras
    assert spec.extras["mode"] == "chat"
