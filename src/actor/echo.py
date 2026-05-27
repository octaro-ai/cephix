"""Echo actor: reflects whatever the kernel sent it.

The smallest actor that satisfies the kernel-actor contract. It owns
no resources, never touches the bus, and answers any
:meth:`run` call with an :class:`ActorResponse` whose ``message`` is
the input text prefixed by ``prefix``.

Its job is to make :class:`BaseKernel` end-to-end testable without
any real decision-making -- and to act as a debug lens: pair an
``EchoActor`` with a ``ChatKernel`` and the resulting
``actor_context`` that the LLM *would* have seen lands verbatim in
the telemetry log.
"""

from __future__ import annotations

import logging
from typing import Any

from src.actor.ports import ActorPort
from src.actor.types import ActorResponse
from src.components import ComponentCategory

logger = logging.getLogger(__name__)


class EchoActor(ActorPort):
    """Replies to every :meth:`run` call with an echoed text.

    Configuration:

    - ``prefix`` -- prefix glued in front of the input text in the
      response. Default ``"echo: "``.

    The actor reads the input text from the curated actor context;
    two shapes are accepted:

    1. ``actor_context["message"]`` -- a flat shape some actors /
       tests use.
    2. ``actor_context["input"]["message"]`` -- the shape
       :class:`BaseKernel` produces in its default ``plan``.

    Anything else returns an empty echo -- the actor never crashes
    on a malformed context.
    """

    component_name = "echo"
    component_category = ComponentCategory.ACTOR
    component_description = (
        "Trivial actor that mirrors the input text back as an "
        "ActorResponse with an echoed message payload. Useful as a "
        "debug lens and for end-to-end tests without an LLM."
    )

    def __init__(self, *, prefix: str = "echo: ") -> None:
        self._prefix = prefix

    async def run(self, actor_context: dict[str, Any]) -> ActorResponse:
        text = self._extract_message(actor_context)
        return ActorResponse(message=f"{self._prefix}{text}", status="ok")

    @staticmethod
    def _extract_message(actor_context: dict[str, Any]) -> str:
        text = actor_context.get("message")
        if isinstance(text, str):
            return text
        nested = actor_context.get("input")
        if isinstance(nested, dict):
            inner = nested.get("message")
            if isinstance(inner, str):
                return inner
        return ""
