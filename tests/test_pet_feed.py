"""Годування пета: читання золотої смужки ситості і рішення про корм."""
from __future__ import annotations

import pytest
from PIL import Image

from app.pipelines.actions import PressKey
from app.pipelines.base import Frame, PipelineContext
from app.pipelines.pet_feed import PetFeedPipeline

GOLD = (230, 172, 70)
EMPTY = (26, 46, 49)


def ctx_for(image: Image.Image, ts: float, shared: dict | None = None) -> PipelineContext:
    from tests.conftest import shared_with_pet

    return PipelineContext(window="test", frame=Frame(image=image, ts=ts),
                           shared=shared_with_pet(image) if shared is None else shared)


def keys(res) -> list[str]:
    return [a.key for a in res.actions if isinstance(a, PressKey)]


def with_food(frame: Image.Image, fraction: float, feed_cfg=None) -> Image.Image:
    from tests.conftest import set_pet_food

    return set_pet_food(frame, fraction)


@pytest.fixture(scope="module")
def feed_cfg(bot_config_raw):
    from app.pipelines.pet_feed import PetFeedConfig
    from tests.conftest import _spec

    return PetFeedConfig(**_spec(bot_config_raw, "pet_feed"))


def read_food(frame: Image.Image):
    from app.vision.pet_frame import read_bar
    from tests.conftest import pet_frame_of

    pet = pet_frame_of(frame)
    filled, total = read_bar(frame, pet, pet.food_row, gold=True)
    return filled, total


def test_reads_full_food_bar(real_frame, feed_cfg):
    filled, total = read_food(with_food(real_frame, 1.0))
    assert filled * 100 // total >= 95


@pytest.mark.parametrize("fraction,expected", [(0.25, 25), (0.5, 50), (0.0, 0)])
def test_reads_partial_food_bar(real_frame, feed_cfg, fraction, expected):
    filled, total = read_food(with_food(real_frame, fraction))
    assert abs(filled * 100 // total - expected) <= 4


def test_feeds_when_hungry(real_frame, feed_cfg):
    hungry = with_food(real_frame, 0.2, feed_cfg)
    res = PetFeedPipeline(feed_cfg).process(ctx_for(hungry, 100.0))
    assert keys(res) == [feed_cfg.feed_key]


def test_feeds_at_zero(real_frame, feed_cfg):
    starving = with_food(real_frame, 0.0, feed_cfg)
    res = PetFeedPipeline(feed_cfg).process(ctx_for(starving, 100.0))
    assert keys(res) == [feed_cfg.feed_key]


def test_quiet_when_full(real_frame, feed_cfg):
    full = with_food(real_frame, 1.0, feed_cfg)
    assert keys(PetFeedPipeline(feed_cfg).process(ctx_for(full, 100.0))) == []


def test_respects_cooldown(real_frame, feed_cfg):
    hungry = with_food(real_frame, 0.1, feed_cfg)
    pipe = PetFeedPipeline(feed_cfg)
    assert keys(pipe.process(ctx_for(hungry, 0.0))) == [feed_cfg.feed_key]
    assert keys(pipe.process(ctx_for(hungry, feed_cfg.cooldown - 1))) == []
    assert keys(pipe.process(ctx_for(hungry, feed_cfg.cooldown + 1))) == [feed_cfg.feed_key]


def test_cooldown_is_configurable(feed_cfg):
    assert feed_cfg.cooldown == 40.0
    assert feed_cfg.feed_key == "f8"
    assert feed_cfg.feed_below == 0.5


def test_gives_up_when_feeding_has_no_effect(real_frame, feed_cfg):
    """Якщо смужка не рухається після кількох спроб — не тиснемо клавішу всю ніч."""
    hungry = with_food(real_frame, 0.1, feed_cfg)
    pipe = PetFeedPipeline(feed_cfg)
    presses = 0
    for i in range(10):
        res = pipe.process(ctx_for(hungry, i * (feed_cfg.cooldown + 1)))
        presses += len(keys(res))
    assert presses == feed_cfg.give_up_after
    assert any("не піднімає ситість" in e for e in res.events) or pipe.gave_up


def test_resumes_when_bar_moves(real_frame, feed_cfg):
    hungry = with_food(real_frame, 0.1, feed_cfg)
    better = with_food(real_frame, 0.3, feed_cfg)
    pipe = PetFeedPipeline(feed_cfg)
    for i in range(feed_cfg.give_up_after):
        pipe.process(ctx_for(hungry, i * (feed_cfg.cooldown + 1)))
    assert pipe.gave_up
    t = 100 * feed_cfg.cooldown
    pipe.process(ctx_for(better, t))          # смужка зрушила — корм діє
    assert not pipe.gave_up
    res = pipe.process(ctx_for(hungry, t + feed_cfg.cooldown + 1))
    assert keys(res) == [feed_cfg.feed_key]


def test_slow_growth_is_not_mistaken_for_broken_food(real_frame, feed_cfg):
    """
    Одна порція додає менше пікселя, тому між сусідніми годуваннями смужка часто
    виглядає однаково. Бот має рахувати ріст від початку серії, а не від кроку до кроку.
    """
    pipe = PetFeedPipeline(feed_cfg)
    presses = 0
    for step in range(12):
        fraction = 0.02 + step * 0.004          # росте повільно, але постійно
        frame = with_food(real_frame, fraction, feed_cfg)
        presses += len(keys(pipe.process(ctx_for(frame, step * (feed_cfg.cooldown + 1)))))
    assert not pipe.gave_up, "повільний, але реальний ріст — не привід кидати годування"
    assert presses >= 10



def test_feeding_up_to_the_threshold_is_a_success(real_frame, feed_cfg):
    """
    Ситість коливається біля порогу: 49% -> годуємо -> 50% -> трохи спадає -> знову.
    Це нормальна робота, а не «корм не діє» (уночі бот саме так помилково здався).
    """
    pipe = PetFeedPipeline(feed_cfg)
    hungry = with_food(real_frame, 0.48)
    full = with_food(real_frame, 0.52)
    t = 0.0
    for _ in range(feed_cfg.give_up_after + 4):
        pipe.process(ctx_for(hungry, t))                  # нижче порога — годує
        t += 1
        pipe.process(ctx_for(full, t))                    # доїв до порога
        t += feed_cfg.cooldown + 1
    assert not pipe.gave_up, "годування працює, здаватись немає причини"
