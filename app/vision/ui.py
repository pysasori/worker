"""
Маркери інтерфейсу: "чи відкрите зараз це вікно гри".

Замість того щоб клікати наосліп і сподіватись, пайплайн перевіряє за кольором
у маленькій зоні, що потрібне вікно справді на екрані. Один узагальнений предикат
(діапазон RGB + скільки таких пікселів) покриває і білий текст кнопок, і золоті іконки.

Виміряно на клієнті 1440x1080:
  рюкзак відкритий      — золота іконка «Лавка» в нижньому ряду: 179 px проти 0;
  «Лавка» відкрита      — світлий текст кнопки «Починить все»: 139 px проти 0;
  діалог підтвердження  — білий текст кнопки «Да (Y)»: 56 px проти 0.
"""
from __future__ import annotations

from PIL import Image
from pydantic import BaseModel, Field

from app.core.geometry import Region


class UiMarker(BaseModel):
    """Зона екрана + діапазон кольору, за яким видно, що елемент на місці."""

    region: Region = Field(json_schema_extra={"tech": True}, title="Зона")
    min_rgb: tuple[int, int, int] = Field(json_schema_extra={"tech": True}, default=(140, 140, 140), title="Колір від")
    max_rgb: tuple[int, int, int] = Field(json_schema_extra={"tech": True}, default=(255, 255, 255), title="Колір до")
    min_pixels: int = Field(json_schema_extra={"tech": True}, default=40, ge=1, title="Пікселів для збігу")


def count_pixels(crop: Image.Image, marker: UiMarker) -> int:
    px = crop.load()
    w, h = crop.size
    lo, hi = marker.min_rgb, marker.max_rgb
    found = 0
    for y in range(h):
        for x in range(w):
            p = px[x, y]
            if (lo[0] <= p[0] <= hi[0] and lo[1] <= p[1] <= hi[1] and lo[2] <= p[2] <= hi[2]):
                found += 1
                if found >= marker.min_pixels:
                    return found
    return found


def marker_present(image: Image.Image, marker: UiMarker) -> bool:
    """image — повний кадр вікна."""
    return count_pixels(image.crop(marker.region.box), marker) >= marker.min_pixels
