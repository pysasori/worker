"""Тести пошуку лута на землі — на справжніх кадрах гри."""
from __future__ import annotations

from PIL import Image

from app.vision.ground import GroundCheckConfig, center_square, scan_ground


def read(frame: Image.Image, cfg: GroundCheckConfig | None = None):
    cfg = cfg or GroundCheckConfig()
    region = center_square(frame.size, cfg)
    return scan_ground(frame.crop(region.box), region, cfg)


def test_finds_item_label_on_ground(loot_frame):
    r = read(loot_frame)
    assert r.has_loot
    assert r.labels >= 1


def test_no_loot_after_empty_kill(no_loot_frame):
    assert not read(no_loot_frame).has_loot


def test_clean_frame_has_nothing(real_frame):
    assert not read(real_frame).has_loot


def test_square_is_centered_and_configurable(real_frame):
    cfg = GroundCheckConfig(size=300)
    region = center_square(real_frame.size, cfg)
    assert region.w == region.h == 300
    assert region.x + region.w // 2 == real_frame.width // 2
    assert region.y + region.h // 2 == real_frame.height // 2


def test_offset_moves_the_square(real_frame):
    region = center_square(real_frame.size, GroundCheckConfig(size=300, offset_y=-100))
    assert region.y + 150 == real_frame.height // 2 - 100


def test_square_never_leaves_the_frame(real_frame):
    region = center_square(real_frame.size, GroundCheckConfig(size=4000))
    assert region.x >= 0 and region.y >= 0
    assert region.x + region.w <= real_frame.width
    assert region.y + region.h <= real_frame.height


def test_own_nickname_is_excluded(real_frame):
    """Свій нік у центрі не має рахуватись за лут."""
    assert not read(real_frame, GroundCheckConfig(exclude=120)).has_loot


def test_higher_threshold_makes_it_stricter(loot_frame):
    assert not read(loot_frame, GroundCheckConfig(min_labels=5)).has_loot
