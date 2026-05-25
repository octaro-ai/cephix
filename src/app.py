"""Legacy demo entry point: a robot built from hard-coded components.

This module predates the configuration-driven toolchain in :mod:`src.cli`
and is kept around as a smoke-test path: it boots a robot exactly the
way the iteration-2 docs describe it, without touching ``~/.cephix/``.

Prefer ``cephix`` (the console script defined by :mod:`src.cli`) for
real use. ``python -m src.app`` will keep working but it does not
participate in onboarding, configuration, multi-instance handling or
``cephix.yaml`` defaults.
"""

from __future__ import annotations

import argparse

from src.bus import AsyncioBus
from src.channels import WebsocketChannel
from src.kernel import EchoKernel
from src.logging_config import configure_logging
from src.robot import Robot


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Legacy hard-coded robot daemon. Prefer the 'cephix' CLI."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Path to a rotating log file. Defaults to console (stderr).",
    )
    args = parser.parse_args()

    configure_logging(level=args.log_level, log_file=args.log_file)

    bus = AsyncioBus()
    kernel = EchoKernel()
    websocket = WebsocketChannel(host=args.host, port=args.port)
    robot = Robot(bus=bus, kernel=kernel, channels=[websocket])

    robot.run()


if __name__ == "__main__":
    main()
