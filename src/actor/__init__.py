"""Actors: the entities the kernel consults to turn context into a reply.

An *actor* is what the kernel calls during its ``act`` phase to turn a
curated context image into a structured reply. Actors are *not* on
the bus -- the kernel holds them as direct in-process collaborators.
The same kernel can be married to different actors (a real LLM, a
scripted test double, a human-in-the-loop, a deterministic program,
a child process) without the kernel caring which.

The first concrete actor is :class:`EchoActor`; later iterations add
``LLMActor``, ``MockActor``, ``HumanActor``, ``PlaywrightActor``.
"""

from src.actor.echo import EchoActor
from src.actor.ports import ActorPort
from src.actor.types import ActorResponse

__all__ = ["ActorPort", "ActorResponse", "EchoActor"]
