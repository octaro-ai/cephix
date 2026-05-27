"""Value types exchanged between a kernel and its actor.

The kernel calls :meth:`ActorPort.run` with the curated *actor
context* and gets back an :class:`ActorResponse`. Both live purely
in process memory: actor traffic is *not* bus traffic. The bus
exists for robot-wide events (input, output, lifecycle, audit,
telemetry, tool calls); the kernel-actor handoff is a service call
inside the kernel's address space.

The naming mirrors the bus contract: just as a :class:`ComponentRequest`
on the bus is answered by a :class:`ComponentResponse`, an in-process
actor invocation is parameterised by an actor context and answered
by an :class:`ActorResponse`. Same vocabulary, different transport.

Why a dedicated type instead of a plain ``dict``:

- It pins the contract (``message``, ``payload``, ``status``,
  ``error``, ``metadata``) so swapping ``EchoActor`` for ``LLMActor``
  or ``PlaywrightActor`` does not break the kernel's
  :meth:`finalize` phase.
- It separates two carriers cleanly: ``payload`` is the actor's
  primary product (tool intents, structured output), ``metadata`` is
  bookkeeping the kernel turns into audit notes / telemetry on the
  actor's behalf (e.g. ``{"provider": "anthropic", "tokens": 1234}``).

:class:`ActorResponse` mixes in :class:`Failable` so the success /
failure outcome uses the same vocabulary as :class:`ComponentResponse`
on the bus -- the kernel can translate an actor-level
:class:`ErrorInfo` into a bus-level :class:`KernelPhase` (or, for
specialising kernels, a :class:`ComponentResponse`) without
re-shaping the error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.bus.messages import Failable


@dataclass(frozen=True, kw_only=True)
class ActorResponse(Failable):
    """Result of one :meth:`ActorPort.run` invocation.

    Fields:

    - ``message`` -- primary textual reply, or ``None`` when the actor
      produced only structured output. The kernel's default
      :meth:`BaseKernel.finalize` uses this as the source for
      ``ctx.output_message``.
    - ``payload`` -- structured response data. The default kernel
      stashes this on ``ctx.output_payload``; specializing kernels
      mine it for tool-call intents.
    - ``metadata`` -- side information the kernel can publish as a
      :class:`RobotAuditNote` on the actor's behalf (provider name,
      token counts, latency, cost, ...). Stays empty for trivial
      actors like ``EchoActor``.

    Plus the inherited :class:`Failable` fields: ``status`` (default
    ``"ok"``) and ``error``. A failed actor returns
    ``ActorResponse(status="error", error=ErrorInfo(code="...",
    message="..."))``; the invariant is enforced by the mixin.
    """

    message: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__post_init__()
