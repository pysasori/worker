"""
Пошук рамки пета на екрані за виглядом, без прив'язки до координат.

Рамку пета можна пересунути мишею куди завгодно, тому шукаємо її за візерунком:
    червона смужка HP
    під нею смужка ситості (золота або темна порожня)
    ще нижче фіолетова смужка
Той самий візерунок майже має рамка персонажа, але в неї посередині СИНЯ мана —
саме цим вони й розрізняються.

Знайдену геометрію кешує пайплайн, тому повний пошук іде лише коли рамка загубилась.
"""
from __future__ import annotations

from PIL import Image, ImageChops
from pydantic import BaseModel, Field


class PetFrameConfig(BaseModel):
    """Пороги пошуку. Технічне: у звичайній роботі чіпати не треба."""

    min_width: int = Field(default=45, title="Мінімальна ширина смужки, px",
                           json_schema_extra={"tech": True})
    max_width: int = Field(default=140, title="Максимальна ширина смужки, px",
                           json_schema_extra={"tech": True})
    purple_below: tuple[int, int] = Field(default=(8, 26), title="Фіолетова нижче на, рядків",
                                          json_schema_extra={"tech": True})
    x_tolerance: int = Field(default=8, title="Допуск по X", json_schema_extra={"tech": True})
    min_red: int = Field(default=4, title="Мінімум червоного, px", json_schema_extra={"tech": True},
                         description="скільки заповненої смужки видно навіть у пораненого пета")
    gap: int = Field(default=2, title="Розрив у смужці, px", json_schema_extra={"tech": True})


class PetFrame(BaseModel):
    x0: int
    width: int
    hp_row: int
    food_row: int
    purple_row: int
    exact: bool = False      # ширина взята з краю рамки, а не оцінена по темному фону


# Правий край смужок пета — бірюзовий відблиск рамки. Від нього ліворуч 3 пікселі
# темного обідка, тож заповнювана частина закінчується за 3 пікселі до відблиску.
# Виміряно на кількох кадрах: відблиск рівно за 85 px від початку, повна смужка — 82 px.
_BEVEL_INSET = 3


def _is_bevel(p) -> bool:
    return p[1] > 85 and p[2] > 85 and p[1] - p[0] > 30 and p[0] < 100


def _bar_end(image: Image.Image, x0: int, hp_row: int, cfg: "PetFrameConfig") -> int | None:
    """
    Ширина заповнюваної частини смужки за краєм рамки. Раніше ширину рахували по
    «темному», і в неї потрапляв темний фон за рамкою: 102 px замість 82, через що
    повний піт показувався як 89%, а ситість занижувалась.
    """
    px = image.load()
    w, h = image.size
    found: dict[int, int] = {}
    for y in range(max(0, hp_row - 1), min(h, hp_row + 11)):
        for x in range(x0 + cfg.min_width, min(w, x0 + cfg.max_width + _BEVEL_INSET + 2)):
            if _is_bevel(px[x, y]):
                found[x] = found.get(x, 0) + 1
                break
    if not found:
        return None
    edge, votes = max(found.items(), key=lambda kv: kv[1])
    return edge - x0 - _BEVEL_INSET if votes >= 3 else None


def _is_red(p) -> bool:
    return p[0] > 150 and p[1] < 100 and p[2] < 100 and p[0] - p[1] >= 70


def _is_purple(p) -> bool:
    return p[2] > 140 and p[2] - p[1] > 60 and 60 < p[0] < 230


def _is_empty(p) -> bool:
    """Порожня частина смужки: темна."""
    return p[0] < 95 and p[1] < 120 and p[2] < 130


def _is_gold(p) -> bool:
    """Заповнена ситість: золота."""
    return p[0] > 150 and p[1] > 110 and p[2] < 130 and p[0] - p[2] > 60


def _is_blue(p) -> bool:
    """Синя мана. Фіолетовий досвід під це не підпадає: у нього багато червоного."""
    return p[2] > 120 and p[2] - p[0] > 50 and p[2] - p[1] > 30 and not _is_purple(p)


def _gt(t: int):
    return lambda v: 255 if v > t else 0


def _lt(t: int):
    return lambda v: 255 if v < t else 0


