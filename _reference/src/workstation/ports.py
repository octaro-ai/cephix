from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str


class WorkstationPort(Protocol):
    """Backend-agnostic interface to a persistent workstation.

    Implementations may use Docker, SSH, or any other mechanism
    to execute commands and transfer files on an isolated machine.
    """

    def start(self) -> dict[str, Any]:
        """Start or resume the workstation. Returns status info."""
        ...

    def stop(self) -> None:
        """Stop the workstation (preserving state)."""
        ...

    def status(self) -> dict[str, Any]:
        """Return current workstation status and configuration."""
        ...

    def exec(self, command: str, *, timeout: int = 30) -> ExecResult:
        """Execute a shell command on the workstation."""
        ...

    def put_file(self, path: str, content: bytes) -> None:
        """Write a file to the workstation."""
        ...

    def get_file(self, path: str) -> bytes:
        """Read a file from the workstation."""
        ...
