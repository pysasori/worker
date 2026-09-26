"""
Пошук іконки інтерфейсу біля заданої точки.

Навіщо: вікна гри можна пересувати мишею. Одного разу рюкзак з'їхав на 21 піксель,
і клік «відкрити Лавку» потрапив у сусідню іконку «Бросить монеты» — саме те, чого
бот робити не має. Тому перед кліком іконку треба ЗНАЙТИ, а не вірити координатам.

Як шукаємо: ковзаємо невеликим вікном навколо заданої точки і рахуємо два кольори —
вміст іконки (золоті монети) і обідок (бірюзове кільце). Обидва пороги потрібні
разом: у сусідньої «Бросить монеты» теж золото, але кільця нема.
"""
from __future__ import annotations

from PIL import Image, ImageChops
from pydantic import BaseModel, Field

from app.core.geometry import Point


class ColorCount(BaseModel):
    min_rgb: tuple[int, int, int]
    max_rgb: tuple[int, int, int]
    min_pixels: int = Field(default=1, ge=0)


class IconSearchConfig(BaseModel):
    enabled: bool = Field(default=True, title="Шукати іконку перед кліком")
    radius: int = Field(json_schema_extra={"tech": True}, default=45, ge=0, title="Радіус пошуку, px")
    step: int = Field(json_schema_extra={"tech": True}, default=3, ge=1, title="Крок пошуку, px")
    box: int = Field(json_schema_extra={"tech": True}, default=25, ge=4, title="Вікно вмісту, px")
    ring_box: int = Field(json_schema_extra={"tech": True}, default=33, ge=4, title="Вікно обідка, px")
    fill: ColorCount = Field(json_schema_extra={"tech": True}, title="Колір вмісту іконки")
    ring: ColorCount | None = Field(json_schema_extra={"tech": True}, default=None, title="Колір обідка")


def _count(px, x0: int, y0: int, size: int, w: int, h: int, c: ColorCount) -> int:
    lo, hi = c.min_rgb, c.max_rgb
    half = size // 2
    n = 0
    for y in range(max(0, y0 - half), min(h, y0 + half + 1)):
        for x in range(max(0, x0 - half), min(w, x0 + half + 1)):
            p = px[x, y]
            if lo[0] <= p[0] <= hi[0] and lo[1] <= p[1] <= hi[1] and lo[2] <= p[2] <= hi[2]:
                n += 1
    return n


def find_icon(image: Image.Image, center: Point, cfg: IconSearchConfig) -> Point | None:
    """Центр знайденої іконки або None, якщо поруч її нема."""
    px = image.load()
    w, h = image.size
    best: tuple[int, Point] | None = None
    for dy in range(-cfg.radius, cfg.radius + 1, cfg.step):
        for dx in range(-cfg.radius, cfg.radius + 1, cfg.step):
            x, y = center.x + dx, center.y + dy
            if not (0 <= x < w and 0 <= y < h):
                continue
            fill = _count(px, x, y, cfg.box, w, h, cfg.fill)
            if fill < cfg.fill.min_pixels:
                continue
            ring = _count(px, x, y, cfg.ring_box, w, h, cfg.ring) if cfg.ring else 0
            if cfg.ring and ring < cfg.ring.min_pixels:
                continue
            score = fill + ring
            if best is None or score > best[0]:
                best = (score, Point(x=x, y=y))
    return best[1] if best else None


# ---- швидкий пошук по всьому екрану -----------------------------------------
def _mask(image: Image.Image, c: ColorCount) -> bytes:
    """Маску кольору рахує PIL (це C): по пікселях у Python було б надто повільно."""
    r, g, b = image.split()
    lo, hi = c.min_rgb, c.max_rgb
    parts = [r.point(lambda v, a=lo[0], z=hi[0]: 255 if a <= v <= z else 0),
             g.point(lambda v, a=lo[1], z=hi[1]: 255 if a <= v <= z else 0),
             b.point(lambda v, a=lo[2], z=hi[2]: 255 if a <= v <= z else 0)]
    out = parts[0]
    for part in parts[1:]:
        out = ImageChops.multiply(out, part)
    return out.tobytes()


