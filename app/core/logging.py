"""
Налаштування логів: кожен рядок підписаний іменем вікна.

Крім виводу в консоль, останні події тримаються в пам'яті (RingBufferHandler) —
незалежно від того, як запущено процес і чи перенаправлено stdout у файл. Так
сторінка (і будь-хто, хто зайшов подивитись) бачить, що бот РЕАЛЬНО вирішив і
чому, а не тільки поточний статус-рядок: він каже «лечу» чи «б'ю», але не каже,
чому саме зараз, а статус-рядок одного тіку цього не пояснює. Без цього кожен
розбір бага починався з «а де лог» — процес міг стартувати руками, без
--log-level чи без перенаправлення виводу, і рядки просто губились.
"""
from __future__ import annotations

import logging
import sys
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any

from app.core.settings import settings

_CONFIGURED = False
_EVENTS_CAPACITY = 4000


@dataclass
class LogEvent:
    ts: float
    window: str            # ім'я логера = ім'я вікна (bot/web — службові)
    level: str
    message: str

    def public(self) -> dict[str, Any]:
        return {"ts": self.ts, "window": self.window, "level": self.level, "message": self.message}


class RingBufferHandler(logging.Handler):
    """Останні N подій у пам'яті. Потокобезпечний: пише кожен потік вікна."""

    def __init__(self, capacity: int = _EVENTS_CAPACITY) -> None:
        super().__init__()
        self._events: deque[LogEvent] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        event = LogEvent(ts=record.created, window=record.name, level=record.levelname,
                         message=record.getMessage())
        with self._lock:
            self._events.append(event)

    def recent(self, window: str | None = None, level: str | None = None,
              limit: int = 200) -> list[LogEvent]:
        with self._lock:
            events = list(self._events)
        if window:
            events = [e for e in events if e.window == window]
        if level:
            floor = logging.getLevelName(level.upper())
            if isinstance(floor, int):
                events = [e for e in events if logging.getLevelName(e.level) >= floor]
        return events[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


ring_buffer = RingBufferHandler()


def setup_logging(level: str | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s [%(name)s] %(message)s", datefmt="%H:%M:%S"))
    ring_buffer.setLevel(logging.INFO)     # DEBUG засмічував би буфер щотіковими дрібницями
    root = logging.getLogger()
    root.handlers[:] = [handler, ring_buffer]
    root.setLevel(getattr(logging, (level or settings.LOG_LEVEL).upper(), logging.INFO))
    _CONFIGURED = True


def window_logger(window_name: str) -> logging.Logger:
    return logging.getLogger(window_name)
