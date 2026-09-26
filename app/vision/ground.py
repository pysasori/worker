"""
Пошук лута на землі — у КВАДРАТІ навколо центру екрана, не по всьому кадру.

Персонаж завжди в центрі, лут падає поруч, тому дивитись треба тільки туди: так
і швидше, і не чіпляється чуже добро та інтерфейс по краях.

Ознака: клієнт PW підписує предмет на землі БІЛИМ текстом ("Пыль элементаля").
Шукаємо саме такі підписи — рядки майже білих пікселів завширшки як слово і
заввишки як рядок тексту. Свій нік і персонаж вирізаються внутрішнім квадратом
`exclude`.

Обмеження: назви мобів малюються таким самим білим текстом, тому підпис моба
поруч теж зарахується. Для збору лута це не шкодить: зайве натискання підбору
нічого не робить, а серія все одно обмежена max_presses.
"""
from __future__ import annotations

from PIL import Image
from pydantic import BaseModel, Field

from app.core.geometry import Region


class GroundCheckConfig(BaseModel):
    enabled: bool = Field(default=True, title="Перевіряти землю")
    size: int = Field(default=420, gt=32, title="Сторона квадрата, px")
    offset_x: int = Field(default=0, title="Зсув по X")
    offset_y: int = Field(default=0, title="Зсув по Y")
    exclude: int = Field(json_schema_extra={"tech": True}, default=120, ge=0, title="Виріз навколо персонажа, px")

    min_brightness: int = Field(json_schema_extra={"tech": True}, default=200, ge=0, le=255, title="Поріг білого")
    min_row_pixels: int = Field(json_schema_extra={"tech": True}, default=12, gt=0, title="Білих пікселів у рядку")
    min_width: int = Field(json_schema_extra={"tech": True}, default=30, gt=0, title="Мінімальна ширина підпису, px")
    min_height: int = Field(json_schema_extra={"tech": True}, default=4, gt=0, title="Мінімальна висота, px")
    max_height: int = Field(json_schema_extra={"tech": True}, default=20, gt=0, title="Максимальна висота, px")
    min_labels: int = Field(json_schema_extra={"tech": True}, default=1, ge=1, title="Підписів = щось лежить")


class GroundReading(BaseModel):
    has_loot: bool = False
    labels: int = 0
    boxes: list[tuple[int, int, int, int]] = Field(default_factory=list, description="підписи (x, y, w, h) у координатах вікна")
    region: Region

    def __str__(self) -> str:
        return f"{'лут' if self.has_loot else 'порожньо'} ({self.labels} підписів)"


def center_square(frame_size: tuple[int, int], cfg: GroundCheckConfig) -> Region:
    w, h = frame_size
    side = min(cfg.size, w, h)
    half = side // 2
    x = max(0, min(w - side, w // 2 + cfg.offset_x - half))
    y = max(0, min(h - side, h // 2 + cfg.offset_y - half))
    return Region.of(x, y, side, side)


def _text_row(px, y: int, w: int, cx: int, ex: int, skip_x: bool,
              cfg: GroundCheckConfig) -> tuple[int, int] | None:
    """
    Рядок літер: багато білих пікселів, розкиданих на ширину слова.

    Літери в рядку розділені розривами до 10 px, тому суцільного відрізка нема —
    вимірюємо щільність і розмах, а не довжину суцільної смуги.
    """
    xs: list[int] = []
    for x in range(w):
        p = px[x, y]
        if p[0] >= cfg.min_brightness and p[1] >= cfg.min_brightness and p[2] >= cfg.min_brightness:
            if skip_x and abs(x - cx) <= ex:
                continue  # свій нік над персонажем
            xs.append(x)
    if len(xs) < cfg.min_row_pixels:
        return None
    if xs[-1] - xs[0] + 1 < cfg.min_width:
        return None
    return xs[0], xs[-1]


def scan_ground(crop: Image.Image, region: Region, cfg: GroundCheckConfig) -> GroundReading:
    """crop — вирізка квадрата пошуку з кадру."""
    px = crop.load()
    w, h = crop.size
    ex = cfg.exclude // 2
    cx, cy = w // 2, h // 2

    rows: dict[int, tuple[int, int]] = {}
    for y in range(h):
        span = _text_row(px, y, w, cx, ex, abs(y - cy) <= ex, cfg)
        if span:
            rows[y] = span

    # склеюємо сусідні рядки в підписи
    groups: list[list[int]] = []  # [y0, y1, x0, x1]
    for y in sorted(rows):
        x0, x1 = rows[y]
        if groups and y - groups[-1][1] <= 1:
            g = groups[-1]
            g[1], g[2], g[3] = y, min(g[2], x0), max(g[3], x1)
        else:
            groups.append([y, y, x0, x1])

    labels: list[tuple[int, int, int, int]] = []
    for y0, y1, x0, x1 in groups:
        height, width = y1 - y0 + 1, x1 - x0 + 1
        if cfg.min_height <= height <= cfg.max_height and width >= cfg.min_width:
            wx, wy = region.to_window(x0, y0)
            labels.append((wx, wy, width, height))

    return GroundReading(has_loot=len(labels) >= cfg.min_labels, labels=len(labels),
                         boxes=labels, region=region)
