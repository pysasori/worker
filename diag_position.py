"""
Діагностика читання координат/висоти — показує, що саме бачить OCR у зоні цифр,
і де насправді намальовані числа на кадрі (пошук по всій верхній смузі).

Запуск (з теки backend, вікно гри відкрите, персонаж живий):
    .venv\\Scripts\\python diag_position.py [hwnd]
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image, ImageDraw

from app.capture.window_finder import WindowMatch, find_windows
from app.capture.win32 import Win32WindowCapture
from app.core.geometry import Region
from app.vision.digits import DigitsConfig, read_numbers
from app.vision.text import OcrConfig

CANDIDATE = Region.of(1068, 22, 78, 14)   # те, що зараз у профілі 1280x720


def main() -> int:
    if len(sys.argv) > 1:
        hwnd = int(sys.argv[1])
    else:
        found = find_windows(WindowMatch())
        if not found:
            print("!! жодного вікна гри не знайдено")
            return 1
        hwnd = found[0]
    print(f"вікно: hwnd={hwnd}")

    image = Win32WindowCapture(hwnd).grab()
    print(f"кадр {image.size}")
    image.save("diag_pos_full.png")

    ocr = OcrConfig()

    # 1) читання рівно там, де зараз стоїть профіль
    white = DigitsConfig(region=CANDIDATE, color="white")
    green = DigitsConfig(region=CANDIDATE, color="green")
    print(f"\n[1] зона {CANDIDATE.box} (як у профілі):")
    print(f"    координати (білі): {read_numbers(image, white, ocr)}")
    print(f"    висота (зелені):   {read_numbers(image, green, ocr)}")

    crop = image.crop(CANDIDATE.box)
    crop.resize((crop.width * 5, crop.height * 5), Image.NEAREST).save("diag_pos_candidate.png")
    print("    -> diag_pos_candidate.png (збільшено x5)")

    # 2) широка смуга праворуч зверху — де числа насправді намальовані
    band = Region.of(900, 0, 380, 60)
    strip = image.crop(band.box)
    strip.resize((strip.width * 2, strip.height * 2), Image.NEAREST).save("diag_pos_band.png")
    print(f"\n[2] широка смуга {band.box} (праворуч зверху, х2) -> diag_pos_band.png")

    # 3) сканування: де в цій смузі найбільше білих пікселів у рядок 10px
    #    (сам текст, а не фон) — грубий пошук правильної зони
    arr = np.asarray(strip.convert("L"))
    best = None
    for y in range(0, arr.shape[0] - 14, 2):
        for x in range(0, arr.shape[1] - 78, 4):
            cell = arr[y:y + 14, x:x + 78]
            bright = int((cell >= 170).sum())
            if best is None or bright > best[0]:
                best = (bright, x, y)
    if best:
        bright, x, y = best
        abs_x, abs_y = band.x + x, band.y + y
        print(f"\n[3] найяскравіша ділянка 78x14 у смузі: {bright} світлих пікселів "
              f"при зоні ({abs_x},{abs_y},78,14)")
        guess = Region.of(abs_x, abs_y, 78, 14)
        print(f"    пробне читання там: {read_numbers(image, DigitsConfig(region=guess, color='white'), ocr)}")
        marked = strip.copy()
        ImageDraw.Draw(marked).rectangle([x, y, x + 78, y + 14], outline=(255, 0, 0), width=2)
        marked.resize((marked.width * 2, marked.height * 2), Image.NEAREST).save("diag_pos_guess.png")
        print("    -> diag_pos_guess.png (позначено червоним)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
