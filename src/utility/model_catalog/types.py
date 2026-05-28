"""Value types for the model catalog.

Two dataclasses with the same composite key ``(model_id, provider)``:

- :class:`ModelSpec` carries static **capabilities** and **limits**
  (context window, supported features). Read by kernels that need to
  plan context-window-aware, and by limits monitors.
- :class:`ModelPricing` carries **cost per token**. Read by cost
  aggregators and by drivers that want to attach a USD cost to every
  reply.

They are split because they have different change cadences (pricing
moves often, capabilities rarely) and different consumers. Both come
from the same upstream snapshot via a
:class:`~src.utility.model_catalog.ports.ModelDataSource` adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelSpec:
    """Static capabilities and limits of a chat model.

    Identity: ``(model_id, provider)`` is the composite key. The same
    ``model_id`` accessed via different providers (e.g. ``gpt-5`` over
    OpenAI direct vs. via OpenRouter) is treated as *different*
    ModelSpecs because pricing tables, latency characteristics and
    capability flags can differ.

    Pricing intentionally lives on a separate :class:`ModelPricing`
    dataclass: pricing changes more often than capabilities, and the
    consumers are different (kernels and limits monitors read specs;
    cost calculators read pricing).

    Fields:

    - ``model_id`` -- canonical model identifier (e.g. ``"gpt-5"``,
      ``"claude-sonnet-4-6"``).
    - ``provider`` -- provider identifier (``"openai"``,
      ``"anthropic"``, ``"openrouter"``, ``"ollama"``, ...).
    - ``context_window_tokens`` -- maximum input tokens per request.
    - ``max_output_tokens`` -- upper bound on completion tokens.
    - ``supports_function_calling`` -- model accepts tool / function
      definitions.
    - ``supports_vision`` -- model accepts image inputs.
    - ``supports_response_schema`` -- model honours JSON-Schema
      structured-output requests.
    - ``supports_system_messages`` -- model accepts a system role.
    - ``extras`` -- pass-through for source-specific fields not yet
      first-class (e.g. ``supports_prompt_caching``,
      ``supports_reasoning``, ``supports_audio_input``,
      ``deprecation_date``). Keeps the dataclass shape stable when
      upstream adds new columns.
    """

    model_id: str
    provider: str
    context_window_tokens: int
    max_output_tokens: int
    supports_function_calling: bool = False
    supports_vision: bool = False
    supports_response_schema: bool = False
    supports_system_messages: bool = True
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model_id:
            raise ValueError("ModelSpec requires a non-empty model_id")
        if not self.provider:
            raise ValueError("ModelSpec requires a non-empty provider")
        if self.context_window_tokens < 0:
            raise ValueError(
                f"ModelSpec.context_window_tokens must be >= 0, "
                f"got {self.context_window_tokens}"
            )
        if self.max_output_tokens < 0:
            raise ValueError(
                f"ModelSpec.max_output_tokens must be >= 0, "
                f"got {self.max_output_tokens}"
            )


@dataclass(frozen=True)
class ModelPricing:
    """Cost-per-token of a chat model.

    Same composite key ``(model_id, provider)`` as :class:`ModelSpec`.
    Costs are in USD *per token*, even though most upstream sources
    report per-million-tokens; the
    :class:`~src.utility.model_catalog.ports.ModelDataSource` adapter
    is responsible for the unit conversion so consumers always see
    the same scale.

    Both costs may be ``0.0`` for free local models (Ollama, LM
    Studio) and self-hosted vLLM deployments.

    ``extras`` mirrors the field on :class:`ModelSpec`: pass-through
    for upstream-provided pricing nuances we don't first-class
    (``cache_read_cost_per_token``, ``cache_write_cost_per_token``,
    ``input_cost_per_token_above_128k``).
    """

    model_id: str
    provider: str
    input_cost_per_token: float = 0.0
    output_cost_per_token: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model_id:
            raise ValueError("ModelPricing requires a non-empty model_id")
        if not self.provider:
            raise ValueError("ModelPricing requires a non-empty provider")
        if self.input_cost_per_token < 0:
            raise ValueError(
                f"ModelPricing.input_cost_per_token must be >= 0, "
                f"got {self.input_cost_per_token}"
            )
        if self.output_cost_per_token < 0:
            raise ValueError(
                f"ModelPricing.output_cost_per_token must be >= 0, "
                f"got {self.output_cost_per_token}"
            )
