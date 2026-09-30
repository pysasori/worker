"""Фікстури: справжній кадр гри 1440x1080 з живою ціллю і живим пітом."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures"
FRAME = FIXTURES / "frame_1440_target_and_pet.png"


@pytest.fixture(scope="session")
def real_frame() -> Image.Image:
    """Жива ціль (380/380), живий піт, на землі нічого."""
    return Image.open(FRAME).convert("RGB")


@pytest.fixture(scope="session")
def loot_frame() -> Image.Image:
    """Той самий кадр, але в центральному квадраті лежить предмет з білим підписом."""
    return Image.open(FIXTURES / "frame_1440_ground_loot.png").convert("RGB")


@pytest.fixture(scope="session")
def no_loot_frame() -> Image.Image:
    """Справжній кадр одразу після смерті моба, який нічого не випустив."""
    return Image.open(FIXTURES / "frame_1440_no_loot.png").convert("RGB")


@pytest.fixture(scope="session")
def bot_config_raw() -> dict:
    from app.config.loader import _strip_comments

    return _strip_comments(json.loads((ROOT / "config" / "windows.json").read_text(encoding="utf-8")))


def _spec(bot_config_raw: dict, type_name: str) -> dict:
    profile = next(iter(bot_config_raw["profiles"].values()))
    return next(p for p in profile["pipelines"]
                if p["type"] == type_name)["config"]


@pytest.fixture(scope="session")
def search_cfg(bot_config_raw):
    """
    Живий конфіг, але фільтр мобів вимкнений: він залежить від того, кого зараз
    обрано в конструкторі, а тести мають бути стабільні. Сам фільтр перевіряє
    tests/test_names.py — він вмикає його явно.
    """
    from app.pipelines.target_search import TargetSearchConfig

    data = dict(_spec(bot_config_raw, "target_search"))
    data["names"] = {**data.get("names", {}), "enabled": False, "allow": [], "deny": []}
    return TargetSearchConfig(**data)


@pytest.fixture(scope="session")
def attack_cfg(bot_config_raw):
    from app.pipelines.attack import AttackConfig

    return AttackConfig(**_spec(bot_config_raw, "attack"))


@pytest.fixture(scope="session")
def loot_cfg(bot_config_raw):
    from app.pipelines.loot import LootConfig

    return LootConfig(**_spec(bot_config_raw, "loot"))


@pytest.fixture(scope="session")
def pet_cfg(bot_config_raw):
    from app.pipelines.pet import PetHealConfig

    return PetHealConfig(**_spec(bot_config_raw, "pet_heal"))


def set_bar_fill(image: Image.Image, x0: int, width: int, rows: range,
                 fraction: float, empty=(29, 54, 59)) -> Image.Image:
    # (29, 54, 59) — справжній колір порожньої частини смужки в грі: по ньому бот
    # бачить рамку цілі навіть при HP 1%, тому синтетичні кадри мають бути такими ж
    """Затерти червоне праворуч від fraction — імітація втрати HP на справжньому кадрі."""
    out = image.copy()
    px = out.load()
    cut = x0 + int(width * fraction)
    for y in rows:
        for x in range(image.width):
            p = px[x, y]
            if p[0] > 150 and p[1] < 100 and p[2] < 100 and x >= cut:
                px[x, y] = empty
    return out


# ---- помічники для пета: рамка шукається за виглядом, тому й у тестах теж -----
GOLD = (230, 172, 70)
EMPTY = (26, 46, 49)


def pet_area() -> tuple[int, int, int, int]:
    """Зона пошуку рамки пета — та сама, що в бота: по всьому екрану шукати не можна."""
    from app.pipelines.pet import PetHealConfig

    return PetHealConfig().search.box


def pet_frame_of(image: Image.Image):
    from app.vision.pet_frame import find_pet_frame

    frame = find_pet_frame(image, None, pet_area())
    assert frame is not None, "на цьому кадрі має бути рамка пета"
    return frame


def set_pet_hp(image: Image.Image, fraction: float) -> Image.Image:
    """Намалювати смужку HP пета заданої довжини."""
    frame = pet_frame_of(image)
    return set_bar_fill(image, frame.x0, frame.width,
                        range(frame.hp_row - 3, frame.hp_row + 4), fraction)


def set_pet_food(image: Image.Image, fraction: float) -> Image.Image:
    """Намалювати смужку ситості заданої довжини."""
    from app.vision.pet_frame import _is_empty, _is_gold

    frame = pet_frame_of(image)
    out = image.copy()
    px = out.load()
    cut = frame.x0 + int(frame.width * fraction)
    # перемальовуємо всю смугу між HP і досвідом: смужка ситості заввишки
    # кілька рядків, і якщо лишити хоч один зі старим золотом, порожня смужка
    # читається як чверть повної.
    # Червону і фіолетову смужки не чіпаємо — по них знаходиться сама рамка.
    for y in range(frame.hp_row + 2, frame.purple_row + 4):
        for x in range(frame.x0, frame.x0 + frame.width):
            if _is_gold(px[x, y]) or _is_empty(px[x, y]):
                px[x, y] = GOLD if x < cut else EMPTY
    return out


def shared_with_pet(image: Image.Image) -> dict:
    """Спільна дошка з геометрією рамки пета — її зазвичай кладе блок лікування."""
    from app.pipelines.shared import SHARED_PET_FRAME

    return {SHARED_PET_FRAME: pet_frame_of(image)}
