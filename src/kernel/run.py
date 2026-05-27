"""Kernel run state machine and per-run context.

A *kernel run* is the deterministic pipeline a kernel walks through to
turn a single :class:`RobotInput` into one or more bus messages. The
shape of the pipeline is fixed; what each phase *does* is what kernels
specialize.

The five-phase split mirrors the symmetry the architecture document
introduced: bus IO at the edges, local computation in between, with a
single in-process actor call in the middle:

::

    Observe   ->   Plan        ->   Act           ->   Finalize    ->   Respond
    Bus IN         compute          Actor call          compute          Bus OUT
    (RobotInput)   (history,        (in-process,        (parse,          (RobotOutput
                    context)         no bus traffic)     classify)         or ComponentRequest
                                                                          to tools)

Note that ``Act`` is *not* a bus round-trip in this architecture: the
kernel holds the actor as a direct in-process collaborator and calls
:meth:`ActorPort.run` on it. Only :class:`ComponentRequest` /
:class:`ComponentResponse` traffic that crosses component boundaries
(future tool execution layer) actually rides the bus.

The contract is intentionally narrow:

- The base kernel owns the *loop*, the phase order, the per-phase
  audit/telemetry events and the error handling. Concrete kernels
  override individual phases without ever having to re-implement the
  loop itself.
- :class:`RunContext` is the only object that travels through the
  phases. It accumulates the bits each phase produced
  (``input``, ``actor_context``, ``actor_response``, ``output_*``)
  and a small amount of run metadata (``run_id``, ``iteration``,
  ``phase``, ``started_at``, ``ended_at``).

Iteration semantics: ``iteration`` stays at ``0`` for the first
``Observe -> ... -> Respond`` cycle. Once the tool execution layer
exists, a respond that publishes a :class:`ComponentRequest` can leave
the run "open"; the kernel then increments ``iteration`` and walks
the same phases again with the tool result on the bus. The first
iteration alone is enough for ReAct without tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from src.actor.types import ActorResponse
from src.bus.messages import RobotInput


class RunPhase(str, Enum):
    """States of a single kernel run.

    The order matches the canonical pipeline; transitions only ever
    move forward, except into :attr:`ERROR` from any non-terminal
    phase. ``IDLE`` and ``DONE`` bracket the run; the four phases in
    between are the work.
    """

    IDLE = "idle"
    OBSERVING = "observing"
    PLANNING = "planning"
    ACTING = "acting"
    FINALIZING = "finalizing"
    RESPONDING = "responding"
    DONE = "done"
    ERROR = "error"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class RunContext:
    """Mutable scratchpad threaded through the phases of one kernel run.

    Phases write into the fields they own; later phases read what
    earlier phases produced. The base kernel never inspects the
    payload-shaped fields (``actor_context``, ``output_text``,
    ``output_payload``) -- those are kernel-specific and only the
    overriding kernel knows their schema.

    Wide-event slot
    ---------------

    ``phase_details`` is the per-phase analytics scratchpad. Each
    phase method may write structured key/value entries into it
    (durations, counters, IDs, model names, token costs, ...). The
    kernel loop emits a :class:`KernelPhase` event after each phase
    completes, hands ``phase_details`` along as the event's
    ``details`` field, and clears it for the next phase. This is
    where the wide-event log gets its analytics-grade fields from.

    ``metadata`` is the open-ended scratchpad for phase-private data
    that should *not* end up in telemetry (e.g. a kernel that wants
    to remember which session a run belongs to before history is
    loaded).
    """

    run_id: str
    iteration: int = 0
    phase: RunPhase = RunPhase.IDLE

    input: RobotInput | None = None
    actor_context: dict[str, Any] = field(default_factory=dict)
    actor_response: ActorResponse | None = None

    output_text: str | None = None
    output_payload: dict[str, Any] = field(default_factory=dict)

    started_at: datetime = field(default_factory=_utcnow)
    ended_at: datetime | None = None
    phase_started_at: datetime | None = None
    total_actor_ms: float = 0.0

    error: str = ""
    phase_details: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
