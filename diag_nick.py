"""
Діагностика читання ніка — окремо від сканера, з проміжними кроками напоказ.

read_nick() мовчки повертає "" у трьох різних місцях (нема іконки-воріт, нема
жодного світлого пікселя в табличці, OCR не встановлено чи прочитав сміття) —
і зовні всі три виглядають однаково: "нік ще не прочитано". Цей скрипт показує
кожен крок окремо, щоб не гадати, який саме.

Запуск (з теки backend, вікно гри має бути відкрите):
    .venv\\Scripts\\python diag_nick.py [hwnd]
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from app.capture.window_finder import WindowMatch, find_windows
from app.capture.win32 import Win32WindowCapture
from app.vision.nick import NickConfig, read_nick
from app.vision.template import find_template, match
from app.vision.text import _engine, read_line


def main() -> int:
    cfg = NickConfig()

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
    image.save("diag_nick_full.png")
    print(f"кадр {image.size} збережено -> diag_nick_full.png")

    # 1) чи встановлений tesseract узагалі
    eng = _engine(cfg.ocr.tesseract_cmd, cfg.ocr.tessdata_dir)
    print(f"\n[1] tesseract знайдено: {eng is not None}"
          f"{f' (шлях {cfg.ocr.tesseract_cmd!r} не існує!)' if eng is None else ''}")

    # 2) іконка-ворота (hud_wrench) — без неї нік не читається ВЗАГАЛІ
    if cfg.hud is not None:
        point, score = match(image, cfg.hud)
        print(f"\n[2] hud_wrench: score={score:.3f} поріг={cfg.hud.threshold} "
              f"-> {'ПРОЙШЛО' if point else 'НЕ ЗНАЙШЛО'}")
    else:
        print("\n[2] hud вимкнений у налаштуваннях — пропускаємо")

    # 3) сама табличка: скільки світлих пікселів у зоні ніка
    crop = image.crop(cfg.region.box).convert("L")
    crop.resize((crop.width * 4, crop.height * 4)).save("diag_nick_region.png")
    arr = np.asarray(crop)
    mask = arr >= cfg.threshold
    print(f"\n[3] зона ніка {cfg.region.box}: яскравість {arr.min()}-{arr.max()}, "
          f"поріг {cfg.threshold}, світлих пікселів {mask.sum()} з {mask.size} "
          f"-> збережено diag_nick_region.png (збільшено x4)")

    # 4) сирий текст без фільтра довжини — що OCR узагалі прочитав
    if mask.any():
        ink = __import__("PIL.Image", fromlist=["Image"]).fromarray(
            (255 - mask.astype("uint8") * 255).astype("uint8"))
        ink.resize((ink.width * 4, ink.height * 4)).save("diag_nick_mask.png")
        raw = read_line(ink, cfg.ocr)
        print(f"\n[4] сирий OCR (без фільтра довжини): {raw!r} -> diag_nick_mask.png")
    else:
        print("\n[4] маска порожня — OCR навіть не викликали")

    # 5) підсумок тим самим шляхом, що йде сканер
    final = read_nick(image, cfg)
    print(f"\n[5] read_nick() -> {final!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
