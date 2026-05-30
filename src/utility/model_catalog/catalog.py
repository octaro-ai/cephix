"""ModelCatalog: a UTILITY component serving model spec and pricing.

The catalog is a plain :class:`~src.components.RobotComponent`
(category :attr:`~src.components.ComponentCategory.UTILITY`), not a
bus participant. It boots after audit and before any consumer can
``start()``, so a future ``LLMKernel`` can hold a reference at
construction time and look up specs / pricing during its own boot.

Why off-bus: today there is *one* consumer (the kernel, in Phase 2).
Adding a bus component for a single consumer would invert the
dependency direction (consumer depends on a topic instead of an
injected port) for no win. When a second consumer arrives -- a cost
aggregator listening for everyone's spend, a credentials broker
shared across actors -- the right move is *that* component on the
bus, while the catalog stays a sync-callable utility.

Lifecycle: the catalog asks its
:class:`~src.utility.model_catalog.ports.ModelDataSource` for data
on every lookup, but the source typically caches an in-memory
snapshot (see
:class:`~src.utility.model_catalog.sources.LLMPriceKitSource`). The
catalog itself doesn't manage a cache layer -- pushing that into
the source keeps the catalog's responsibility narrow:
"port-fronted view of a data source".

Audit: when :meth:`refresh` triggers an externally-sourced data
fetch (Iteration 1b, when the LLMPriceKit auto-update path is
exercised), the catalog has no bus to publish on. The owner-pattern
applies: the robot publishes the audit note on the catalog's
behalf via the ``component.<name>.lifecycle`` mount path. For now
``refresh`` is a no-op stub on the catalog level; callers go to
the source directly.
"""

from __future__ import annotations

import logging

from src.components import ComponentCategory, RobotComponent
from src.utility.model_catalog.ports import ModelCatalogPort, ModelDataSource
from src.utility.model_catalog.sources import LLMPriceKitSource
from src.utility.model_catalog.types import ModelPricing, ModelSpec

logger = logging.getLogger(__name__)


class ModelCatalog(RobotComponent, ModelCatalogPort):
    """Utility component serving spec and pricing lookups.

    Constructor:

    - ``source`` -- a :class:`ModelDataSource`. Default:
      :class:`~src.utility.model_catalog.sources.LLMPriceKitSource`
      with offline-only mode (``auto_update=False``). Tests inject
      in-memory fakes.

    Lifecycle hooks are no-ops: the source is constructed eagerly
    (the lib loads its bundled snapshot in __init__), so ``start``
    has nothing to do. ``stop`` releases the source reference.
    """

    component_name = "model-catalog"
    component_category = ComponentCategory.UTILITY
    component_description = (
        "Read-side view of model specs (capabilities, limits) and "
        "pricing (cost per token), served from a pluggable "
        "ModelDataSource. UTILITY-tier: off-bus, sync, consumed by "
        "an LLMKernel during its own boot."
    )

    def __init__(self, *, source: ModelDataSource | None = None) -> None:
        self._source: ModelDataSource = source or LLMPriceKitSource()

    async def start(self) -> None:
        """No-op: the data source is initialised eagerly."""
        return None

    async def _stop(self) -> None:
        """No-op: nothing to release."""
        return None

    # ---- ModelCatalogPort -------------------------------------------------

    def lookup_spec(self, model_id: str, provider: str) -> ModelSpec | None:
        return self._source.load_spec(model_id, provider)

    def lookup_pricing(
        self, model_id: str, provider: str
    ) -> ModelPricing | None:
        return self._source.load_pricing(model_id, provider)

    # ---- Refresh ----------------------------------------------------------

    @property
    def snapshot_id(self) -> str:
        """Forward the source's snapshot identifier."""
        return self._source.snapshot_id
