"""Tests for :func:`configure_logging`."""

from __future__ import annotations

import logging
from pathlib import Path

from src.logging_config import configure_logging


def test_configure_logging_console_by_default() -> None:
    configure_logging(level="DEBUG")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0], logging.StreamHandler)


def test_configure_logging_writes_to_file(tmp_path: Path) -> None:
    log_file = tmp_path / "cephix.log"

    configure_logging(level="INFO", log_file=str(log_file))
    try:
        logging.getLogger("test").info("hello-from-file-handler")
        for handler in logging.getLogger().handlers:
            handler.flush()
    finally:
        # Detach the file handler so the OS releases the lock before tmp_path
        # is cleaned up on Windows.
        for handler in list(logging.getLogger().handlers):
            handler.close()
            logging.getLogger().removeHandler(handler)

    assert log_file.exists()
    assert "hello-from-file-handler" in log_file.read_text(encoding="utf-8")


def test_configure_logging_is_idempotent() -> None:
    configure_logging(level="INFO")
    configure_logging(level="INFO")
    configure_logging(level="WARNING")

    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert root.level == logging.WARNING
