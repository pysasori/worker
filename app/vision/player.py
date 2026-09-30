"""
Смужка HP персонажа (ліворуч угорі).

Вона не плаває по екрану, як рамка цілі, тому шукати її не треба — досить прочитати
довжину червоного. Пастка та сама, що й у цілі: поверх смужки написано «769/777»,
і білий текст рве червоний відрізок. Тому міряємо до ОСТАННЬОГО червоного пікселя,
дозволяючи розриви.

Повна довжина смужки стала (виміряно на кількох кадрах: 137 px від x=103), тому
відсоток рахується від неї, а не від знайденого відрізка.
"""
from __future__ import annotations

from PIL import Image
from pydantic import BaseModel, Field

from app.core.geometry import Region
from app.vision.colors import is_red
from app.vision.schemas import BarReading


class PlayerBarConfig(BaseModel):
    """Де смужка HP персонажа і яка вона завдовжки."""

    region: Region = Field(default=Region.of(100, 28, 150, 12), title="Зона смужки HP",
                           json_schema_extra={"tech": True})
    width: int = Field(default=137, gt=0, title="Повна довжина смужки, px",
                       json_schema_extra={"tech": True})
    gap: int = Field(default=14, ge=0, title="Розрив від тексту, px", json_schema_extra={"tech": True},
                     description="напис «769/777» лежить поверх смужки і рве червоне")
    bright_min: int = Field(default=180, title="Поріг тексту", json_schema_extra={"tech": True})


def read_player_hp(image: Image.Image, cfg: PlayerBarConfig | None = None) -> BarReading:
    """Скільки життя лишилось у персонажа. present=False — смужки не видно взагалі."""
    cfg = cfg or PlayerBarConfig()
    crop = image.crop(cfg.region.box)
    px = crop.load()
    w, h = crop.size

    # Зона щільно охоплює лише HP-смужку. Білий напис на кшталт «924/925» може
    # розірвати червоне на 20–30 px: старий пошук зупинявся всередині цифр і
    # читав повну смужку як 44%. Беремо крайні червоні пікселі по всій висоті;
    # напис між ними не змінює справжній правий край заповнення.
    first, last = w, -1
    row = 0
    for y in range(h):
        for x in range(w):
            if is_red(px[x, y]):
                if x < first:
                    first = x
                if x > last:
                    row = y
                    last = x
    if last < first:
        return BarReading(present=False, total=cfg.width)
    filled = min(last - first + 1, cfg.width)
    return BarReading(present=True, filled=filled, total=cfg.width,
                      x0=cfg.region.x + first, row=cfg.region.y + row)
