"""Tests for the onboarding wizard's prompt-filtering logic.

Full end-to-end Rich-based UX is exercised manually; here we focus on
the deterministic behaviour: which fields the wizard asks for, and how
it handles existing values.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from src.kernel.echo import EchoKernel
from src.onboarding import _ask_for_kwargs


class _RecordingPrompt:
    """Stand-in for rich.prompt.Prompt.ask that records every call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses: dict[str, str] = {}

    def ask(self, prompt: str, *, default: str = "", console: Any = None) -> str:
        self.calls.append({"prompt": prompt, "default": default})
        return self.responses.get(prompt, default)


def test_wizard_only_prompts_for_allowlisted_fields() -> None:
    """EchoKernel exposes only ``prefix``; topics must remain silent."""
    recorder = _RecordingPrompt()
    with patch("src.onboarding.Prompt", recorder):
        answers = _ask_for_kwargs(console=None, cls=EchoKernel, existing={})

    prompted = [c["prompt"] for c in recorder.calls]
    assert any("prefix" in p for p in prompted)
    assert not any("input_topic" in p for p in prompted)
    assert not any("output_topic" in p for p in prompted)
    # Default of prefix returned unchanged -> nothing recorded
    assert "prefix" not in answers


def test_wizard_records_changed_value_for_allowlisted_field() -> None:
    recorder = _RecordingPrompt()
    recorder.responses = {"  echo.prefix": "yo: "}
    with patch("src.onboarding.Prompt", recorder):
        answers = _ask_for_kwargs(console=None, cls=EchoKernel, existing={})
    assert answers == {"prefix": "yo: "}


def test_wizard_preserves_existing_values_for_blocked_fields() -> None:
    """If a previous robot.yaml had a non-default topic, the wizard
    keeps it instead of silently reverting to the constructor default."""
    recorder = _RecordingPrompt()
    with patch("src.onboarding.Prompt", recorder):
        answers = _ask_for_kwargs(
            console=None,
            cls=EchoKernel,
            existing={"input_topic": "custom.in", "output_topic": "custom.out"},
        )
    assert answers["input_topic"] == "custom.in"
    assert answers["output_topic"] == "custom.out"


def test_wizard_skips_blocked_field_matching_default() -> None:
    recorder = _RecordingPrompt()
    with patch("src.onboarding.Prompt", recorder):
        answers = _ask_for_kwargs(
            console=None,
            cls=EchoKernel,
            existing={"input_topic": "input.message"},
        )
    assert "input_topic" not in answers
