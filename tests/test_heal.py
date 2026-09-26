"""Відхіл персонажа: читання власної смужки HP і банка по порогу."""
from __future__ import annotations

import pytest
from PIL import Image

from app.pipelines.actions import PressKey
from app.pipelines.base import Frame, PipelineContext
from app.pipelines.heal import HealConfig, HealPipeline
from app.pipelines.shared import SHARED_PLAYER
from app.vision.player import PlayerBarConfig, read_player_hp
from tests.conftest import FIXTURES


def keys(res) -> list[str]:
    return [a.key for a in res.actions if isinstance(a, PressKey)]


def frame_with_hp(fraction: float, cfg: HealConfig | None = None) -> Image.Image:
    """Кадр із заданою смужкою HP персонажа (з білим написом поверх, як у грі)."""
    bar = (cfg or HealConfig()).bar
    img = Image.new("RGB", (400, 120), (24, 36, 44))
    px = img.load()
    x0 = bar.region.x + 3
    for y in range(bar.region.y + 2, bar.region.y + bar.region.h - 2):
        for x in range(x0, x0 + int(bar.width * fraction)):
            px[x, y] = (214, 60, 38)
    for y in range(bar.region.y + 3, bar.region.y + 8):        # напис «769/777»
        for x in range(x0 + 55, x0 + 70):
            px[x, y] = (255, 255, 255)
    return img


def ctx_for(image: Image.Image, ts: float, shared: dict) -> PipelineContext:
    return PipelineContext(window="t", frame=Frame(image=image, ts=ts), shared=shared)


# ---- читання смужки -----------------------------------------------------------
def test_reads_real_frame():
    """Справжній кадр гри: у персонажа 635 із 777."""
    frame = Image.open(FIXTURES / "frame_1440_player_82.png").convert("RGB")
    reading = read_player_hp(frame)
    assert reading.present
    assert abs(reading.percent - 82) <= 2


@pytest.mark.parametrize("fraction", [1.0, 0.75, 0.5, 0.2])
def test_reads_any_level(fraction):
    reading = read_player_hp(frame_with_hp(fraction))
    assert abs(reading.ratio - fraction) <= 0.04, "білий напис не має рвати смужку"


def test_no_bar_no_reading():
    assert not read_player_hp(Image.new("RGB", (400, 120), (18, 30, 24))).present


# ---- сам блок -----------------------------------------------------------------
def test_drinks_below_the_threshold():
    pipe, shared = HealPipeline(HealConfig()), {}
    res = pipe.process(ctx_for(frame_with_hp(0.4), 0.0, shared))
    assert keys(res) == ["f6"], "клавіша банки за замовчуванням — F6"
    assert shared[SHARED_PLAYER].present


def test_full_hp_needs_no_potion():
    pipe, shared = HealPipeline(HealConfig()), {}
    assert keys(pipe.process(ctx_for(frame_with_hp(0.9), 0.0, shared))) == []


def test_respects_the_cooldown():
    cfg = HealConfig(cooldown=10.0, panic_below=0)
    pipe, shared = HealPipeline(cfg), {}
    hurt = frame_with_hp(0.4)
    assert keys(pipe.process(ctx_for(hurt, 0.0, shared))) == ["f6"]
    assert keys(pipe.process(ctx_for(hurt, 5.0, shared))) == [], "сумка не безмежна"
    assert keys(pipe.process(ctx_for(hurt, 11.0, shared))) == ["f6"]


def test_panic_shortens_the_pause():
    """При зовсім малому HP чекати повний кулдаун — це смерть."""
    cfg = HealConfig(cooldown=10.0, panic_below=0.25)
    pipe, shared = HealPipeline(cfg), {}
    pipe.process(ctx_for(frame_with_hp(0.4), 0.0, shared))
    assert keys(pipe.process(ctx_for(frame_with_hp(0.15), 6.0, shared))) == ["f6"]


def test_hp_lands_on_the_shared_board():
    """Приклик пета дивиться сюди, щоб зрозуміти, що нас б'ють."""
    pipe, shared = HealPipeline(HealConfig()), {}
    pipe.process(ctx_for(frame_with_hp(0.6), 0.0, shared))
    assert abs(shared[SHARED_PLAYER].ratio - 0.6) <= 0.04


def test_shouts_when_the_potions_ran_out():
    """
    Живий випадок: комірка F6 спорожніла, бот тиснув її до самої смерті, а в лозі
    щоразу було лише «HP 30% -> f6». Тепер видно, що банка не діє.
    """
    pipe, shared = HealPipeline(HealConfig(cooldown=0, potion_check=1.0, potion_misses=3)), {}
    low = frame_with_hp(0.3)
    events, ts = [], 0.0
    for _ in range(6):
        events += pipe.process(ctx_for(low, ts, shared)).events        # випили
        ts += 1.5
        events += pipe.process(ctx_for(low, ts, shared)).events        # життя не додалось
        ts += 1.5
    assert any("банки скінчились" in e for e in events)


def test_working_potion_raises_no_alarm():
    pipe, shared = HealPipeline(HealConfig(cooldown=0, potion_check=1.0, potion_misses=2)), {}
    events, ts = [], 0.0
    for i in range(6):
        events += pipe.process(ctx_for(frame_with_hp(0.3), ts, shared)).events
        ts += 1.5
        events += pipe.process(ctx_for(frame_with_hp(0.6), ts, shared)).events   # банка подіяла
        ts += 1.5
    assert not any("скінчились" in e for e in events)
