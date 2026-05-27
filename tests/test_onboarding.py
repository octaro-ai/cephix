"""Tests for the onboarding wizard's prompt-filtering logic.

Full end-to-end Rich-based UX is exercised manually; here we focus on
the deterministic behaviour: which fields the wizard asks for, and how
it handles existing values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from src.actor.echo import EchoActor
from src.channels.websocket import WebsocketChannel
from src.configuration import CONTROL_PLANE_TOKEN_ENV, load_robot_env
from src.onboarding import _ask_for_kwargs, _ensure_control_plane_token


class _RecordingPrompt:
    """Stand-in for rich.prompt.Prompt.ask that records every call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses: dict[str, str] = {}

    def ask(self, prompt: str, *, default: str = "", console: Any = None) -> str:
        self.calls.append({"prompt": prompt, "default": default})
        return self.responses.get(prompt, default)


def test_wizard_only_prompts_for_allowlisted_fields() -> None:
    """EchoActor exposes only ``prefix``; nothing else must be asked."""
    recorder = _RecordingPrompt()
    with patch("src.onboarding.Prompt", recorder):
        answers = _ask_for_kwargs(console=None, cls=EchoActor, existing={})

    prompted = [c["prompt"] for c in recorder.calls]
    assert any("prefix" in p for p in prompted)
    # Default of prefix returned unchanged -> nothing recorded
    assert "prefix" not in answers


def test_wizard_records_changed_value_for_allowlisted_field() -> None:
    recorder = _RecordingPrompt()
    recorder.responses = {"  echo.prefix": "yo: "}
    with patch("src.onboarding.Prompt", recorder):
        answers = _ask_for_kwargs(console=None, cls=EchoActor, existing={})
    assert answers == {"prefix": "yo: "}


def test_wizard_only_asks_websocket_allowlisted_fields() -> None:
    """WebsocketChannel exposes ``host``+``port``; topic plumbing stays silent."""
    recorder = _RecordingPrompt()
    with patch("src.onboarding.Prompt", recorder):
        _ask_for_kwargs(console=None, cls=WebsocketChannel, existing={})

    prompted = [c["prompt"] for c in recorder.calls]
    assert any("host" in p for p in prompted)
    assert any("port" in p for p in prompted)
    assert not any("input_topic" in p for p in prompted)
    assert not any("output_topic" in p for p in prompted)


def test_wizard_preserves_existing_values_for_blocked_fields() -> None:
    """If a previous robot.yaml had a non-default plumbing parameter,
    the wizard keeps it instead of silently reverting to the
    constructor default."""
    recorder = _RecordingPrompt()
    with patch("src.onboarding.Prompt", recorder):
        answers = _ask_for_kwargs(
            console=None,
            cls=WebsocketChannel,
            existing={"input_topic": "custom.input"},
        )
    assert answers["input_topic"] == "custom.input"


def test_wizard_skips_blocked_field_matching_default() -> None:
    recorder = _RecordingPrompt()
    with patch("src.onboarding.Prompt", recorder):
        answers = _ask_for_kwargs(
            console=None,
            cls=WebsocketChannel,
            existing={"input_topic": "input.message"},
        )
    assert "input_topic" not in answers


def test_ensure_control_plane_token_creates_env_when_missing(
    tmp_path: Path,
) -> None:
    """First call generates a fresh token and writes it into .env."""
    env_path = _ensure_control_plane_token(tmp_path)
    assert env_path == tmp_path / ".env"
    env = load_robot_env(tmp_path)
    token = env[CONTROL_PLANE_TOKEN_ENV]
    assert len(token) >= 32  # secrets.token_hex(32) -> 64 hex chars
    assert token.isalnum()


def test_ensure_control_plane_token_keeps_existing_token(
    tmp_path: Path,
) -> None:
    """Re-running the wizard does not rotate an existing token."""
    (tmp_path / ".env").write_text(
        f"{CONTROL_PLANE_TOKEN_ENV}=keep-this\n", encoding="utf-8"
    )
    _ensure_control_plane_token(tmp_path)
    env = load_robot_env(tmp_path)
    assert env[CONTROL_PLANE_TOKEN_ENV] == "keep-this"
