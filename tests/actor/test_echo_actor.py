"""Tests for :class:`EchoActor`.

The echo actor is a plain :class:`RobotComponent`: no bus, no topics,
no subscriptions. It exposes :meth:`run` -- the single method the
kernel calls during its act phase. These tests exercise that method
directly and verify the lifecycle hooks are no-ops.
"""

from __future__ import annotations

import pytest

from src.actor.echo import EchoActor
from src.actor.types import ActorResponse
from src.components import ComponentCategory, RobotComponent


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


async def test_echo_actor_responds_with_prefixed_text_from_flat_context() -> None:
    actor = EchoActor()
    response = await actor.run({"text": "hello"})

    assert isinstance(response, ActorResponse)
    assert response.ok is True
    assert response.text == "echo: hello"
    assert response.error is None


async def test_echo_actor_reads_text_from_nested_input_context() -> None:
    """Mirrors the BaseKernel actor_context shape (``ctx['input']['text']``)."""
    actor = EchoActor()
    response = await actor.run({"input": {"text": "from-kernel", "principal": "user"}})

    assert response.text == "echo: from-kernel"
    assert response.ok is True


async def test_echo_actor_returns_empty_echo_for_malformed_context() -> None:
    actor = EchoActor()
    response = await actor.run({"random": "garbage"})

    assert response.text == "echo: "
    assert response.ok is True


async def test_echo_actor_honours_custom_prefix() -> None:
    actor = EchoActor(prefix="yo: ")
    response = await actor.run({"text": "ping"})

    assert response.text == "yo: ping"


# ---------------------------------------------------------------------------
# Identity / lifecycle
# ---------------------------------------------------------------------------


def test_echo_actor_is_a_plain_robot_component() -> None:
    """The actor must NOT inherit BusComponent: it never touches the bus."""
    from src.components import BusComponent

    actor = EchoActor()
    assert isinstance(actor, RobotComponent)
    assert not isinstance(actor, BusComponent)


def test_echo_actor_metadata() -> None:
    from src.onboarding import WIZARD_ALLOWLIST

    assert EchoActor.component_name == "echo"
    assert EchoActor.component_category is ComponentCategory.ACTOR
    assert WIZARD_ALLOWLIST[EchoActor] == ("prefix",)


async def test_echo_actor_start_and_stop_are_noops() -> None:
    actor = EchoActor()
    await actor.start()
    await actor.stop()
    response = await actor.run({"text": "still works"})
    assert response.text == "echo: still works"


# ---------------------------------------------------------------------------
# Defensive: malformed ActorResponse construction
# ---------------------------------------------------------------------------


def test_actor_response_failure_requires_error_label() -> None:
    with pytest.raises(ValueError, match="error"):
        ActorResponse(ok=False)
