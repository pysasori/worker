"""Буфер подій у пам'яті: єдине джерело правди про те, що бот РЕАЛЬНО вирішив."""
from __future__ import annotations

import logging

from app.core.logging import RingBufferHandler


def make_record(name: str, level: int, message: str) -> logging.LogRecord:
    return logging.LogRecord(name=name, level=level, pathname=__file__, lineno=1,
                             msg=message, args=(), exc_info=None)


def test_keeps_only_the_last_n_events():
    buf = RingBufferHandler(capacity=3)
    for i in range(5):
        buf.emit(make_record("A", logging.INFO, f"подія {i}"))
    events = buf.recent(limit=10)
    assert [e.message for e in events] == ["подія 2", "подія 3", "подія 4"]


def test_filters_by_window_and_keeps_others_intact():
    buf = RingBufferHandler(capacity=100)
    buf.emit(make_record("A", logging.INFO, "для A"))
    buf.emit(make_record("B", logging.INFO, "для B"))
    assert [e.message for e in buf.recent(window="A")] == ["для A"]
    assert [e.message for e in buf.recent(window="B")] == ["для B"]
    assert len(buf.recent()) == 2


def test_filters_by_minimum_level():
    buf = RingBufferHandler(capacity=100)
    buf.emit(make_record("A", logging.INFO, "звичайна"))
    buf.emit(make_record("A", logging.WARNING, "тривога"))
    only_warn = buf.recent(level="WARNING")
    assert [e.message for e in only_warn] == ["тривога"]


def test_limit_returns_the_newest_events():
    buf = RingBufferHandler(capacity=100)
    for i in range(10):
        buf.emit(make_record("A", logging.INFO, str(i)))
    assert [e.message for e in buf.recent(limit=3)] == ["7", "8", "9"]
