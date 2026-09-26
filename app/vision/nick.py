"""
Нік персонажа з кадру гри.

Заголовок вікна в усіх клієнтів однаковий («ComebackPW»), тому відрізнити персонажів
можна лише по табличці з ніком у лівому верхньому куті. Читаємо її так само, як інші
написи: вирізка -> чорно-біла маска -> tesseract.

Маска потрібна, бо табличка то помаранчева (ціль вибрана), то темна, а по краях у неї
рамка, яку OCR читає як «|». Світлі літери відділяються від будь-якого фону порогом.

OCR плутає кирилицю з латиницею («CEKXU» -> «СЕКХИ»), і плутає завжди однаково, тому
ключем персонажа служить прочитаний рядок як є, а порівнюються ніки нечітко — через
ту саму нормалізацію, що й назви мобів.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from app.core.geometry import Region
from app.vision.template import TemplateSpec, find_template
from app.vision.text import OcrConfig, normalize, read_line

_ALLOWED = re.compile(r"[^\w\-. ]+", re.UNICODE)


class NickConfig(BaseModel):
    region: Region = Field(default=Region.of(112, 64, 108, 15), title="Табличка з ніком",
                           description="зона, де клієнт малює нік; рамку таблички захоплювати "
                                       "не треба — її OCR читає як «|»")
    threshold: int = Field(default=150, ge=0, le=255, title="Поріг яскравості літер",
                           description="літери світліші за фон; вище — губляться тонкі літери, "
                                       "нижче — до тексту липне фон")
    hud: TemplateSpec | None = Field(
        default_factory=lambda: TemplateSpec(name="hud_wrench.png", threshold=0.7,
                                             area=Region.of(120, 0, 180, 40)),
        title="Ознака, що клієнт у грі",
        description="кнопка налаштувань над рамкою персонажа. Без неї (екран завантаження, "
                    "вибір персонажа) нік не читається: статична картинка читалась би "
                    "однаково щоразу, і сміття виглядало б стабільним ніком")
    min_length: int = Field(default=2, ge=1, title="Найкоротший нік")
    max_length: int = Field(default=24, ge=1, title="Найдовший нік")
    same_ratio: float = Field(default=0.75, gt=0, le=1, title="Схожість двох прочитань ніка",
                              description="OCR інколи хибить в одній літері, і той самий нік "
                                          "не повинен ставати двома персонажами")
    ocr: OcrConfig = Field(
        default_factory=lambda: OcrConfig(lang="eng+rus", scale=4, psm=7),
        title="Читання тексту",
        description="з однією лише російською моделлю латинський нік читався як «Вгои огКег»")


def read_nick(image: Image.Image, cfg: NickConfig) -> str:
    """Нік або порожній рядок, якщо таблички нема чи текст не схожий на нік."""
    if cfg.hud is not None and find_template(image, cfg.hud) is None:
        return ""
    crop = image.crop(cfg.region.box).convert("L")
    mask = np.asarray(crop) >= cfg.threshold
    if not mask.any():
        return ""
    ink = Image.fromarray((255 - mask.astype("uint8") * 255).astype("uint8"))   # чорне на білому
    text = _ALLOWED.sub("", read_line(ink, cfg.ocr)).strip()
    return text if cfg.min_length <= len(text) <= cfg.max_length else ""


def same_nick(a: str, b: str, ratio: float = 0.75) -> bool:
    """Чи це той самий нік із точністю до помилок читання."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return False
    return na == nb or SequenceMatcher(None, na, nb).ratio() >= ratio
