"""Tests for the bus message types."""

from __future__ import annotations

import pytest

from src.bus import (
    ComponentInfo,
    ComponentLifecycle,
    ComponentRequest,
    ComponentResponse,
    ErrorInfo,
    Failable,
    KernelPhase,
    LifecycleAware,
    MountEvent,
    RobotInput,
    RobotLifecycle,
    RobotOutput,
    component_lifecycle_topic,
    component_mount_topic,
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


# ---------------------------------------------------------------------------
# Failable: warn status (trinary ResultStatus)
# ---------------------------------------------------------------------------


def test_robot_output_warn_status_requires_error_info() -> None:
    with pytest.raises(ValueError, match="status='warn' requires"):
        RobotOutput(**_COMMON, message="hi", status="warn")


def test_robot_output_warn_carries_error_info() -> None:
    msg = RobotOutput(
        **_COMMON,
        message="Antwort aus Cache",
        status="warn",
        error=ErrorInfo(code="cache_fallback", message="upstream timed out"),
    )
    assert msg.status == "warn"
    assert msg.error is not None
    assert msg.error.code == "cache_fallback"


def test_component_response_warn_carries_error_info() -> None:
    msg = ComponentResponse(
        **_COMMON,
        correlation_id="corr-1",
        status="warn",
        error=ErrorInfo(code="partial_result"),
        payload={"items": [1, 2]},
    )
    assert msg.status == "warn"
    assert msg.payload == {"items": [1, 2]}


def test_kernel_phase_warn_carries_error_info() -> None:
    msg = KernelPhase(
        **_COMMON,
        phase="acting",
        kernel="base",
        status="warn",
        error=ErrorInfo(code="rate_limit_retry", details={"attempts": 2}),
    )
    assert msg.status == "warn"
    assert msg.error is not None
    assert msg.error.details["attempts"] == 2


# ---------------------------------------------------------------------------
# LifecycleAware mixin
# ---------------------------------------------------------------------------


def test_robot_lifecycle_inherits_lifecycle_aware() -> None:
    assert issubclass(RobotLifecycle, LifecycleAware)
    assert issubclass(ComponentLifecycle, LifecycleAware)


def test_robot_lifecycle_accepts_extended_phases() -> None:
    for phase in ("boot", "ready", "warn", "failure", "shutdown"):
        evt = RobotLifecycle(**_COMMON, phase=phase)
        assert evt.phase == phase


def test_robot_lifecycle_rejects_unknown_phase() -> None:
    with pytest.raises(ValueError, match="phase must be one of"):
        RobotLifecycle(**_COMMON, phase="bogus")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ComponentInfo.metadata
# ---------------------------------------------------------------------------


def test_component_info_metadata_defaults_to_empty_dict() -> None:
    info = ComponentInfo(category="actor", name="echo")
    assert info.metadata == {}


def test_component_info_carries_metadata() -> None:
    info = ComponentInfo(
        category="actor",
        name="openai",
        metadata={
            "model": "gpt-5",
            "context_window_tokens": 128_000,
            "provider": "openai",
        },
    )
    assert info.metadata["model"] == "gpt-5"
    assert info.metadata["context_window_tokens"] == 128_000


# ---------------------------------------------------------------------------
# ComponentLifecycle
# ---------------------------------------------------------------------------


def test_component_lifecycle_topic_format() -> None:
    assert component_lifecycle_topic("echo") == "component.echo.lifecycle"
    assert component_lifecycle_topic("kernel.base") == "component.kernel.base.lifecycle"


def test_component_lifecycle_topic_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="non-empty name"):
        component_lifecycle_topic("")


def test_component_lifecycle_event_basics() -> None:
    info = ComponentInfo(category="actor", name="echo")
    evt = ComponentLifecycle(
        topic=component_lifecycle_topic("echo"),
        principal="robot:alpha",
        source="robot",
        run_id="boot-abc",
        phase="ready",
        info=info,
    )
    assert evt.topic == "component.echo.lifecycle"
    assert evt.phase == "ready"
    assert evt.info.name == "echo"
    assert evt.parent == ""
    assert evt.message == ""


