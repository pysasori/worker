"""
Кеш читання цифр: персонаж стоїть — маска та сама — tesseract.exe не запускаємо.

Кожне читання координат/висоти — окремий процес tesseract; при кількох вікнах таких
запусків десятки на секунду, і на слабкій машині всі боти лагали.
"""
from __future__ import annotations

import pytest
from PIL import Image

from app.vision import digits
from app.vision.digits import DigitsConfig, read_numbers
from app.vision.text import OcrConfig
from tests.conftest import FIXTURES


@pytest.fixture
def frame() -> Image.Image:
    return Image.open(FIXTURES / "frame_1440_coords.png").convert("RGB")


@pytest.fixture(autouse=True)
def fresh_cache():
    digits.clear_cache()
    yield
    digits.clear_cache()


def count_ocr_calls(monkeypatch, result: str = "241, 563") -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(digits, "_GLYPHS", {})      # без абетки цифр: рахуємо саме запуски OCR

    def fake(piece, cfg):
        calls.append(1)
        return result

    monkeypatch.setattr(digits, "read_line", fake)
    return calls


def test_same_picture_is_read_once(monkeypatch, frame):
    """Одне читання — це голосування кількох запусків tesseract; повтори — вже безкоштовні."""
    calls = count_ocr_calls(monkeypatch)
    cfg, ocr = DigitsConfig(), OcrConfig()
    first = read_numbers(frame, cfg, ocr)
    after_first = len(calls)
    assert after_first >= 1
    for _ in range(5):
        assert read_numbers(frame, cfg, ocr) == first == [241, 563]
    assert len(calls) == after_first, "п'ять повторів — жодного нового запуску OCR"


def test_changed_digits_are_read_again(monkeypatch, frame):
    """Інші цифри на екрані = інша маска = новий ключ, а не стара відповідь."""
    calls = count_ocr_calls(monkeypatch)
    cfg, ocr = DigitsConfig(), OcrConfig()
    read_numbers(frame, cfg, ocr)
    before = len(calls)
    other = frame.copy()
    for x in range(1235, 1250):                      # затираємо частину цифр
        for y in range(24, 34):
            other.putpixel((x, y), (30, 30, 30))
    read_numbers(other, cfg, ocr)
    assert len(calls) > before, "інша маска — це нове читання, а не стара відповідь"


def test_cache_ignores_background_changes(monkeypatch, frame):
    """Під панеллю рухається картинка світу, а цифри ті самі — це не привід читати знову."""
    calls = count_ocr_calls(monkeypatch)
    cfg, ocr = DigitsConfig(), OcrConfig()
    read_numbers(frame, cfg, ocr)
    before = len(calls)
    shifted = frame.copy()
    shifted.putpixel((1229, 23), (12, 80, 40))       # піксель фону, не цифра
    read_numbers(shifted, cfg, ocr)
    assert len(calls) == before


def test_empty_result_is_not_cached(monkeypatch, frame):
    """Порожньо = OCR не спрацював (нема tesseract, збій). Лишати це в кеші не можна:
    після встановлення tesseract бот і далі бачив би «координат нема» до перезапуску."""
    calls = count_ocr_calls(monkeypatch, result="")
    cfg, ocr = DigitsConfig(), OcrConfig()
    assert read_numbers(frame, cfg, ocr) == []
    one_read = len(calls)
    assert read_numbers(frame, cfg, ocr) == []
    assert len(calls) == 2 * one_read, "порожнє не запам'яталось: друге читання знову пробує OCR"


def test_cache_is_bounded(monkeypatch, frame):
    monkeypatch.setattr(digits, "_CACHE_MAX", 3)
    count_ocr_calls(monkeypatch)
    cfg, ocr = DigitsConfig(), OcrConfig()
    for i in range(10):
        img = frame.copy()
        for x in range(1235, 1235 + i + 1):
            img.putpixel((x, 25), (20, 20, 20))
        read_numbers(img, cfg, ocr)
    assert len(digits._CACHE) <= 3


def test_colours_do_not_share_an_entry(monkeypatch, frame):
    """Білі координати й зелена висота з однієї зони — різні маски, різні записи."""
    calls = count_ocr_calls(monkeypatch)
    ocr = OcrConfig()
    read_numbers(frame, DigitsConfig(color="white"), ocr)
    read_numbers(frame, DigitsConfig(color="green"), ocr)
    settled = len(calls)
    read_numbers(frame, DigitsConfig(color="white"), ocr)
    read_numbers(frame, DigitsConfig(color="green"), ocr)
    assert len(calls) == settled, "обидва кольори вже в кеші окремими записами"


# ---- абетка цифр: читання без tesseract ---------------------------------------------
def test_reads_coordinates_by_glyphs_without_tesseract(monkeypatch, frame):
    """Шрифт панелі стоїть на місці: цифри впізнаються точним порівнянням, tesseract не потрібен."""
    def boom(*a, **k):
        raise AssertionError("tesseract не мав запускатись")

    monkeypatch.setattr(digits, "read_line", boom)
    assert read_numbers(frame, DigitsConfig(), OcrConfig()) == [241, 563]


def test_touching_digits_are_split(monkeypatch):
    """«24» у «486»: цифри торкаються краями й виходять однією широкою фігурою."""
    from PIL import Image as _I

    from app.vision.digits import GLYPH_FILE, _glyph_table, read_by_glyphs

    table = _glyph_table()
    rows = {d: k.split("|") for k, d in table.items()}
    def render(text: str, gap: int = 1) -> _I.Image:
        chars = text.replace(" ", "")
        parts = [rows[ch] if ch != "," else ["."] * 8 + ["#"] * 4 for ch in chars]
        # між цифрами одного числа — gap (0 = впритул), навколо коми завжди порожній стовпець
        gaps = [gap if ch != "," and (i + 1 < len(chars) and chars[i + 1] != ",") else 1
                for i, ch in enumerate(chars)]
        height = max(len(p) for p in parts)
        img = _I.new("L", (sum(len(p[0]) + g for p, g in zip(parts, gaps)) + 4, height + 4), 0)
        x = 2
        for p, g in zip(parts, gaps):
            for dy, row in enumerate(p):
                for dx, ch in enumerate(row):
                    if ch == "#":
                        img.putpixel((x + dx, 2 + dy), 255)
            x += len(p[0]) + g
        return img

    assert read_by_glyphs(render("486,585")) == [486, 585]
    assert read_by_glyphs(render("486,585", gap=0)) == [486, 585], "цифри впритул — теж розрізаються"
    assert GLYPH_FILE.exists()


def test_unknown_shape_falls_back_to_tesseract(monkeypatch, frame):
    """Фігура, якої нема в абетці, — не вгадуємо, а віддаємо читання tesseract-у."""
    from app.vision.digits import read_by_glyphs

    weird = Image.new("L", (30, 14), 0)
    for x in range(2, 7):
        for y in range(2, 12):
            weird.putpixel((x, y), 255)                  # суцільний прямокутник — не цифра
    assert read_by_glyphs(weird) is None
