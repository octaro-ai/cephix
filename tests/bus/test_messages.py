"""Tests for the bus message types."""

from __future__ import annotations

import pytest

from src.bus import (
    ComponentRequest,
    ComponentResponse,
    ErrorInfo,
    Failable,
    KernelPhase,
    RobotInput,
    RobotOutput,
)


_COMMON = dict(
    topic="input.demo",
    principal="user-1",
    source="test",
    run_id="run-1",
)


# ---------------------------------------------------------------------------
# Plain message types
# ---------------------------------------------------------------------------


def test_robot_input_defaults() -> None:
    msg = RobotInput(**_COMMON, message="hi")

    assert msg.topic == "input.demo"
    assert msg.principal == "user-1"
    assert msg.message == "hi"
    assert msg.payload == {}
    assert msg.event_id.startswith("evt-")
    assert msg.timestamp
    assert msg.correlation_id is None


def test_robot_output_payload_defaults_to_ok() -> None:
    msg = RobotOutput(**_COMMON, message="hi", payload={"k": 1})

    assert msg.payload == {"k": 1}
    assert msg.status == "ok"
    assert msg.error is None


def test_robot_output_can_carry_error_status() -> None:
    msg = RobotOutput(
        **_COMMON,
        message="Sorry, something broke.",
        status="error",
        error=ErrorInfo(code="timeout", message="actor timed out"),
    )

    assert msg.status == "error"
    assert msg.error is not None
    assert msg.error.code == "timeout"


def test_messages_are_frozen() -> None:
    msg = RobotInput(**_COMMON, message="hi")

    with pytest.raises(Exception):
        msg.topic = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ComponentRequest / ComponentResponse
# ---------------------------------------------------------------------------


def test_component_request_requires_correlation_id() -> None:
    with pytest.raises(ValueError, match="correlation_id"):
        ComponentRequest(**_COMMON, action="tool.mail.list")


def test_component_request_requires_action() -> None:
    with pytest.raises(ValueError, match="action"):
        ComponentRequest(**_COMMON, correlation_id="corr-1", action="")


def test_component_response_requires_correlation_id() -> None:
    with pytest.raises(ValueError, match="correlation_id"):
        ComponentResponse(**_COMMON)


def test_component_response_error_status_requires_error_info() -> None:
    with pytest.raises(ValueError, match="status='error' requires"):
        ComponentResponse(**_COMMON, correlation_id="corr-1", status="error")


def test_component_response_ok_must_not_carry_error_info() -> None:
    with pytest.raises(ValueError, match="status='ok' must not"):
        ComponentResponse(
            **_COMMON,
            correlation_id="corr-1",
            error=ErrorInfo(code="boom"),
        )


def test_successful_component_response() -> None:
    msg = ComponentResponse(
        **_COMMON,
        correlation_id="corr-1",
        payload={"result": 42},
    )

    assert msg.status == "ok"
    assert msg.payload == {"result": 42}
    assert msg.error is None


def test_failed_component_response() -> None:
    msg = ComponentResponse(
        **_COMMON,
        correlation_id="corr-1",
        status="error",
        error=ErrorInfo(
            code="timeout",
            message="took too long",
            details={"timeout_s": 30},
        ),
    )

    assert msg.status == "error"
    assert msg.error is not None
    assert msg.error.code == "timeout"
    assert msg.error.details == {"timeout_s": 30}


# ---------------------------------------------------------------------------
# KernelPhase
# ---------------------------------------------------------------------------


def test_kernel_phase_requires_phase() -> None:
    with pytest.raises(ValueError, match="phase"):
        KernelPhase(**_COMMON, kernel="base")


def test_kernel_phase_requires_kernel() -> None:
    with pytest.raises(ValueError, match="kernel"):
        KernelPhase(**_COMMON, phase="observing")


def test_kernel_phase_defaults_to_ok_status() -> None:
    msg = KernelPhase(**_COMMON, phase="observing", kernel="base")
    assert msg.status == "ok"
    assert msg.error is None
    assert msg.message == ""


def test_kernel_phase_carries_iteration_and_message() -> None:
    msg = KernelPhase(
        **_COMMON,
        phase="acting",
        kernel="base",
        iteration=2,
        message="used cached actor response",
    )
    assert msg.phase == "acting"
    assert msg.kernel == "base"
    assert msg.iteration == 2
    assert msg.status == "ok"
    assert msg.message == "used cached actor response"
    assert msg.details == {}


def test_kernel_phase_with_error_status_carries_error_info() -> None:
    msg = KernelPhase(
        **_COMMON,
        phase="acting",
        kernel="base",
        status="error",
        error=ErrorInfo(
            code="timeout",
            message="actor base timed out after 30s",
            details={"failed_phase": "acting", "exception_type": "TimeoutError"},
        ),
        details={"actor_name": "openai", "actor_duration_ms": 30001.2},
    )
    assert msg.status == "error"
    assert msg.error is not None
    assert msg.error.code == "timeout"
    assert msg.error.details["failed_phase"] == "acting"
    # Wide-event details on the phase itself remain untouched
    assert msg.details["actor_name"] == "openai"


def test_kernel_phase_carries_wide_event_details() -> None:
    msg = KernelPhase(
        **_COMMON,
        phase="acting",
        kernel="base",
        details={
            "actor_name": "echo",
            "actor_duration_ms": 12.4,
            "actor_status": "ok",
        },
    )
    assert msg.details["actor_name"] == "echo"
    assert msg.details["actor_status"] == "ok"


# ---------------------------------------------------------------------------
# ErrorInfo / Failable
# ---------------------------------------------------------------------------


def test_error_info_requires_non_empty_code() -> None:
    with pytest.raises(ValueError, match="non-empty code"):
        ErrorInfo(code="")


def test_error_info_defaults() -> None:
    err = ErrorInfo(code="timeout")
    assert err.code == "timeout"
    assert err.message == ""
    assert err.details == {}


def test_failable_invariant_is_inheritable() -> None:
    """Any class composed of Failable + RobotEvent gets the invariant."""

    # Sanity: KernelPhase is the canonical ``Failable`` event.
    assert issubclass(KernelPhase, Failable)
    assert issubclass(ComponentResponse, Failable)
    assert issubclass(RobotOutput, Failable)
