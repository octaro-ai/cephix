"""Tests for the bus message types."""

from __future__ import annotations

import pytest

from src.bus import RobotInput, RobotOutput, RobotRequest, RobotResponse


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
        RobotRequest(**_COMMON, action="tool.mail.list")


def test_robot_request_requires_action() -> None:
    with pytest.raises(ValueError, match="action"):
        RobotRequest(**_COMMON, correlation_id="corr-1", action="")


def test_robot_response_requires_correlation_id() -> None:
    with pytest.raises(ValueError, match="correlation_id"):
        RobotResponse(**_COMMON)


def test_failed_response_requires_error() -> None:
    with pytest.raises(ValueError, match="error"):
        RobotResponse(**_COMMON, correlation_id="corr-1", ok=False)


def test_successful_response() -> None:
    msg = RobotResponse(
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
