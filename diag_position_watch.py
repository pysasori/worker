"""
Стежить за роботою справжнього PositionPipeline кілька секунд поспіль, із
конфігом, узятим з РЕАЛЬНОГО профілю персонажа (а не з заводських дефолтів —
саме на цьому спіткнулась перша версія скрипта: профіль під 1280x720 зсуває
зону цифр, а голий PositionConfig() — ні, і показував порожні читання там,
де насправді все працює).

Запуск (з теки backend, персонаж на екрані, живий):
    .venv\\Scripts\\python diag_position_watch.py <нік> [секунд] [hwnd]

Приклад:
    .venv\\Scripts\\python diag_position_watch.py BroWorker 15
"""
from __future__ import annotations

import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.capture.window_finder import WindowMatch, find_windows
from app.capture.win32 import Win32WindowCapture
from app.pipelines.base import Frame, PipelineContext
from app.pipelines.position import PositionConfig, PositionPipeline
from app.pipelines.registry import get_pipeline_class
from app.vision.digits import read_numbers


def load_position_config(nick: str) -> PositionConfig:
    raw = json.load(open("config/windows.json", encoding="utf-8"))
    char = raw["characters"].get(nick)
    if char is None:
        print(f"!! персонажа {nick!r} нема в config/windows.json — перевір назву (чутливо до регістру)")
        raise SystemExit(1)
    profile = raw["profiles"][char["profile"]]
    spec = next((s for s in profile["pipelines"] if s["type"] == "position"), None)
    if spec is None:
        print(f"!! у профілі {char['profile']!r} нема блока «Координати»")
        raise SystemExit(1)
    merged = {**spec["config"], **char.get("overrides", {}).get("position", {})}
    print(f"профіль: {char['profile']!r}, client_size={profile.get('client_size')}")
    cfg = get_pipeline_class("position").config_model(**merged)
    print(f"зона цифр з профілю: {cfg.digits.region.box}")
    return cfg


def main() -> int:
    if len(sys.argv) < 2:
        print("використання: diag_position_watch.py <нік> [секунд] [hwnd]")
        return 1
    nick = sys.argv[1]
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0
    hwnd = int(sys.argv[3]) if len(sys.argv) > 3 else None

    cfg = load_position_config(nick)

    if hwnd is None:
        found = find_windows(WindowMatch())
        if not found:
            print("!! жодного вікна гри не знайдено")
            return 1
        hwnd = found[0]
    print(f"вікно: hwnd={hwnd}, стежу {seconds:.0f}с (читання раз на {cfg.read_every}с, як у боті)")

    cap = Win32WindowCapture(hwnd)
    pipe = PositionPipeline(cfg)
    shared: dict = {}

    start = time.time()
    tick = 0
    while time.time() - start < seconds:
        tick += 1
        image = cap.grab()
        raw = read_numbers(image, cfg.digits, cfg.ocr)
        ctx = PipelineContext(window="diag", frame=Frame(image=image, ts=time.time()), shared=shared)
        pipe.process(ctx)
        print(f"[{tick:>2}] сире OCR={raw}  ->  pos={pipe.pos}  "
              f"candidate={pipe.candidate}  jump={pipe.jump}({pipe.jump_seen})  misses={pipe.misses}")
        time.sleep(max(0.05, cfg.read_every))

    print(f"\nпідсумок: pos.known={pipe.pos.known}"
          f"{f' -> {pipe.pos}' if pipe.pos.known else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
