"""
Вікно «Автопуть»: з'являється після подвійного кліку по точці в «Списку», коли
персонаж верхи на літаючому звірі.

У ньому повзунок «Высота» — на цій висоті гра сама й летить. Лишити як є не можна:
після воскресіння там висота землі (≈22), і автопуть упирається в стіну міста.
Клавішами висоту з фону не змінити, а повзунок тягнеться звичайним drag — перевірено.

Число над повзунком тонким шрифтом OCR читає через раз, тому висоту рахуємо за
положенням самого повзунка: 22 при x=675, 75 при x=776 (виміряно в грі).
Усе міряється від заголовка «Автопуть», бо вікно гравець може пересунути.
"""
from __future__ import annotations

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from app.core.geometry import Point, Region
from app.vision.template import TemplateSpec, find_template


class AutopathConfig(BaseModel):
    title: TemplateSpec = Field(
        default_factory=lambda: TemplateSpec(name="autopath_title.png", threshold=0.8,
                                             area=Region.of(300, 300, 840, 700)),
        title="Заголовок «Автопуть»", json_schema_extra={"tech": True})
    track_y: int = Field(default=43, title="Повзунок нижче заголовка на, px", json_schema_extra={"tech": True})
    track_from: int = Field(default=-80, title="Доріжка від, px", json_schema_extra={"tech": True})
    track_to: int = Field(default=105, title="Доріжка до, px", json_schema_extra={"tech": True})
    ref_dx: int = Field(default=-20, title="Повзунок при відомій висоті, px від заголовка",
                        json_schema_extra={"tech": True})
    ref_height: int = Field(default=22, title="Відома висота", json_schema_extra={"tech": True})
    per_px: float = Field(default=0.525, gt=0, title="Висоти на піксель повзунка",
                          json_schema_extra={"tech": True})


class AutopathReading(BaseModel):
    title: Point
    handle_x: int | None = None
    height: int | None = None



def read_autopath(image: Image.Image, cfg: AutopathConfig) -> AutopathReading | None:
    title = find_template(image, cfg.title)
    if title is None:
        return None
    handle = _handle(image, title, cfg)
    height = None if handle is None else round(
        cfg.ref_height + (handle - title.x - cfg.ref_dx) * cfg.per_px)
    return AutopathReading(title=title, handle_x=handle, height=height)


def handle_x_for(title: Point, height: int, cfg: AutopathConfig) -> int:
    """Куди поставити повзунок, щоб вийшла потрібна висота (у межах доріжки)."""
    x = title.x + cfg.ref_dx + (height - cfg.ref_height) / cfg.per_px
    return int(round(min(max(x, title.x + cfg.track_from + 8), title.x + cfg.track_to - 8)))


def _handle(image: Image.Image, title: Point, cfg: AutopathConfig) -> int | None:
    """Бірюзова «таблетка» на темній доріжці: її світла рамка над і під смужкою."""
    y = title.y + cfg.track_y
    x0, x1 = title.x + cfg.track_from, title.x + cfg.track_to
    band = np.asarray(image.crop((x0, y - 6, x1, y + 6)).convert("RGB")).astype(int)
    bright = (band[:, :, 1] > 120) & (band[:, :, 2] > 120)
    cols = np.where(bright.sum(axis=0) >= 2)[0]
    cols = cols[(cols > 3) & (cols < band.shape[1] - 3)]      # краї вікна не беремо
    if len(cols) == 0:
        return None
    return int(x0 + (cols.min() + cols.max()) / 2)
