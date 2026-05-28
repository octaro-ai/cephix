"""Ports for the model catalog.

Two interfaces, both :class:`abc.ABC`:

- :class:`ModelDataSource` -- where the raw model metadata snapshot
  comes from. The :class:`~src.utility.model_catalog.catalog.ModelCatalog`
  consumes one of these. Concrete implementations:
  :class:`~src.utility.model_catalog.sources.LLMPriceKitSource`
  (default, wraps the ``llmprice`` lib) and -- for tests -- in-memory
  fakes.

- :class:`ModelCatalogPort` -- read-side of model **specifications**
  (capabilities and limits) plus the optional **pricing** for the
  same key. Consumed today by no production code; the future
  ``LLMKernel`` (Phase 2) takes one of these as a constructor
  argument so it can plan context-window-aware. Building it now
  prevents the kernel from later hard-binding to a concrete
  catalog implementation: the port is the architectural seam.

Both are ABCs (not :class:`typing.Protocol`) on purpose: the
codebase enforces Dependency Inversion via *explicit* inheritance
so static analysis catches a missing method at definition time
rather than at the first runtime call. The concrete catalog and
sources inherit from these directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.utility.model_catalog.types import ModelPricing, ModelSpec


class ModelDataSource(ABC):
    """Provides the raw model metadata snapshot to the catalog.

    Two methods:

    - :meth:`load_spec` -- look up capabilities / limits.
    - :meth:`load_pricing` -- look up cost-per-token.

    The catalog asks the source per-model, lazily; sources cache
    upstream snapshots in-process so the lookups are sync. Both
    methods return ``None`` for unknown models.

    ``snapshot_id`` is an opaque identifier (lib version, file hash,
    upstream SHA) the catalog includes in audit notes so a refresh
    is traceable.
    """

    @property
    @abstractmethod
    def snapshot_id(self) -> str:
        """Stable identifier for the current snapshot."""

    @abstractmethod
    def load_spec(self, model_id: str, provider: str) -> ModelSpec | None:
        """Return the spec for ``(model_id, provider)`` or ``None``."""

    @abstractmethod
    def load_pricing(
        self, model_id: str, provider: str
    ) -> ModelPricing | None:
        """Return the pricing for ``(model_id, provider)`` or ``None``."""


class ModelCatalogPort(ABC):
    """Read-side of model metadata.

    A single port that exposes both spec and pricing lookups. The
    two are conceptually separate (different change cadences,
    different consumers), but in the current design every consumer
    that wants pricing also wants the spec, so combining them into
    one port keeps the wiring minimal.

    If a future consumer wants only pricing (a dedicated cost
    aggregator listening on the bus, say), that consumer can
    subscribe to :class:`~src.bus.messages.RobotAuditNote` events
    that already carry ``cost_usd`` per actor call -- no second port
    needed.

    Returns ``None`` for unknown ``(model_id, provider)`` keys; the
    caller decides whether to warn, fall back, or refuse to start.
    """

    @abstractmethod
    def lookup_spec(self, model_id: str, provider: str) -> ModelSpec | None:
        """Return the spec for ``(model_id, provider)`` or ``None``."""

    @abstractmethod
    def lookup_pricing(
        self, model_id: str, provider: str
    ) -> ModelPricing | None:
        """Return the pricing for ``(model_id, provider)`` or ``None``."""
