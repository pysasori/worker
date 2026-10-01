r"""
Чому бот не бачить ціль: знімає кадр, читає рамку цілі з РЕАЛЬНОГО профілю
персонажа (target_search) і показує смужку HP та колір назви (моб/свій).
Виділи моба в грі (Tab) і запусти: .venvScripts\python diag_target.py [нік] [hwnd]
Зберігає diag_target_<hwnd>.png (кадр) і diag_target_bar_<hwnd>.png (зона рамки x3).
"""
from __future__ import annotations

import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.capture.win32 import Win32WindowCapture
from app.capture.window_finder import WindowMatch, find_windows
from app.pipelines.registry import get_pipeline_class
from app.vision.bars import scan_bar
from app.vision.nameplate import classify_target, name_region
from app.vision.nick import NickConfig, read_nick


def main() -> int:
    args = sys.argv[1:]
    nick_arg = args[0] if args and not args[0].isdigit() else None
    hwnds = [int(a) for a in args if a.isdigit()] or find_windows(WindowMatch())
    raw = json.load(open("config/windows.json", encoding="utf-8"))
    for hwnd in hwnds:
        print(f"\n--- hwnd={hwnd} ---")
        image = Win32WindowCapture(hwnd).grab()
        print(f"кадр {image.size}")
        nick = nick_arg or read_nick(image, NickConfig())
        char = raw["characters"].get(nick)
        print(f"нік: {nick!r}, профіль: {char and char['profile']!r}")
        if not char:
            continue
        spec = next(s for s in raw["profiles"][char["profile"]]["pipelines"] if s["type"] == "target_search")
        cfg = get_pipeline_class("target_search").config_model(
            **{**spec["config"], **char.get("overrides", {}).get("target_search", {})})
        print(f"зона рамки: {cfg.region.box}, rows={cfg.bar.rows}, min_span={cfg.bar.min_span}")
        crop = image.crop(cfg.region.box_xyxy if hasattr(cfg.region, "box_xyxy") else
                          (cfg.region.x, cfg.region.y, cfg.region.x + cfg.region.w, cfg.region.y + cfg.region.h))
        crop.resize((crop.width * 3, crop.height * 3)).save(f"diag_target_bar_{hwnd}.png")
        image.save(f"diag_target_{hwnd}.png")
        r = scan_bar(crop, cfg.region, cfg.bar)
        print(f"смужка: {r}  present={r.present} x0={r.x0} row={r.row}")
        if r.present:
            nr = name_region(r.x0, r.filled, r.row, cfg.hostile_check)
            kind, warm, cool = classify_target(image.crop((nr.x, nr.y, nr.x + nr.w, nr.y + nr.h)), cfg.hostile_check)
            print(f"назва: {kind.value}, теплих={warm}, холодних={cool} (треба >= {cfg.hostile_check.min_pixels})")
        else:
            print("!! рамку цілі не знайдено — дивись diag_target_bar_*.png (чи є в зоні червона смужка?)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
