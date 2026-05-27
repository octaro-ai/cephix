"""Tests for the bus message types."""

from __future__ import annotations

import pytest

from src.bus import (
    KernelPhase,
    RobotInput,
    RobotOutput,
    ComponentRequest,
    ComponentResponse,
)


_COMMON = dict(
    topic="input.demo",
    principal="user-1",
    source="test",
    run_id="run-1",
)


def test_robot_input_defaults() -> None:
    msg = RobotInput(**_COMMON, text="hi")

    assert msg.topic == "input.demo"
    assert msg.principal == "user-1"
    assert msg.text == "hi"
    assert msg.payload == {}
    assert msg.event_id.startswith("evt-")
    assert msg.timestamp
    assert msg.correlation_id is None


def test_robot_output_payload() -> None:
    msg = RobotOutput(**_COMMON, text="hi", payload={"k": 1})

    assert msg.payload == {"k": 1}


def test_robot_request_requires_correlation_id() -> None:
    with pytest.raises(ValueError, match="correlation_id"):
        ComponentRequest(**_COMMON, action="tool.mail.list")


def test_robot_request_requires_action() -> None:
    with pytest.raises(ValueError, match="action"):
        ComponentRequest(**_COMMON, correlation_id="corr-1", action="")


def test_robot_response_requires_correlation_id() -> None:
    with pytest.raises(ValueError, match="correlation_id"):
        ComponentResponse(**_COMMON)


def test_failed_response_requires_error() -> None:
    with pytest.raises(ValueError, match="error"):
        ComponentResponse(**_COMMON, correlation_id="corr-1", ok=False)


def test_successful_response() -> None:
    msg = ComponentResponse(
        **_COMMON,
        correlation_id="corr-1",
        ok=True,
        payload={"result": 42},
    )

    assert msg.ok
    assert msg.payload == {"result": 42}
    assert msg.error is None


def test_messages_are_frozen() -> None:
    msg = RobotInput(**_COMMON, text="hi")

    with pytest.raises(Exception):
        msg.topic = "other"  # type: ignore[misc]


def test_kernel_phase_requires_phase() -> None:
    with pytest.raises(ValueError, match="phase"):
        KernelPhase(**_COMMON, kernel="base")


def test_kernel_phase_requires_kernel() -> None:
    with pytest.raises(ValueError, match="kernel"):
        KernelPhase(**_COMMON, phase="observing")


def test_kernel_phase_carries_iteration_and_error() -> None:
    msg = KernelPhase(
        **_COMMON,
        phase="error",
        kernel="base",
        iteration=2,
        error="actor failed",
    )
    assert msg.phase == "error"
    assert msg.kernel == "base"
    assert msg.iteration == 2
    assert msg.error == "actor failed"
    assert msg.details == {}


def test_kernel_phase_carries_wide_event_details() -> None:
    """The details dict is the kernel's wide-event analytics slot."""
    msg = KernelPhase(
        **_COMMON,
        phase="acting",
        kernel="base",
        details={
            "actor_name": "echo",
            "actor_duration_ms": 12.4,
            "actor_ok": True,
        },
    )
    assert msg.details["actor_name"] == "echo"
    assert msg.details["actor_duration_ms"] == 12.4
    assert msg.details["actor_ok"] is True
