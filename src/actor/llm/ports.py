"""Ports for the LLM subsystem.

Three interfaces, each with one current consumer:

- :class:`ModelDataSource` -- where the model metadata snapshot
  comes from. The :class:`~src.llm.catalog.ModelCatalog` consumes
  this. Concrete implementations:
  :class:`~src.llm.sources.LLMPriceKitSource` (default, wraps the
  ``llmprice`` lib) and -- for tests -- in-memory fakes.

- :class:`ModelCatalogPort` -- read-side of model **specifications**
  (capabilities and limits) plus the optional **pricing** for the
  same key. Consumed today by no production code; the future
  :class:`LLMKernel` (Phase 2) takes one of these as a constructor
  argument so it can plan context-window-aware. Building it now
  prevents the ``LLMKernel`` from later hard-binding to a concrete
  catalog implementation: the port is the architectural seam.

- :class:`LLMActorPort` -- the LLM-aware extension of
  :class:`~src.actor.ports.ActorPort`. Adds streaming as a
  mandatory capability and exposes ``model_id`` / ``provider`` /
  ``count_tokens`` so the kernel can identify the actor and reason
  about token budgets without inspecting return values.

A note on the symmetry: the ``LLMKernel`` will hold *both* an
:class:`LLMActorPort` (the driver) and a :class:`ModelCatalogPort`
(the spec source). That mirrors the OS-driver pattern: the driver
knows how to drive the device, the kernel knows the device's
characteristics from a separate registry. The actor itself never
talks to the catalog -- responsibility separation is enforced at
the type system level.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from src.actor.ports import ActorPort
from src.actor.llm.types import (
    ActorChunk,
    ModelPricing,
    ModelSpec,
)


# ---------------------------------------------------------------------------
# Source: where the metadata snapshot comes from
# ---------------------------------------------------------------------------


@runtime_checkable
class ModelDataSource(Protocol):
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
    def snapshot_id(self) -> str:
        ...

    def load_spec(self, model_id: str, provider: str) -> ModelSpec | None:
        ...

    def load_pricing(
        self, model_id: str, provider: str
    ) -> ModelPricing | None:
        ...


# ---------------------------------------------------------------------------
# Catalog: the public read side
# ---------------------------------------------------------------------------


@runtime_checkable
class ModelCatalogPort(Protocol):
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

    def lookup_spec(self, model_id: str, provider: str) -> ModelSpec | None:
        ...

    def lookup_pricing(
        self, model_id: str, provider: str
    ) -> ModelPricing | None:
        ...


# ---------------------------------------------------------------------------
# LLM actor port
# ---------------------------------------------------------------------------


class LLMActorPort(ActorPort):
    """LLM-aware extension of :class:`~src.actor.ports.ActorPort`.

    Three things on top of the plain ``ActorPort`` contract:

    1. **Streaming as a mandatory capability**: every LLM actor
       implements :meth:`stream`. A non-streaming SDK still yields
       a single final chunk (via the
       :class:`~src.llm.actor_base.LLMActorBase` adapter).
    2. **Identity properties** (``model_id``, ``provider``) so a
       caller can route, audit and look up specs without inspecting
       a returned response.
    3. **Token counting** (``count_tokens``) so a context-window-aware
       kernel can plan its message list before the call. The actor
       knows its tokenizer because it knows its SDK; pulling the
       tokenizer out into a separate service would multiply the
       wiring without buying anything for the canonical case.

    A future ``LLMKernel`` will accept ``LLMActorPort`` instead of
    ``ActorPort`` in its constructor, making the LLM dependency
    explicit at the type-system level. The plain
    :class:`~src.kernel.base.BaseKernel` happily accepts any
    ``ActorPort``, so a :class:`~src.llm.mock_actor.MockLLMActor`
    works end-to-end with the base kernel today.

    Audit attribution stays the kernel's job: actors do not publish
    on the bus; their bookkeeping rides on
    :attr:`~src.actor.types.ActorResponse.metadata`, and the kernel
    surfaces it in :class:`~src.bus.messages.KernelPhase` /
    :class:`~src.bus.messages.RobotAuditNote` events.
    """

    @property
    def model_id(self) -> str:
        raise NotImplementedError(
            f"{type(self).__name__}.model_id not implemented"
        )

    @property
    def provider(self) -> str:
        raise NotImplementedError(
            f"{type(self).__name__}.provider not implemented"
        )

    def count_tokens(self, text: str) -> int:
        raise NotImplementedError(
            f"{type(self).__name__}.count_tokens not implemented"
        )

    def stream(self, actor_context: dict[str, Any]) -> AsyncIterator[ActorChunk]:
        raise NotImplementedError(
            f"{type(self).__name__}.stream not implemented"
        )
