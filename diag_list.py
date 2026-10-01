"""
Діагностика пошуку точки в «Списку» — повторює _find_point() з return_home.py
крок за кроком, із збереженням проміжних картинок.

Відкрий «Список» у грі (кнопка під мінімапою) ПЕРЕД запуском.

Запуск (з теки backend):
    .venv\\Scripts\\python diag_list.py [hwnd]
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image, ImageChops, ImageOps

from app.capture.window_finder import WindowMatch, find_windows
from app.capture.win32 import Win32WindowCapture
from app.core.geometry import Region
from app.pipelines.return_home import ReturnHomeConfig
from app.vision.template import find_template
from app.vision.text import best_match, read_lines


def text_mask(image: Image.Image, threshold: int) -> Image.Image:
    r, g, b = image.split()

    def keep(v: int) -> int:
        return 255 if v > threshold else 0

    mask = ImageChops.multiply(ImageChops.multiply(r.point(keep), g.point(keep)), b.point(keep))
    return ImageOps.invert(mask).convert("RGB")


def main() -> int:
    cfg = ReturnHomeConfig()

    if len(sys.argv) > 1:
        hwnd = int(sys.argv[1])
    else:
        found = find_windows(WindowMatch())
        if not found:
            print("!! жодного вікна гри не знайдено")
            return 1
        hwnd = found[0]
    print(f"вікно: hwnd={hwnd}, точка, яку шукаємо: {cfg.point_name!r}")

    image = Win32WindowCapture(hwnd).grab()
    image.save("diag_list_full.png")

    # 1) заголовок вікна «Список» — шукається по всьому кадру
    title = find_template(image, cfg.title)
    if title is None:
        print("\n!! заголовок «Список» не знайдено — вікно не відкрите чи шаблон не підійшов")
        return 1
    print(f"\n[1] заголовок «Список» знайдено в ({title.x}, {title.y})")

    # 2) права колонка відносно заголовка
    box = Region.of(title.x + cfg.pane.x, title.y + cfg.pane.y, cfg.pane.w, cfg.pane.h)
    print(f"\n[2] права колонка: {box.box} (title + offset {cfg.pane.x},{cfg.pane.y}, "
          f"розмір {cfg.pane.w}x{cfg.pane.h})")
    raw = image.crop(box.box)
    raw.resize((raw.width * 3, raw.height * 3), Image.NEAREST).save("diag_list_pane_raw.png")
    print("    сира вирізка -> diag_list_pane_raw.png (х3)")

    # 3) маска (світлий текст -> чорне на білому)
    pane = text_mask(raw, cfg.text_threshold)
    pane.resize((pane.width * 3, pane.height * 3), Image.NEAREST).save("diag_list_pane_mask.png")
    print(f"\n[3] поріг яскравості {cfg.text_threshold} -> diag_list_pane_mask.png (х3)")

    # 4) що OCR прочитав рядок за рядком
    print(f"\n[4] рядки, які прочитав OCR (поріг схожості {cfg.min_ratio}):")
    lines = read_lines(pane, cfg.ocr)
    if not lines:
        print("    !! жодного рядка — OCR нічого не побачив у цій зоні")
    for text, box_rel in lines:
        hit = best_match(text, [cfg.point_name], cfg.min_ratio)
        mark = f"  <-- збіг {hit[1]:.2f}" if hit else ""
        print(f"    {text!r} @ {box_rel}{mark}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
