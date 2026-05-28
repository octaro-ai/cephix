"""Ports for the LLM actor subsystem.

One interface lives here:

- :class:`LLMActorPort` -- the LLM-aware extension of
  :class:`~src.actor.ports.ActorPort`. Adds streaming as a
  mandatory capability and exposes ``model_id`` / ``provider`` /
  ``count_tokens`` so the kernel can identify the actor and reason
  about token budgets without inspecting return values.

Catalog-side ports (:class:`ModelCatalogPort`,
:class:`ModelDataSource`) live with the catalog itself in
:mod:`src.utility.model_catalog.ports`. The LLM actor never imports
them at runtime; drivers that accept a catalog reference type-hint
against the port from the utility package, mirroring the OS-driver
pattern: the driver knows how to drive the device, the catalog
(a separate utility) knows the device's characteristics.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from src.actor.ports import ActorPort
from src.actor.llm.types import ActorChunk


class LLMActorPort(ActorPort):
    """LLM-aware extension of :class:`~src.actor.ports.ActorPort`.

    Three things on top of the plain ``ActorPort`` contract:

    1. **Streaming as a mandatory capability**: every LLM actor
       implements :meth:`stream`. A non-streaming SDK still yields
       a single final chunk (via the
       :class:`~src.actor.llm.actor_base.LLMActorBase` adapter).
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
    ``ActorPort``, so a :class:`~src.actor.llm.mock_actor.MockLLMActor`
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