def test_component_lifecycle_with_warn_carries_metadata_via_info() -> None:
    info = ComponentInfo(
        category="actor",
        name="openai",
        metadata={"model": "gpt-5", "rate_limit_remaining": 0},
    )
    evt = ComponentLifecycle(
        topic=component_lifecycle_topic("openai"),
        principal="robot:alpha",
        source="kernel.base",
        run_id="boot-abc",
        phase="warn",
        info=info,
        parent="kernel.base",
        message="rate limit hit, falling back to cache",
    )
    assert evt.phase == "warn"
    assert evt.info.metadata["rate_limit_remaining"] == 0
    assert evt.parent == "kernel.base"


def test_component_lifecycle_requires_info_with_name() -> None:
    with pytest.raises(ValueError, match="ComponentInfo.name"):
        ComponentLifecycle(
            topic="component..lifecycle",
            principal="p",
            source="s",
            run_id="r",
            info=ComponentInfo(category="actor", name=""),
        )


def test_component_lifecycle_rejects_unknown_phase() -> None:
    with pytest.raises(ValueError, match="phase must be one of"):
        ComponentLifecycle(
            topic="component.echo.lifecycle",
            principal="p",
            source="s",
            run_id="r",
            phase="bogus",  # type: ignore[arg-type]
            info=ComponentInfo(category="actor", name="echo"),
        )


# ---------------------------------------------------------------------------
# MountEvent
# ---------------------------------------------------------------------------


def test_mount_topic_format() -> None:
    assert component_mount_topic("kernel.base") == "component.kernel.base.mount"


def test_mount_event_mounted_basics() -> None:
    info = ComponentInfo(category="actor", name="echo")
    evt = MountEvent(
        topic=component_mount_topic("kernel.base"),
        principal="robot:alpha",
        source="kernel.base",
        run_id="boot-abc",
        phase="mounted",
        owner="kernel.base",
        slot="actor",
        mounted=info,
    )
    assert evt.phase == "mounted"
    assert evt.owner == "kernel.base"
    assert evt.slot == "actor"
    assert evt.mounted is not None
    assert evt.mounted.name == "echo"


def test_mount_event_unmounted_must_not_carry_info() -> None:
    info = ComponentInfo(category="actor", name="echo")
    with pytest.raises(ValueError, match="must not carry"):
        MountEvent(
            topic=component_mount_topic("kernel.base"),
            principal="p",
            source="s",
            run_id="r",
            phase="unmounted",
            owner="kernel.base",
            slot="actor",
            mounted=info,
        )


def test_mount_event_mounted_requires_component_info() -> None:
    with pytest.raises(ValueError, match="requires a mounted ComponentInfo"):
        MountEvent(
            topic=component_mount_topic("kernel.base"),
            principal="p",
            source="s",
            run_id="r",
            phase="mounted",
            owner="kernel.base",
            slot="actor",
        )


def test_mount_event_requires_owner_and_slot() -> None:
    info = ComponentInfo(category="actor", name="echo")
    with pytest.raises(ValueError, match="owner"):
        MountEvent(
            topic=component_mount_topic("kernel.base"),
            principal="p",
            source="s",
            run_id="r",
            owner="",
            slot="actor",
            mounted=info,
        )
    with pytest.raises(ValueError, match="slot"):
        MountEvent(
            topic=component_mount_topic("kernel.base"),
            principal="p",
            source="s",
            run_id="r",
            owner="kernel.base",
            slot="",
            mounted=info,
        )


def test_mount_event_rejects_unknown_phase() -> None:
    info = ComponentInfo(category="actor", name="echo")
    with pytest.raises(ValueError, match="must be"):
        MountEvent(
            topic=component_mount_topic("kernel.base"),
            principal="p",
            source="s",
            run_id="r",
            phase="bogus",  # type: ignore[arg-type]
            owner="kernel.base",
            slot="actor",
            mounted=info,
        )
