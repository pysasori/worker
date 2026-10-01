"""
Читання чисел з інтерфейсу: координати персонажа і висота.

Панель угорі праворуч показує «Поселок у моста / 241, 563  21»: назва локації і
координати — білим, висота — світло-зеленим. Кольором вони й розділяються, тому
кожне число читається окремо і чуже в нього не потрапляє.

Перед OCR лишаємо самі цифри: маска кольору -> обрізка по тексту -> чорне на білому.
Так tesseract бачить чистий рядок і не вигадує літер із фону.
"""
from __future__ import annotations

import re

from PIL import Image, ImageChops, ImageOps
from pydantic import BaseModel, Field

from app.core.geometry import Region
from app.vision.text import OcrConfig, read_line

_NUM = re.compile(r"\d+")


class DigitsConfig(BaseModel):
    """Де і якого кольору шукати числа."""

    region: Region = Field(default=Region.of(1228, 22, 78, 14), title="Зона з числом",
                           json_schema_extra={"tech": True},
                           description="між іконкою сонця і мінімапою: біла рамка мінімапи "
                                       "праворуч інакше читалась як зайва цифра («5493»)")
    color: str = Field(default="white", title="Колір тексту", json_schema_extra={"tech": True},
                       description="white — координати, green — висота")
    pad: int = Field(default=3, ge=0, title="Поля навколо тексту", json_schema_extra={"tech": True})
    scale: int = Field(default=6, ge=2, le=10, title="Збільшення перед читанням",
                       json_schema_extra={"tech": True})


def _gt(t: int):
    return lambda v: 255 if v > t else 0


def _mask(crop: Image.Image, color: str) -> Image.Image:
    r, g, b = crop.split()
    if color == "green":
        # висота світло-зелена (≈187,217,148): зеленого більше, ніж синього
        out = ImageChops.multiply(g.point(_gt(140)), ImageChops.subtract(g, b).point(_gt(40)))
        return ImageChops.multiply(out, ImageChops.subtract(g, r).point(_gt(12)))
    out = ImageChops.multiply(r.point(_gt(195)), g.point(_gt(195)))
    return ImageChops.multiply(out, b.point(_gt(195)))


def read_numbers(image: Image.Image, cfg: DigitsConfig, ocr: OcrConfig) -> list[int]:
    """Усі числа з зони заданим кольором, зліва направо. Порожньо — якщо не прочиталось."""
    crop = image.crop(cfg.region.box)
    mask = _mask(crop, cfg.color)
    box = mask.getbbox()
    if box is None:
        return []
    box = (max(0, box[0] - cfg.pad), max(0, box[1] - cfg.pad),
           min(crop.width, box[2] + cfg.pad), min(crop.height, box[3] + cfg.pad))
    base = ImageOps.invert(mask.crop(box))
    # Tesseract на такому дрібному тексті хитається («586» -> «566», кома губиться або
    # зливає числа в «244586»). Тому читаємо кількома способами й беремо збіг двох.
    votes: dict[tuple[int, ...], int] = {}
    first: tuple[int, ...] | None = None
    for lang, scale, whitelist in _VARIANTS:
        piece = base.resize((base.width * scale, base.height * scale), Image.LANCZOS)
        extra = "-c tessedit_char_whitelist=0123456789," if whitelist else ""
        text = read_line(piece, ocr.model_copy(update={"psm": 7, "scale": 1, "lang": lang, "extra": extra}))
        pair = _numbers(text, split=cfg.color == "white")
        if pair is None:
            continue
        first = first or pair
        votes[pair] = votes.get(pair, 0) + 1
        if votes[pair] >= 2:
            return list(pair)
    if not votes:
        return []
    best = max(votes, key=lambda k: (votes[k], k == first))
    return list(best)


# (мова, збільшення, лише цифри) — від найточнішого до запасних
_VARIANTS = (("eng", 8, False), ("eng", 8, True), ("rus", 4, False), ("eng", 6, False), ("eng", 4, True))


def _numbers(text: str, split: bool) -> tuple[int, ...] | None:
    """Числа з рядка. Для координат без коми число зливається («244586») — ділимо навпіл."""
    nums = _NUM.findall(text)
    if split and len(nums) == 1 and len(nums[0]) == 6:
        return int(nums[0][:3]), int(nums[0][3:])
    return tuple(int(n) for n in nums) or None
