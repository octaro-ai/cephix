"""ModelMetadataService: serves :class:`ModelSpec` and :class:`ModelPricing`.

A single :class:`~src.components.BusComponent` that wraps an
:class:`~src.llm.ports.ModelDataSource` and exposes two read-side
ports (:class:`~src.llm.ports.ModelCatalogPort` and
:class:`~src.llm.ports.PricingPort`) on top of the same in-memory
snapshot. CQRS-flavoured: one source of truth, two read models.

Audit semantics: any externally-sourced refresh of the snapshot
publishes a :class:`~src.bus.messages.RobotAuditNote` on the bus.
The bundled snapshot path is silent (offline, deterministic, no
side effect). Iteration 1b will plug in an llmprice-kit-backed
source whose ``load`` *does* network IO -- and the audit note then
documents exactly that side effect.
"""

from __future__ import annotations

import logging
from typing import Any

from src.bus.ports import BusPort
from src.components import BusComponent, ComponentCategory
from src.llm.ports import ModelCatalogPort, ModelDataSource, PricingPort
from src.llm.sources import BundledLiteLLMSource
from src.llm.types import ModelPricing, ModelSpec

logger = logging.getLogger(__name__)


class ModelMetadataService(BusComponent):
    """Bus component that serves model spec and pricing lookups.

    Two read-side ports on the same data:

    - :meth:`lookup_spec` returns a :class:`ModelSpec` (capabilities
      and limits). Implements :class:`ModelCatalogPort`.
    - :meth:`lookup_pricing` returns a :class:`ModelPricing` (cost
      per token). Implements :class:`PricingPort`.

    Boot order: :attr:`ComponentCategory.GOVERNANCE` (priority 7)
    so the service is online *before* actors and kernels start --
    they may consult it during their own ``start()`` to validate
    model configuration.

    Source: defaults to :class:`BundledLiteLLMSource` (offline). A
    custom source can be injected via the constructor; the service
    only relies on the :class:`ModelDataSource` protocol.

    Refresh: out of scope for Iteration 1a -- the bundled source
    loads exactly once during ``start()`` and never refreshes. The
    audit-loud refresh path arrives with the llmprice-kit-backed
    source in Iteration 1b. The :meth:`refresh` method is already
    here so the contract is stable; it currently re-reads the
    bundled file and publishes an audit note when the snapshot id
    actually changed.
    """

    component_name = "model-metadata"
    component_category = ComponentCategory.GOVERNANCE
    component_description = (
        "Serves model specs and pricing as two read-side ports on a "
        "shared, audit-tracked snapshot. Backs the LLM actor's "
        "context-window-aware planning and any cost-tracking layer."
    )

    def __init__(
        self,
        *,
        source: ModelDataSource | None = None,
    ) -> None:
        self._source: ModelDataSource = source or BundledLiteLLMSource()
        self._raw: dict[tuple[str, str], dict[str, Any]] = {}
        self._snapshot_id: str = ""
        self._bus: BusPort | None = None

    # ---- BusComponent lifecycle -------------------------------------------

    async def start(self, bus: BusPort) -> None:
        self._bus = bus
        await self._load_initial()

    async def stop(self) -> None:
        self._bus = None
        self._raw = {}
        self._snapshot_id = ""

    # ---- Catalog + Pricing read sides -------------------------------------

    def lookup_spec(self, model_id: str, provider: str) -> ModelSpec | None:
        """Return the :class:`ModelSpec` for ``(model_id, provider)``."""
        row = self._raw.get((provider, model_id))
        if row is None:
            return None
        return _row_to_spec(row)

    def lookup_pricing(
        self, model_id: str, provider: str
    ) -> ModelPricing | None:
        """Return the :class:`ModelPricing` for ``(model_id, provider)``."""
        row = self._raw.get((provider, model_id))
        if row is None:
            return None
        return _row_to_pricing(row)

    # Both protocols expose a ``lookup`` method; expose them as
    # explicit aliases so the service satisfies both
    # :class:`ModelCatalogPort` and :class:`PricingPort` directly via
    # bound methods. Consumers pick the slice they want via
    # :meth:`as_catalog_port` / :meth:`as_pricing_port` for clarity
    # at the call site (avoids ambiguity when both ports are on the
    # same object).

    def as_catalog_port(self) -> ModelCatalogPort:
        """Return a view that satisfies :class:`ModelCatalogPort`."""
        return _CatalogView(self)

    def as_pricing_port(self) -> PricingPort:
        """Return a view that satisfies :class:`PricingPort`."""
        return _PricingView(self)

    # ---- Refresh (audit-tracked) ------------------------------------------

    async def refresh(self) -> bool:
        """Re-load the snapshot. Publish an audit note if it changed.

        Returns ``True`` if the snapshot id changed (and thus an
        audit note was emitted), ``False`` if the snapshot was
        already current.

        Currently a no-op for the bundled source (the file does not
        change at runtime). Iteration 1b plugs in llmprice-kit and
        this method becomes the network-touching refresh path.
        """
        before = self._snapshot_id
        try:
            new_raw = await self._source.load()
            new_snapshot_id = self._source.snapshot_id
        except Exception as exc:  # noqa: BLE001 -- audit a refresh failure
            await self._audit_refresh_failed(reason=str(exc))
            raise
        if new_snapshot_id == before:
            return False
        self._raw = new_raw
        self._snapshot_id = new_snapshot_id
        await self._audit_refresh(
            before=before,
            after=new_snapshot_id,
            rows=len(new_raw),
        )
        return True

    # ---- internals --------------------------------------------------------

    async def _load_initial(self) -> None:
        """Load the snapshot during ``start()`` -- silent on success.

        The first load on boot is *not* audited: it is expected and
        deterministic for the bundled source. Subsequent
        :meth:`refresh` calls are the audit-loud path.
        """
        self._raw = await self._source.load()
        self._snapshot_id = self._source.snapshot_id
        logger.info(
            "ModelMetadataService loaded snapshot %r (%d models)",
            self._snapshot_id,
            len(self._raw),
        )

    async def _audit_refresh(
        self, *, before: str, after: str, rows: int
    ) -> None:
        if self._bus is None:
            return
        await self.publish_audit(
            self._bus,
            action="pricing.refresh",
            details={
                "source": type(self._source).__name__,
                "before_snapshot_id": before,
                "after_snapshot_id": after,
                "rows": rows,
            },
        )

    async def _audit_refresh_failed(self, *, reason: str) -> None:
        if self._bus is None:
            return
        await self.publish_audit(
            self._bus,
            action="pricing.refresh.failed",
            details={
                "source": type(self._source).__name__,
                "snapshot_id": self._snapshot_id,
                "reason": reason,
            },
        )


