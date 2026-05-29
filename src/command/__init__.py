"""Command layer: components advertise commands, channels invoke them.

Public API:

- :class:`CommandSpec` / :class:`RiskClass` -- the static declaration a
  component lists in ``provides_commands``.
- :func:`wire_commands` -- turn those specs into bus subscriptions at
  ``start`` time.

The bus event types (:class:`~src.bus.messages.CommandRequest`,
:class:`~src.bus.messages.CommandResponse`,
:class:`~src.bus.messages.CommandNotify`) live in :mod:`src.bus.messages`
alongside the rest of the wire vocabulary.
"""

from src.command.spec import CommandSpec, RiskClass
from src.command.wiring import wire_commands

__all__ = [
    "CommandSpec",
    "RiskClass",
    "wire_commands",
]
