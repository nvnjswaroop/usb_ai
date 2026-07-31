"""Centralised logger configuration for USB AI.

All modules use `logging.getLogger("usbai")` — never `print()` for
internal events. Configure once in main.py at startup.

Levels:
  DEBUG   — token streaming, thread lifecycle, cache hits/misses
  INFO    — model load/unload, session saves, tool invocations
  WARNING — rate limits, auth failures, path denials, backup failures
  ERROR   — unexpected exceptions, load failures
"""
from __future__ import annotations

import logging
import sys


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the usbai logger. Call once from main.py at startup."""
    logger = logging.getLogger("usbai")
    logger.setLevel(level)

    # Avoid duplicate handlers if called twice (e.g. re-import during tests)
    if logger.handlers:
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    fmt = logging.Formatter(
        "[%(levelname)s] %(name)s — %(message)s",
        style="%",
    )
    handler.setFormatter(fmt)
    logger.addHandler(handler)

    # Quiet down noisy third-party loggers
    for noisy in ("uvicorn", "fastapi", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# Convenience aliases used throughout the codebase
getLogger = logging.getLogger  # at module import time: _log = getLogger("usbai")