# ---------------------------------------------------------------------------
# Read-side adapter views
# ---------------------------------------------------------------------------


class _CatalogView:
    """Adapter exposing the catalog read-side as a sole ``lookup`` method."""

    def __init__(self, service: ModelMetadataService) -> None:
        self._service = service

    def lookup(self, model_id: str, provider: str) -> ModelSpec | None:
        return self._service.lookup_spec(model_id, provider)


class _PricingView:
    """Adapter exposing the pricing read-side as a sole ``lookup`` method."""

    def __init__(self, service: ModelMetadataService) -> None:
        self._service = service

    def lookup(self, model_id: str, provider: str) -> ModelPricing | None:
        return self._service.lookup_pricing(model_id, provider)


# ---------------------------------------------------------------------------
# Row -> dataclass translation (LiteLLM-shaped JSON)
# ---------------------------------------------------------------------------


# Keys we surface as first-class fields on ModelSpec / ModelPricing.
# Everything else from the LiteLLM row goes into ``extras`` so a new
# upstream column is automatically available without code changes.
_SPEC_FIRST_CLASS_KEYS = {
    "model_id",
    "provider",
    "max_input_tokens",
    "max_output_tokens",
    "supports_function_calling",
    "supports_vision",
    "supports_response_schema",
    "supports_system_messages",
}

_PRICING_FIRST_CLASS_KEYS = {
    "model_id",
    "provider",
    "input_cost_per_token",
    "output_cost_per_token",
}


def _row_to_spec(row: dict[str, Any]) -> ModelSpec:
    extras = {
        key: value
        for key, value in row.items()
        if key not in _SPEC_FIRST_CLASS_KEYS
        and key not in _PRICING_FIRST_CLASS_KEYS
    }
    return ModelSpec(
        model_id=row["model_id"],
        provider=row["provider"],
        context_window_tokens=int(row.get("max_input_tokens", 0)),
        max_output_tokens=int(row.get("max_output_tokens", 0)),
        supports_function_calling=bool(row.get("supports_function_calling", False)),
        supports_vision=bool(row.get("supports_vision", False)),
        supports_response_schema=bool(row.get("supports_response_schema", False)),
        supports_system_messages=bool(row.get("supports_system_messages", True)),
        extras=extras,
    )


def _row_to_pricing(row: dict[str, Any]) -> ModelPricing:
    extras = {
        key: value
        for key, value in row.items()
        if key not in _SPEC_FIRST_CLASS_KEYS
        and key not in _PRICING_FIRST_CLASS_KEYS
    }
    return ModelPricing(
        model_id=row["model_id"],
        provider=row["provider"],
        input_cost_per_token=float(row.get("input_cost_per_token", 0.0)),
        output_cost_per_token=float(row.get("output_cost_per_token", 0.0)),
        extras=extras,
    )