def _blobs(mask: bytes, w: int, h: int, step: int = 2, merge: int = 12) -> list[tuple[int, int, int, int, int]]:
    """Скупчення увімкнених пікселів: (центр x, центр y, скільки, ширина, висота)."""
    found: list[list[int]] = []          # [sum_x, sum_y, count, min_x, max_x, min_y, max_y]
    for y in range(0, h, step):
        row = mask[y * w:(y + 1) * w]
        x = row.find(255)
        while x >= 0:
            for blob in found:
                if blob[3] - merge <= x <= blob[4] + merge and blob[5] - merge <= y <= blob[6] + merge:
                    blob[0] += x; blob[1] += y; blob[2] += 1
                    blob[3] = min(blob[3], x); blob[4] = max(blob[4], x)
                    blob[5] = min(blob[5], y); blob[6] = max(blob[6], y)
                    break
            else:
                found.append([x, y, 1, x, x, y, y])
            x = row.find(255, x + step)
    return [(b[0] // b[2], b[1] // b[2], b[2], b[4] - b[3] + 1, b[6] - b[5] + 1) for b in found]


def changed_area(before: Image.Image, after: Image.Image, threshold: int = 45,
                 tile: int = 32, min_tiles: int = 4) -> tuple[int, int, int, int] | None:
    """
    Прямокутник, у якому екран помітно змінився. Використовуємо після натискання
    клавіші рюкзака: вікно гри можна пересунути куди завгодно, але саме воно й дає
    найбільшу зміну кадру — так пошук іконки звужується з усього екрана до вікна.
    """
    diff = ImageChops.difference(before.convert("L"), after.convert("L")).point(
        lambda v: 255 if v > threshold else 0).tobytes()
    w, h = before.size
    tiles = []
    for ty in range(0, h - tile + 1, tile):
        for tx in range(0, w - tile + 1, tile):
            n = sum(1 for y in range(ty, ty + tile, 4) for x in range(tx, tx + tile, 4)
                    if diff[y * w + x])
            if n > (tile // 4) ** 2 * 0.35:
                tiles.append((tx, ty))
    if len(tiles) < min_tiles:
        return None
    xs = [t[0] for t in tiles]
    ys = [t[1] for t in tiles]
    return min(xs), min(ys), min(w, max(xs) + tile), min(h, max(ys) + tile)


def find_icon_anywhere(image: Image.Image, cfg: IconSearchConfig,
                       area: tuple[int, int, int, int] | None = None) -> Point | None:
    """
    Іконка будь-де на екрані. Потрібно, бо вікна гри пересувають, і прив'язка
    навіть до приблизної точки колись привела до кліку в сусідню іконку.
    """
    offset_x, offset_y = 0, 0
    if area:
        offset_x, offset_y = area[0], area[1]
        image = image.crop(area)
    w, h = image.size
    gold = _mask(image, cfg.fill)
    ring = _mask(image, cfg.ring) if cfg.ring else None
    best: tuple[int, Point] | None = None
    px = image.load()
    for cx, cy, size, bw, bh in _blobs(gold, w, h):
        # іконка компактна, а не витягнута смуга золота в інтерфейсі
        if not (6 <= bw <= cfg.ring_box + 10 and 5 <= bh <= cfg.ring_box + 10):
            continue
        fill = _count(px, cx, cy, cfg.box, w, h, cfg.fill)
        if fill < cfg.fill.min_pixels:
            continue
        rings = _count(px, cx, cy, cfg.ring_box, w, h, cfg.ring) if cfg.ring else 0
        if cfg.ring and rings < cfg.ring.min_pixels:
            continue
        score = fill + rings
        if best is None or score > best[0]:
            best = (score, Point(x=cx + offset_x, y=cy + offset_y))
    return best[1] if best else None
