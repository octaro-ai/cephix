"""Echo actor package.

Tiny driver that mirrors its input back. Lives in its own
subpackage so future debug-lens utilities (an "echo with delay",
an "echo into a file") can sit next to it without polluting the
``src.actor`` namespace.

Public re-export: ``from src.actor.echo import EchoActor``
remains the canonical import path.
"""

from src.actor.echo.actor import EchoActor

__all__ = ["EchoActor"]
