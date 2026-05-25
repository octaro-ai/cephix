"""Central operational logging configuration.

The cephix process configures Python's stdlib :mod:`logging` once, in
one place. Components stay clean: they only emit through
``logging.getLogger(__name__)`` and never decide where the bytes go.

This module is intentionally minimal. ``logging`` already separates
emission from routing, so we do not need a custom facade -- this
function is just the routing setup. Future iterations can add new
sinks (database, syslog, structured JSON) by extending
:func:`configure_logging`; no component code needs to change.

Note that this is operational logging only -- the lifecycle of the
robot, errors, health. The behavioural audit trail (every
``RobotInput``/``RobotOutput``, every kernel decision) is a separate
concern and lives in a dedicated bus component, not in
:mod:`logging`.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"


def configure_logging(
    *,
    level: str = "INFO",
    log_file: str | None = None,
    max_bytes: int = 10_000_000,
    backup_count: int = 5,
    fmt: str = DEFAULT_FORMAT,
    datefmt: str = DEFAULT_DATEFMT,
) -> None:
    """Configure the root logger for the cephix process.

    The default sink is the console (``stderr``). Pass ``log_file`` to
    rotate operational logs into a file instead. Calling this function
    is idempotent: previously installed handlers on the root logger
    are removed first, so it is safe to call from tests or from
    long-running entry points.

    Future extensions (e.g. a SQLite handler) plug in here without
    changing any component.
    """
    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(fmt=fmt, datefmt=datefmt)

    handler: logging.Handler
    if log_file:
        handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
    else:
        handler = logging.StreamHandler()

    handler.setFormatter(formatter)
    root.addHandler(handler)
