"""
Читання тексту з кадру (назва цілі) і нечітке порівняння назв.

OCR помиляється в окремих літерах: «Горный варвар» легко стає «Горный варвао».
Тому назви не порівнюються на точний збіг — рахується схожість рядків, і збіг
зараховується від порога (за замовчуванням 0.75). Так список «кого бити» працює,
навіть коли розпізнавання трохи хибить.

Tesseract береться з системи, а мовний файл — з models/tessdata у проєкті, тому
нічого в системі міняти не треба. Якщо його нема — читання просто вимикається.
"""
from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
_LETTERS = re.compile(r"[^a-zа-яіїєґ0-9 ]+", re.IGNORECASE)
# OCR часто плутає кирилицю з латинськими двійниками: «варвар» -> «вapвap».
# Зводимо їх до кирилиці, інакше схожі рядки давали б низький відсоток збігу.
_HOMOGLYPHS = str.maketrans({
    "a": "а", "b": "ь", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м",
    "o": "о", "p": "р", "t": "т", "x": "х", "y": "у", "u": "и", "3": "з",
})


class OcrConfig(BaseModel):
    enabled: bool = Field(default=True, title="Читати назви")
    engine: str = Field(json_schema_extra={"tech": True}, default="tesseract", title="Двигун",
                        description="tesseract — точна кирилиця (~140 мс); "
                                    "rapidocr — утричі швидший, але зі стандартною моделлю "
                                    "читає кирилицю латиницею")
    lang: str = Field(json_schema_extra={"tech": True}, default="rus", title="Мова")
    psm: int = Field(json_schema_extra={"tech": True}, default=7, title="Режим рядка", description="7 = один рядок тексту")
    scale: int = Field(json_schema_extra={"tech": True}, default=4, ge=1, le=8, title="Збільшення перед читанням")
    tesseract_cmd: str = Field(json_schema_extra={"tech": True}, default=r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                               title="Шлях до tesseract.exe")
    tessdata_dir: str = Field(json_schema_extra={"tech": True}, default=str(ROOT / "models" / "tessdata"),
                              title="Тека з мовними файлами")


@lru_cache(maxsize=4)
def _engine(cmd: str, tessdata: str):
    """None, якщо tesseract недоступний — тоді читання назв просто вимикається."""
    try:
        import pytesseract
    except ImportError:
        return None
    if not Path(cmd).exists():
        return None
    pytesseract.pytesseract.tesseract_cmd = cmd
    # шлях із профілю міг бути написаний на іншому ПК (F:\...) — тоді беремо теку проєкту,
    # інакше системний tesseract без rus.traineddata мовчки читає порожнечу
    for candidate in (Path(tessdata), ROOT / "models" / "tessdata"):
        if candidate.is_dir():
            os.environ["TESSDATA_PREFIX"] = str(candidate.resolve())
            break
    return pytesseract


@lru_cache(maxsize=1)
def _rapid():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return None
    return RapidOCR()


def _read_rapid(image: Image.Image) -> str:
    ocr = _rapid()
    if ocr is None:
        return ""
    try:
        import numpy as np

        result, _ = ocr(np.array(image))
    except Exception:
        return ""
    return " ".join(line[1] for line in result) if result else ""


def read_line(crop: Image.Image, cfg: OcrConfig) -> str:
    """Один рядок тексту з вирізки. Порожній рядок, якщо прочитати не вдалось."""
    if not cfg.enabled:
        return ""
    image = crop
    if cfg.scale > 1:
        image = crop.resize((crop.width * cfg.scale, crop.height * cfg.scale), Image.LANCZOS)
    if cfg.engine == "rapidocr":
        return " ".join(_read_rapid(image).split())
    engine = _engine(cfg.tesseract_cmd, cfg.tessdata_dir)
    if engine is None:
        return ""
    try:
        text = engine.image_to_string(image, lang=cfg.lang, config=f"--psm {cfg.psm}")
    except Exception:
        return ""
    return " ".join(text.split())


def read_lines(crop: Image.Image, cfg: OcrConfig) -> list[tuple[str, tuple[int, int, int, int]]]:
    """
    Рядки тексту з їхніми рамками (у координатах вирізки). Потрібно, щоб клікнути
    саме по слову: наприклад по точці «фарм» у списку, де її місце залежить від того,
    скільки точок вище.
    """
    if not cfg.enabled:
        return []
    engine = _engine(cfg.tesseract_cmd, cfg.tessdata_dir)
    if engine is None:
        return []
    k = max(1, cfg.scale)
    image = crop.resize((crop.width * k, crop.height * k), Image.LANCZOS) if k > 1 else crop
    try:
        data = engine.image_to_data(image, lang=cfg.lang, config="--psm 6",
                                    output_type=engine.Output.DICT)
    except Exception:
        return []
    lines: dict[tuple[int, int, int], list[int]] = {}
    for i, word in enumerate(data["text"]):
        if word.strip():
            lines.setdefault((data["block_num"][i], data["par_num"][i], data["line_num"][i]), []).append(i)
    out = []
    for idx in lines.values():
        text = " ".join(data["text"][i] for i in idx)
        x0 = min(data["left"][i] for i in idx)
        y0 = min(data["top"][i] for i in idx)
        x1 = max(data["left"][i] + data["width"][i] for i in idx)
        y1 = max(data["top"][i] + data["height"][i] for i in idx)
        out.append((text, (x0 // k, y0 // k, x1 // k, y1 // k)))
    return sorted(out, key=lambda line: line[1][1])


def normalize(name: str) -> str:
    """Порівнюємо без рівня в дужках, розділових знаків і регістру."""
    name = name.lower().replace("ё", "е").translate(_HOMOGLYPHS)
    name = re.sub(r"\[.*?\]", " ", name)
    name = _LETTERS.sub(" ", name)
    return " ".join(name.split())


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def best_match(text: str, names: list[str], min_ratio: float) -> tuple[str, float] | None:
    """Найсхожіша назва зі списку або None, якщо нічого не дотягує до порога."""
    clean = normalize(text)
    if not clean or not names:
        return None
    best = max(((n, SequenceMatcher(None, clean, normalize(n)).ratio()) for n in names),
               key=lambda p: p[1])
    return best if best[1] >= min_ratio else None
