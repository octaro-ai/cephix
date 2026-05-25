"""Cephix robot daemon.

Boots a robot composed from:

- an :class:`AsyncioBus` (the system bus);
- an :class:`EchoKernel` (the active kernel implementation);
- a :class:`WebsocketChannel` (the outside-world boundary).

The process runs until SIGINT (Ctrl-C). External clients communicate
with the robot through the WebSocket channel, e.g. ``python -m
src.cli_client``.

This module is deliberately tiny: it parses CLI arguments, configures
operational logging once, builds the robot from its three axes, and
hands control over to ``robot.run()``. Everything operationally
interesting (boot sequence, errors, shutdown) is logged by the robot
and its components -- not from here.

Run with::

    python -m src.app
    python -m src.app --host 0.0.0.0 --port 8765
    python -m src.app --log-file /var/log/cephix/cephix.log
"""

from __future__ import annotations

import argparse

from src.bus import AsyncioBus
from src.channels import WebsocketChannel
from src.kernel import EchoKernel
from src.logging_config import configure_logging
from src.robot import Robot


def main() -> None:
    parser = argparse.ArgumentParser(description="Cephix robot daemon.")
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