def _all(*masks: Image.Image) -> Image.Image:
    out = masks[0]
    for m in masks[1:]:
        out = ImageChops.multiply(out, m)
    return out


def _color_masks(image: Image.Image) -> dict[str, bytes]:
    """
    Маски кольорів рахує PIL (це C, мілісекунди), а не Python по пікселях.
    Без цього повний пошук рамки займав понад 300 мс і просаджував увесь цикл.
    """
    r, g, b = image.split()
    rg = ImageChops.subtract(r, g)
    bg = ImageChops.subtract(b, g)
    br = ImageChops.subtract(b, r)
    red = _all(r.point(_gt(150)), g.point(_lt(100)), b.point(_lt(100)), rg.point(_gt(69)))
    purple = _all(b.point(_gt(140)), bg.point(_gt(60)), r.point(_gt(60)), r.point(_lt(230)))
    empty = _all(r.point(_lt(95)), g.point(_lt(120)), b.point(_lt(130)))
    blue = _all(b.point(_gt(120)), br.point(_gt(50)), bg.point(_gt(30)))
    # повна смужка досвіду теж «синя» на око маски, і рамка пета через це
    # відкидалась як рамка персонажа. Мана — це синє БЕЗ фіолетового.
    blue = ImageChops.subtract(blue, purple)
    gold = _all(r.point(_gt(150)), g.point(_gt(110)), b.point(_lt(130)),
                ImageChops.subtract(r, b).point(_gt(60)))
    bright = _all(r.point(_gt(180)), g.point(_gt(180)), b.point(_gt(180)))
    return {k: m.tobytes() for k, m in
            (("red", red), ("purple", purple), ("empty", empty), ("blue", blue),
             ("gold", gold), ("bright", bright))}


def _row_runs(mask: bytes, y: int, w: int, gap: int) -> list[tuple[int, int]]:
    """Відрізки увімкнених пікселів у рядку маски."""
    row = mask[y * w:(y + 1) * w]
    if row.find(255) < 0:
        return []
    out: list[list[int]] = []
    start = 0
    while True:
        i = row.find(255, start)
        if i < 0:
            break
        j = i
        while j + 1 < w and (row[j + 1] == 255 or (row.find(255, j + 1, j + 2 + gap) > 0)):
            nxt = row.find(255, j + 1, j + 2 + gap)
            if nxt < 0:
                break
            j = nxt
        out.append([i, j])
        start = j + 1
    return [(a, b - a + 1) for a, b in out]


def _runs(px, y: int, width: int, pred, gap: int) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for x in range(width):
        if pred(px[x, y]):
            if out and x - out[-1][1] <= gap:
                out[-1][1] = x
            else:
                out.append([x, x])
    return [(r[0], r[1] - r[0] + 1) for r in out]


def _full_width(px, x0: int, y: int, w: int, cfg: PetFrameConfig) -> int:
    """
    Знайдений червоний відрізок — це лише ЗАПОВНЕНА частина. Повна ширина смужки
    рахується далі вправо по темній порожній частині, інакше 50% HP виглядали б як 100%.
    """
    x = x0
    while x < w and (x - x0) < cfg.max_width and (_is_red(px[x, y]) or _is_empty(px[x, y])):
        x += 1
    return max(1, x - x0)


def read_bar(image: Image.Image, frame: "PetFrame", row: int, gold: bool = False,
             spread: int = 3) -> tuple[int, int]:
    """
    (скільки заповнено, повна ширина) у смужці рамки пета.

    Дивимось кілька сусідніх рядків і беремо найкращий: смужка заввишки кілька
    пікселів, а крайні рядки — це антиаліасинг, на них заповнення майже не видно.
    """
    px = image.load()
    w, h = image.size
    fill = _is_gold if gold else _is_red
    best = 0
    for y in range(max(0, row - spread), min(h, row + spread + 1)):
        n = sum(1 for x in range(frame.x0, min(w, frame.x0 + frame.width)) if fill(px[x, y]))
        best = max(best, n)
    return best, frame.width


