"""Context compaction strategies.

A compactor takes a list of older interactions and produces a condensed
text summary.  The summary is included in ``build_context()`` so the LLM
retains awareness of earlier conversation without consuming the full
token budget.

Two built-in strategies:

* ``NullCompactor`` -- returns empty string (no compaction).
* ``TruncatingCompactor`` -- extracts key lines from each interaction,
  producing a bullet-point synopsis.  No LLM required.

A future ``LLMCompactor`` can call the planner to generate a richer
summary once the real LLM integration is in place.
"""

from __future__ import annotations

from typing import Protocol

from src.domain import InteractionRecord


class CompactionStrategy(Protocol):
    """Produce a text summary from a list of interactions."""

    def compact(self, interactions: list[InteractionRecord]) -> str:
        ...


class NullCompactor:
    """No-op compactor -- always returns empty string."""

    def compact(self, interactions: list[InteractionRecord]) -> str:
        return ""


class TruncatingCompactor:
    """Rule-based compactor that creates a bullet-point synopsis.

    Each interaction is reduced to a single line showing the user's
    message and a truncated robot response.  This is good enough to
    preserve topic awareness without an LLM call.
    """

    def __init__(self, max_response_chars: int = 80) -> None:
        self._max_response_chars = max_response_chars

    def compact(self, interactions: list[InteractionRecord]) -> str:
        if not interactions:
            return ""

        lines: list[str] = []
        for ix in interactions:
            response = ix.robot_text
            if len(response) > self._max_response_chars:
                response = response[: self._max_response_chars] + "..."
            lines.append(f"- User: {ix.user_text} -> Robot: {response}")

        header = f"Summary of {len(interactions)} earlier message(s):"
        return header + "\n" + "\n".join(lines)
