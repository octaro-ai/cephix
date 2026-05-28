"""Tests for the catalog value types.

Covers invariants on :class:`ModelSpec` and :class:`ModelPricing`.
The fields are dataclass-shallow but the validation rules in
:meth:`__post_init__` are load-bearing for the rest of the stack.
"""

from __future__ import annotations

import pytest

from src.utility.model_catalog.types import ModelPricing, ModelSpec


# ---------------------------------------------------------------------------
# ModelSpec
# ---------------------------------------------------------------------------


def test_model_spec_minimal() -> None:
    spec = ModelSpec(
        model_id="gpt-5",
        provider="openai",
        context_window_tokens=272_000,
        max_output_tokens=128_000,
    )
    assert spec.model_id == "gpt-5"
    assert spec.supports_function_calling is False
    assert spec.supports_system_messages is True
    assert spec.extras == {}


def test_model_spec_rejects_empty_id_or_provider() -> None:
    with pytest.raises(ValueError, match="model_id"):
        ModelSpec(
            model_id="",
            provider="openai",
            context_window_tokens=1,
            max_output_tokens=1,
        )
    with pytest.raises(ValueError, match="provider"):
        ModelSpec(
            model_id="x",
            provider="",
            context_window_tokens=1,
            max_output_tokens=1,
        )


def test_model_spec_rejects_negative_limits() -> None:
    with pytest.raises(ValueError, match="context_window"):
        ModelSpec(
            model_id="x",
            provider="y",
            context_window_tokens=-1,
            max_output_tokens=1,
        )
    with pytest.raises(ValueError, match="max_output"):
        ModelSpec(
            model_id="x",
            provider="y",
            context_window_tokens=1,
            max_output_tokens=-1,
        )


# ---------------------------------------------------------------------------
# ModelPricing
# ---------------------------------------------------------------------------


def test_model_pricing_zero_cost_is_valid_for_local_models() -> None:
    pricing = ModelPricing(model_id="llama3.2", provider="ollama")
    assert pricing.input_cost_per_token == 0.0
    assert pricing.output_cost_per_token == 0.0


def test_model_pricing_rejects_negative_costs() -> None:
    with pytest.raises(ValueError, match="input_cost_per_token"):
        ModelPricing(model_id="x", provider="y", input_cost_per_token=-1.0)
    with pytest.raises(ValueError, match="output_cost_per_token"):
        ModelPricing(model_id="x", provider="y", output_cost_per_token=-0.1)
