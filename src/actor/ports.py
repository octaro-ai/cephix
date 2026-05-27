"""Actor port.

An *actor* is the entity a kernel consults during its ``act`` phase
to turn a curated *actor context* into a response. Critically, an
actor is **not** on the bus. The kernel mediates: it owns the bus
subscription that turns external traffic into the actor context,
and once the context is curated it makes a direct in-process call
into the actor. The actor knows nothing about topics,
``ComponentRequest`` correlation or the rest of the bus vocabulary --
it only knows the dict it was handed and the
:class:`ActorResponse` it has to return.

Why off-bus:

- *Conceptually*, kernel-to-actor is a service call within one logical
  unit. Putting it on the shared bus would pollute the wire with
  internal traffic that nobody else needs.
- *Practically*, this lets an actor be anything with a Python entry
  point: a stateless function, an HTTP client (``LLMActor``), a
  child process (``PlaywrightActor`` driving a browser), a remote
  GRPC service, a human via an operator UI. Each implements the
  port the same way, the kernel doesn't notice.
- *Operationally*, the actor still gets a real lifecycle: it is a
  :class:`RobotComponent`, the robot starts it before the kernel
  (boot priority 8 < kernel 10) and stops it after the kernel during
  the mirrored shutdown. Subprocess actors get :meth:`drain` for
  bounded cleanup for free.

Audit attribution: actors do not publish audit notes themselves --
they have no bus to publish on. Instead they put bookkeeping
(provider, token count, latency, ...) into
:attr:`ActorResponse.metadata` and the kernel emits a
:class:`RobotAuditNote` on the actor's behalf during ``finalize``,
attributed to ``actor.<component_name>``.
"""

from __future__ import annotations

from typing import Any

from src.actor.types import ActorResponse
from src.components import RobotComponent


class ActorPort(RobotComponent):
    """Base class for actors. Plain :class:`RobotComponent`, no bus.

    Subclasses implement :meth:`run`. They inherit ``start`` /
    ``stop`` / ``drain`` from :class:`RobotComponent`; trivial
    actors like :class:`EchoActor` use no-op implementations,
    while resource-holding actors (subprocess, HTTP client, ...) own
    real setup and teardown there.
    """

    async def start(self) -> None:
        """Default: nothing to bring online. Override for resources."""
        return None

    async def stop(self) -> None:
        """Default: nothing to release. Override for resources."""
        return None

    async def run(self, actor_context: dict[str, Any]) -> ActorResponse:
        """Turn the actor context into an :class:`ActorResponse`.

        ``actor_context`` is the dict the kernel curated during its
        ``plan`` phase: input text, principal, run id, history,
        memory, tool schemas -- whatever the specific kernel decided
        to expose. The actor reads it as a flat data structure; the
        contract between kernel and actor is whatever the two sides
        agree on for the keys.

        Implementations must not raise except on programmer errors:
        recoverable failures (network, timeout, refusal) are
        signalled by returning :class:`ActorResponse` with
        ``ok=False`` and a populated ``error`` field. The kernel
        turns that into an ``error`` phase event.
        """
        raise NotImplementedError(f"{type(self).__name__}.run() not implemented")
