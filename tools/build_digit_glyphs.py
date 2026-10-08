r"""
Збирає «абетку» цифр панелі координат: кожна цифра — це маленька картинка (5-7 px завширшки,
10 px заввишки) однієї й тієї ж гри-шрифту, тому її можна впізнавати точним порівнянням,
а не tesseract-ом, який на такому тексті плутає 586/566, губить кому й додає цифри.

Звідки мітки: tesseract читає той самий рядок багатьма способами; мітку беремо, лише коли
кількість знайдених цифр збіглась із кількістю фігур, а кілька способів дали ОДНЕ число.
Результат — assets/digit_glyphs.json. Запуск (гра запущена, персонажі десь стоять):
    .venv\Scripts\python tools\build_digit_glyphs.py [секунд]
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image, ImageOps

from app.vision.digits import (DigitsConfig, _mask, glyphs_of, GLYPH_FILE)
from app.vision.text import OcrConfig, read_line

REGIONS = [(1068, 22, 78, 14), (1228, 22, 78, 14)]      # 1280x720 і 1440x1080
VARIANTS = [("eng", 8, False), ("eng", 6, False), ("rus", 4, False), ("eng", 8, True),
            ("eng", 10, False), ("eng", 5, False)]


def consensus(mask: Image.Image) -> str | None:
    """Число (лише цифри) з білої маски, якщо щонайменше 3 способи дали те саме."""
    bb = mask.getbbox()
    if bb is None:
        return None
    base = ImageOps.invert(mask.crop((max(0, bb[0] - 3), max(0, bb[1] - 3), bb[2] + 3, bb[3] + 3)))
    ocr = OcrConfig()
    votes: Counter = Counter()
    for lang, scale, wl in VARIANTS:
        piece = base.resize((base.width * scale, base.height * scale), Image.LANCZOS)
        extra = "-c tessedit_char_whitelist=0123456789," if wl else ""
        text = read_line(piece, ocr.model_copy(update={"psm": 7, "scale": 1, "lang": lang, "extra": extra}))
        digits = "".join(ch for ch in text if ch.isdigit())
        if digits:
            votes[digits] += 1
    if not votes:
        return None
    best, n = votes.most_common(1)[0]
    return best if n >= 3 else None


def sample(image: Image.Image, seen: dict[str, Counter]) -> bool:
    for region in REGIONS:
        x, y, w, h = region
        if image.width < x + w:
            continue
        mask = _mask(image.crop((x, y, x + w, y + h)).convert("RGB"), "white")
        shapes = glyphs_of(mask)
        if not shapes:
            continue
        label = consensus(mask)
        digit_shapes = [g for g in shapes if g.kind == "digit"]
        if label is None or len(label) != len(digit_shapes):
            continue
        for ch, g in zip(label, digit_shapes):
            seen[ch][g.key] += 1
        return True
    return False


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    seen: dict[str, Counter] = defaultdict(Counter)
    frames = 0
    fixtures = sorted((Path(__file__).resolve().parents[1] / "tests" / "fixtures").glob("frame_1440_*.png"))
    for p in fixtures:
        frames += sample(Image.open(p).convert("RGB"), seen)
    print(f"фікстури: {frames} кадрів із читаною панеллю")
    if seconds > 0:
        from app.capture.win32 import Win32WindowCapture
        from app.capture.window_finder import WindowMatch, find_windows
        caps = [Win32WindowCapture(h) for h in find_windows(WindowMatch())]
        end = time.time() + seconds
        while time.time() < end:
            for cap in caps:
                try:
                    frames += sample(cap.grab().convert("RGB"), seen)
                except Exception as exc:                       # noqa: BLE001
                    print("кадр:", exc)
            time.sleep(0.3)
    out = {d: [{"key": k, "n": n} for k, n in c.most_common()] for d, c in sorted(seen.items())}
    GLYPH_FILE.parent.mkdir(parents=True, exist_ok=True)
    Path(str(GLYPH_FILE) + ".raw").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("цифр, які бачили:", "".join(sorted(out)) or "—")
    for d, items in sorted(out.items()):
        print(d, [(i["n"]) for i in items])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
