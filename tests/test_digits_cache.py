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
