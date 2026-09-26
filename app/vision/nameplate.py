"""
Чи ворожа взята ціль — по кольору її назви в рамці цілі.

Клієнт PW фарбує назву за ставленням:
    моб (ворог)        — тепла жовта  ≈ (255, 221, 128), R помітно більше за B;
    свій піт, гравець  — холодна синя ≈ (150, 200, 255), B помітно більше за R.

Цього досить, щоб бот не молотив власного вовка: OCR не потрібен, порівнюємо
кількість «теплих» і «холодних» пікселів у смузі з назвою.
"""
from __future__ import annotations

from enum import Enum

from PIL import Image
from pydantic import BaseModel, Field

from app.core.geometry import Region


class TargetKind(str, Enum):
    HOSTILE = "ворог"
    FRIENDLY = "свій"
    UNKNOWN = "невідомо"


class HostileCheckConfig(BaseModel):
    """Смуга з назвою береться відносно знайденої смужки HP, тому не залежить від
    того, де саме гра намалювала рамку цілі."""

    enabled: bool = Field(default=True, title="Перевіряти, що це моб")
    dy_from: int = Field(default=12, title="Назва: від рядка смужки +", json_schema_extra={"tech": True})
    dy_to: int = Field(json_schema_extra={"tech": True}, default=28, title="Назва: до рядка смужки +")
    min_value: int = Field(json_schema_extra={"tech": True}, default=170, ge=0, le=255, title="Яскравість тексту")
    min_diff: int = Field(json_schema_extra={"tech": True}, default=40, ge=1, title="Різниця R і B",
                          description="менша різниця = колір невиразний, не рахуємо")
    min_pixels: int = Field(json_schema_extra={"tech": True}, default=25, ge=1, title="Пікселів для висновку")


def name_region(bar_x0: int, bar_width: int, bar_row: int, cfg: HostileCheckConfig) -> Region:
    """Смуга з назвою цілі: одразу під смужкою HP, тієї ж ширини."""
    return Region.of(bar_x0, max(0, bar_row + cfg.dy_from), max(1, bar_width),
                     max(1, cfg.dy_to - cfg.dy_from))


def classify_target(crop: Image.Image, cfg: HostileCheckConfig) -> tuple[TargetKind, int, int]:
    """crop — вирізка смуги з назвою. Повертає (хто це, теплих px, холодних px)."""
    px = crop.load()
    w, h = crop.size
    warm = cool = 0
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y][:3]
            if max(r, g, b) < cfg.min_value:
                continue
            if r - b >= cfg.min_diff:
                warm += 1
            elif b - r >= cfg.min_diff:
                cool += 1
    if max(warm, cool) < cfg.min_pixels:
        return TargetKind.UNKNOWN, warm, cool
    return (TargetKind.HOSTILE if warm >= cool else TargetKind.FRIENDLY), warm, cool
