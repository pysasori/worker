"""
Спільна дошка між пайплайнами одного вікна.

Пайплайни не викликають один одного і не знають один про одного — вони лише
кладуть і читають типізовані записи тут. Так пошук цілі може віддавати стан,
а атака й лут його споживати, лишаючись незалежними і вимкненними по одному.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.vision.schemas import BarReading

SHARED_TARGET = "target"
SHARED_PET = "pet"
SHARED_PET_FOOD = "pet_food"
SHARED_PET_FRAME = "pet_frame"
SHARED_BUSY = "busy"
SHARED_POS = "pos"          # координати персонажа з панелі вгорі
SHARED_ALT = "alt"          # висота (третє число там само)
SHARED_GROUND = "ground"  # висота землі на місці фарму (int або None), її вчить повернення на місце
SHARED_HOME = "home"        # місце, куди повертатись (Position або None)
SHARED_PLAYER = "player"    # HP персонажа (BarReading)
SHARED_MOUNT = "mount"      # чи сидимо верхи: True/False, None — не знаємо
SHARED_COMBAT_READY = "combat_ready"  # стартову позицію перевірено, можна шукати й бити


class TargetInfo(BaseModel):
    """Що пайплайн пошуку знає про ціль. Оновлюється кожен кадр."""

    present: bool = False                    # ціль є і підтверджена
    bar: BarReading = Field(default_factory=BarReading)
    acquired_at: float | None = None         # коли з'явилась ПОТОЧНА ціль
    died_at: float | None = None             # коли зникла остання ціль
    died_now: bool = False                   # ціль зникла саме на цьому кадрі
    updated_at: float = 0.0
    updated_tick: int = 0

    @property
    def hp(self) -> float:
        return self.bar.ratio

    def __str__(self) -> str:
        if self.died_now:
            return "ціль мертва"
        return f"ціль {self.bar}" if self.present else "цілі нема"


class Position(BaseModel):
    """Де стоїть персонаж. Читається з панелі локації, а не вгадується."""

    x: int = 0
    y: int = 0
    known: bool = False               # False = прочитати не вдалось жодного разу
    at: float = 0.0                   # коли востаннє прочитали

    def distance_to(self, other: "Position") -> float:
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5

    def __str__(self) -> str:
        return f"{self.x}, {self.y}" if self.known else "координат нема"


class Altitude(BaseModel):
    """Висота персонажа: по ній видно політ, воду і падіння в яму."""

    z: int = 0
    known: bool = False
    at: float = 0.0

    def __str__(self) -> str:
        return f"висота {self.z}" if self.known else "висоти нема"


def read_player(shared: dict[str, Any]) -> BarReading | None:
    """HP персонажа. None = блок «Відхіл персонажа» вимкнений."""
    return shared.get(SHARED_PLAYER)


def read_position(shared: dict[str, Any]) -> Position | None:
    return shared.get(SHARED_POS)


def read_altitude(shared: dict[str, Any]) -> Altitude | None:
    return shared.get(SHARED_ALT)


def altitude_above_ground(shared: dict[str, Any]) -> float | None:
    """
    На скільки персонаж вище за землю на місці фарму. None — не знаємо (нема висоти або
    землі). За цим видно, що він у повітрі, коли «верхи» не відомо: клавіша польоту
    перемикач, і натиснута на землі вона САДИТЬ НА ЗВІРА.
    """
    alt, ground = read_altitude(shared), shared.get(SHARED_GROUND)
    if alt is None or not alt.known or ground is None:
        return None
    return alt.z - ground


def read_target(shared: dict[str, Any]) -> TargetInfo | None:
    """None означає, що пайплайн пошуку вимкнений або ще не відпрацював."""
    return shared.get(SHARED_TARGET)


def write_target(shared: dict[str, Any], info: TargetInfo) -> None:
    shared[SHARED_TARGET] = info


def set_busy(shared: dict[str, Any], who: str, busy: bool) -> None:
    """Позначити, що пайплайн зайнятий довгою дією (наприклад збирає лут)."""
    holders: set[str] = shared.setdefault(SHARED_BUSY, set())
    holders.add(who) if busy else holders.discard(who)


def busy_reasons(shared: dict[str, Any]) -> set[str]:
    """Хто саме зараз зайнятий. Порожньо = можна робити свої справи."""
    return set(shared.get(SHARED_BUSY, ()))


def set_combat_ready(shared: dict[str, Any], ready: bool) -> None:
    """Відкрити/закрити бойовий шлюз. Якщо блока навігації нема, шлюз вважається відкритим."""
    shared[SHARED_COMBAT_READY] = ready


def combat_ready(shared: dict[str, Any]) -> bool:
    return bool(shared.get(SHARED_COMBAT_READY, True))


def set_mounted(shared: dict[str, Any], mounted: bool | None) -> None:
    """
    Запам'ятати, верхи персонаж чи ні. Клавіша польоту — ПЕРЕМИКАЧ, а в грі нема
    жодної позначки на екрані (перевірено: кадр верхи й пішки різняться тільки самим
    персонажем), тому стан можна лише вести самому. None — «не знаємо»: так на старті
    й після смерті, бо гра сама знімає зі звіра.
    """
    shared[SHARED_MOUNT] = mounted


def is_mounted(shared: dict[str, Any]) -> bool | None:
    return shared.get(SHARED_MOUNT)
