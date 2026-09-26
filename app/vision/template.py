"""
Пошук елементів інтерфейсу за зразком (шаблоном).

Вікна гри — «Лавка», рюкзак, діалоги — відкриваються там, де їх востаннє лишили
мишею, тому прив'язка кнопок до координат раз по раз ламалась: бот клікав у порожнє
місце і ремонт «не працював». Кнопку шукаємо за її виглядом: маленький знімок
кнопки лежить у assets/templates, а на кадрі знаходиться найсхожіше місце.

Шаблони маленькі (близько 100x25), тому пошук по всьому кадру займає кілька
мілісекунд і робиться лише на кроках ремонту, а не щотіка.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from app.core.geometry import Point, Region

TEMPLATES = Path(__file__).resolve().parents[2] / "assets" / "templates"


class TemplateSpec(BaseModel):
    """Який зразок шукати і наскільки точно він має збігтись."""

    name: str = Field(title="Файл зразка", description="ім'я файлу з assets/templates")
    threshold: float = Field(default=0.8, ge=0.1, le=1.0, title="Поріг схожості",
                             description="1.0 — піксель у піксель; нижче 0.7 бувають хибні збіги")
    area: Region | None = Field(default=None, title="Де шукати",
                                description="порожнє — по всьому кадру")


@lru_cache(maxsize=32)
def _load(name: str) -> np.ndarray | None:
    path = TEMPLATES / name
    if not path.exists():
        return None
    data = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    return data


def match(image: Image.Image, spec: TemplateSpec) -> tuple[Point | None, float]:
    """Центр знайденого зразка і оцінка схожості (для логів і калібрування)."""
    tpl = _load(spec.name)
    if tpl is None:
        return None, 0.0
    off_x, off_y = 0, 0
    if spec.area:
        off_x, off_y = spec.area.x, spec.area.y
        image = image.crop(spec.area.box)
    if image.width < tpl.shape[1] or image.height < tpl.shape[0]:
        return None, 0.0
    haystack = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    res = cv2.matchTemplate(haystack, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < spec.threshold:
        return None, float(score)
    h, w = tpl.shape[:2]
    return Point(x=loc[0] + off_x + w // 2, y=loc[1] + off_y + h // 2), float(score)


def find_template(image: Image.Image, spec: TemplateSpec) -> Point | None:
    return match(image, spec)[0]


def template_present(image: Image.Image, spec: TemplateSpec) -> bool:
    return match(image, spec)[0] is not None
