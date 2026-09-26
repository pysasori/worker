"""Тести розпізнавання смужок на справжньому кадрі гри."""
from __future__ import annotations

import pytest

from app.vision.bars import fixed_bar, scan_bar
from tests.conftest import set_bar_fill

TARGET_BAR_X0 = 605       # виміряно на кадрі 1440x1080
TARGET_BAR_WIDTH = 208
TARGET_ROWS = range(9, 17)
PET_BAR_X0 = 46
PET_BAR_WIDTH = 82
PET_ROWS = range(188, 194)


def test_target_bar_full(real_frame, search_cfg):
    r = scan_bar(real_frame.crop(search_cfg.region.box), search_cfg.region, search_cfg.bar)
    assert r.present
    assert r.x0 == TARGET_BAR_X0
    assert r.filled == TARGET_BAR_WIDTH


def test_target_bar_text_does_not_split_it(real_frame, search_cfg):
    """Білий напис '380/380' лежить поверх смужки; без обробки бар читався б удвічі коротшим."""
    r = scan_bar(real_frame.crop(search_cfg.region.box), search_cfg.region, search_cfg.bar)
    assert r.filled > TARGET_BAR_WIDTH * 0.9


@pytest.mark.parametrize("fraction,expected", [(1.0, 100), (0.5, 50), (0.25, 25)])
def test_target_bar_partial(real_frame, search_cfg, fraction, expected):
    frame = set_bar_fill(real_frame, TARGET_BAR_X0, TARGET_BAR_WIDTH, TARGET_ROWS, fraction)
    r = scan_bar(frame.crop(search_cfg.region.box), search_cfg.region, search_cfg.bar,
                 total=TARGET_BAR_WIDTH)
    assert r.present
    assert abs(r.percent - expected) <= 2


def test_target_bar_gone_when_dead(real_frame, search_cfg):
    """Клієнт PW не малює 0% — після смерті рамка зникає."""
    frame = set_bar_fill(real_frame, TARGET_BAR_X0, TARGET_BAR_WIDTH, TARGET_ROWS, 0.0)
    r = scan_bar(frame.crop(search_cfg.region.box), search_cfg.region, search_cfg.bar)
    assert not r.present


# ---- рамка пета -------------------------------------------------------------
def test_finds_pet_frame_by_look(real_frame):
    """Рамку пета можна пересунути мишею, тому шукаємо її за візерунком смужок."""
    from app.vision.pet_frame import read_bar
    from tests.conftest import pet_frame_of

    frame = pet_frame_of(real_frame)
    filled, total = read_bar(real_frame, frame, frame.hp_row)
    assert total >= 60
    # розмах на кілька пікселів довший за саму смужку через заокруглені краї рамки,
    # тому «повний піт» — це майже весь розмах, а не рівно він
    assert filled / total >= 0.85, "на цьому кадрі піт цілий"


@pytest.mark.parametrize("fraction,expected", [(1.0, 100), (0.5, 50), (0.25, 25), (0.0, 0)])
def test_reads_pet_hp_at_any_level(real_frame, fraction, expected):
    from tests.conftest import pet_frame_of

    from app.vision.pet_frame import find_pet_frame, read_bar
    from tests.conftest import pet_area, set_bar_fill

    frame = pet_frame_of(real_frame)
    hurt = set_bar_fill(real_frame, frame.x0, frame.width,
                        range(frame.hp_row - 3, frame.hp_row + 4), fraction)
    moved = find_pet_frame(hurt, None, pet_area())
    if fraction == 0.0:
        assert moved is None, "порожня смужка HP — рамку не знаходимо, це і є «пета нема»"
        return
    assert moved is not None, "поранений піт має знаходитись так само"
    filled, total = read_bar(hurt, moved, moved.hp_row)
    assert moved.exact, "край рамки має знаходитись"
    assert abs(round(filled * 100 / total) - expected) <= 3


def test_no_pet_frame_on_a_frame_without_pet():
    from PIL import Image

    from app.vision.pet_frame import find_pet_frame

    from tests.conftest import pet_area

    assert find_pet_frame(Image.new("RGB", (600, 400), (28, 48, 51)), None, pet_area()) is None


def test_player_frame_is_not_mistaken_for_pet(real_frame):
    """У рамки персонажа теж є червона і фіолетова смужки, але між ними синя мана."""
    from app.vision.pet_frame import find_pet_frame

    # зона навмисно захоплює обидві рамки: розрізнити їх має саме пошук
    frame = find_pet_frame(real_frame, None, (0, 0, 320, 320))
    assert frame is not None
    assert frame.hp_row > 100, "рамка персонажа вгорі, пет нижче"
