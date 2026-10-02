"""
Координати й висота: у спокої читаються раз на 15 с, а коли точність потрібна — раз на секунду.

Кожне читання — це запуск tesseract, а при кількох вікнах їх десятки на секунду; стоячому
на фармі персонажу це зайве. Але повернення додому й воскресіння звіряють координати за
секунди (verify_timeout 20 с) — для них повільний режим зламав би прильот.
"""
from __future__ import annotations

import pytest
from PIL import Image

from app.pipelines import altitude as altitude_mod
from app.pipelines import position as position_mod
from app.pipelines.altitude import AltitudeConfig, AltitudePipeline
from app.pipelines.base import Frame, PipelineContext
from app.pipelines.position import PositionConfig, PositionPipeline
from app.pipelines.shared import SHARED_POS, Position, set_busy, set_mounted

IMG = Image.new("RGB", (4, 4))


class Reader:
    """Підробка read_numbers: рахує виклики й віддає задане значення."""

    def __init__(self, value):
        self.value = value
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        return list(self.value)


def tick(pipe, t, shared):
    ctx = PipelineContext(window="t", frame=Frame(image=IMG, ts=t), shared=shared)
    pipe.process(ctx)


def settle(pipe, shared, t0=0.0):
    """Довести блок до «відомого місця» (потрібно два схожі читання підряд)."""
    tick(pipe, t0, shared)
    tick(pipe, t0 + 1.0, shared)
    return t0 + 1.0


@pytest.fixture
def pos_pipe(monkeypatch):
    reader = Reader([456, 674])
    monkeypatch.setattr(position_mod, "read_numbers", reader)
    pipe = PositionPipeline(PositionConfig(home_x=456, home_y=674, read_every=1.0, calm_every=15.0))
    return pipe, reader, {}


def test_position_reads_every_second_until_the_place_is_known(pos_pipe):
    pipe, reader, shared = pos_pipe
    tick(pipe, 0.0, shared)
    tick(pipe, 1.0, shared)
    assert pipe.pos.known and reader.calls == 2, "два читання підряд, раз на секунду"


def test_position_goes_calm_once_known_and_near_home(pos_pipe):
    pipe, reader, shared = pos_pipe
    t = settle(pipe, shared)
    before = reader.calls
    for i in range(1, 15):
        tick(pipe, t + i, shared)
    assert reader.calls == before, "14 секунд спокою — жодного читання"
    tick(pipe, t + 15.0, shared)
    assert reader.calls == before + 1, "на 15-й секунді — одне"


@pytest.mark.parametrize("who", ["return_home", "death_return"])
def test_position_goes_fast_while_a_trip_is_running(pos_pipe, who):
    """Поїздка додому/після смерті звіряє координати за секунди — тут повільно не можна."""
    pipe, reader, shared = pos_pipe
    t = settle(pipe, shared)
    set_busy(shared, who, True)
    before = reader.calls
    for i in range(1, 4):
        tick(pipe, t + i, shared)
    assert reader.calls == before + 3


def test_position_goes_fast_when_the_character_drifts_away(monkeypatch):
    reader = Reader([456, 674])
    monkeypatch.setattr(position_mod, "read_numbers", reader)
    pipe = PositionPipeline(PositionConfig(home_x=456, home_y=674, calm_every=15.0, calm_radius=6.0))
    shared: dict = {}
    t = settle(pipe, shared)
    reader.value = [456, 684]                      # відтяг на 10 — далі за calm_radius
    tick(pipe, t + 15.0, shared)
    assert pipe.pos.y == 684
    before = reader.calls
    for i in range(1, 4):
        tick(pipe, t + 15.0 + i, shared)
    assert reader.calls == before + 3, "відійшов від дому — знову раз на секунду"


def test_position_calm_can_be_switched_off(monkeypatch):
    reader = Reader([456, 674])
    monkeypatch.setattr(position_mod, "read_numbers", reader)
    pipe = PositionPipeline(PositionConfig(home_x=456, home_y=674, calm_every=0))
    shared: dict = {}
    t = settle(pipe, shared)
    before = reader.calls
    for i in range(1, 6):
        tick(pipe, t + i, shared)
    assert reader.calls == before + 5


def test_position_goes_fast_again_after_failed_reads(monkeypatch):
    reader = Reader([456, 674])
    monkeypatch.setattr(position_mod, "read_numbers", reader)
    pipe = PositionPipeline(PositionConfig(home_x=456, home_y=674, calm_every=15.0))
    shared: dict = {}
    t = settle(pipe, shared)
    reader.value = []                              # панель перекрили
    tick(pipe, t + 15.0, shared)
    assert pipe.misses == 1
    before = reader.calls
    tick(pipe, t + 16.0, shared)
    assert reader.calls == before + 1, "після невдачі не чекаємо 15 с — перечитуємо одразу"


def test_altitude_calm_then_fast_when_mounted_or_on_a_trip(monkeypatch):
    reader = Reader([22])
    monkeypatch.setattr(altitude_mod, "read_numbers", reader)
    pipe = AltitudePipeline(AltitudeConfig(read_every=1.0, calm_every=15.0))
    shared: dict = {}
    tick(pipe, 0.0, shared)
    assert pipe.alt.known and reader.calls == 1
    for i in range(1, 15):
        tick(pipe, float(i), shared)
    assert reader.calls == 1, "на землі й у спокої — раз на 15 с"
    tick(pipe, 15.0, shared)
    assert reader.calls == 2

    set_mounted(shared, True)                       # верхи — висота важлива (посадка)
    tick(pipe, 16.0, shared)
    tick(pipe, 17.0, shared)
    assert reader.calls == 4

    set_mounted(shared, False)
    set_busy(shared, "return_home", True)
    tick(pipe, 18.0, shared)
    assert reader.calls == 5


def test_calm_defaults_are_on():
    assert PositionConfig().calm_every == 15.0 and AltitudeConfig().calm_every == 15.0