def find_pet_frame(image: Image.Image, cfg: PetFrameConfig | None = None,
                   area: tuple[int, int, int, int] | None = None) -> PetFrame | None:
    """
    Геометрія рамки пета або None, якщо її на екрані нема.

    Ознака — СТОС смужок однакової ширини: HP зверху, під нею ситість, іноді ще
    досвід. Спиратись на фіолетову смужку не можна: у молодого пета вона порожня,
    і рамка тоді просто не знаходилась.
    """
    cfg = cfg or PetFrameConfig()
    off_x, off_y = 0, 0
    if area:
        off_x, off_y = area[0], area[1]
        image = image.crop(area)
    w, h = image.size
    m = _color_masks(image)
    red, empty, blue = m["red"], m["empty"], m["blue"]
    gold, purple, bright = m["gold"], m["purple"], m["bright"]

    def span(masks, y: int, x0: int) -> int:
        """Довжина смужки праворуч від x0 по будь-якій із масок."""
        base, x, miss, width = y * w, x0, 0, 0
        while x < w and (x - x0) < cfg.max_width + cfg.gap:
            if any(mask[base + x] for mask in masks):
                width, miss = x - x0 + 1, 0
            else:
                miss += 1
                if miss > cfg.gap:
                    break
            x += 1
        return width

    for y in range(h - cfg.purple_below[1]):
        base = y * w
        row = red[base:base + w]
        x = row.find(255)
        while x >= 0:
            red_len = span([red], y, x)
            if red_len >= cfg.min_red:
                # білий текст поверх смужки («472/472») теж належить смужці,
                # інакше широка рамка цілі виглядала б як вузька рамка пета
                width = span([red, empty, bright], y, x)
                if cfg.min_width <= width <= cfg.max_width:
                    below = _next_bar(y, x, width, w, h, cfg,
                                      [empty, gold, purple, red, bright], blue, gold, purple)
                    if below is not None:
                        exact = _bar_end(image, x, y, cfg)
                        if exact is not None and exact < cfg.min_width:
                            # справжня смужка пета ~82 px; коротший «край» — це обрізок чужих
                            # смужок (івент-бос поруч), і «ситість 2% (1/42px)» годувала б повітря
                            x = row.find(255, x + max(1, red_len))
                            continue
                        return PetFrame(x0=x + off_x, width=exact or width, hp_row=y + off_y,
                                        food_row=below[0] + off_y, purple_row=below[1] + off_y,
                                        exact=exact is not None)
            x = row.find(255, x + max(1, red_len))
    return None


def _next_bar(hp_row: int, x0: int, width: int, w: int, h: int, cfg: PetFrameConfig,
              masks: list[bytes], blue: bytes, gold: bytes, purple: bytes) -> tuple[int, int] | None:
    """Рядок наступної смужки того ж розміру під HP (і рядок ще однієї, якщо є)."""
    # спершу дивимось усю смугу під HP: синя смужка = мана, тобто рамка персонажа
    for dy in range(3, cfg.purple_below[1]):
        y = hp_row + dy
        if y >= h:
            break
        base = y * w
        if sum(1 for x in range(x0, min(w, x0 + width)) if blue[base + x]) > width * 0.25:
            return None

    rows: list[int] = []
    for dy in range(4, cfg.purple_below[1]):
        y = hp_row + dy
        if y >= h:
            break
        base = y * w
        hits = sum(1 for x in range(x0, min(w, x0 + width)) if any(mk[base + x] for mk in masks))
        if hits < width * 0.8:
            continue
        if not rows or y - rows[-1] > 2:      # смужки йдуть із проміжком
            rows.append(y)
        if len(rows) == 2:
            break
    if not rows:
        return None
    # у рамці пета під HP є ситість (золота) або досвід (фіолетовий).
    # Без цієї вимоги «стосом смужок» виглядає майже будь-який куточок інтерфейсу.
    own = 0
    for dy in range(4, cfg.purple_below[1]):          # уся смуга під HP, а не лише
        y = hp_row + dy                               # два перші рядки: у голодного
        if y >= h:                                    # пета золота нема зовсім,
            break                                     # і рамку тримає лише досвід
        base = y * w
        own += sum(1 for x in range(x0, min(w, x0 + width))
                   if gold[base + x] or purple[base + x])
    if own < width * 0.08:
        return None
    return rows[0], rows[-1]
