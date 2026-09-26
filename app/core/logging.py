"""Налаштування логів: кожен рядок підписаний іменем вікна."""
from __future__ import annotations

import logging
import sys

from app.core.settings import settings

_CONFIGURED = False


def setup_logging(level: str | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s [%(name)s] %(message)s", datefmt="%H:%M:%S"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, (level or settings.LOG_LEVEL).upper(), logging.INFO))
    _CONFIGURED = True


def window_logger(window_name: str) -> logging.Logger:
    return logging.getLogger(window_name)
