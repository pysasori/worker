"""
Нічний монітор: раз на 10 с питає бота, як справи, і раз на 5 хвилин пише зведення
в logs/monitor.log. Окремо фіксує тривоги: бот стоїть, кадри не рухаються, пета
нема довго, повернення не вдалось, помилки в лозі.

    .venv\Scripts\python tools\monitor.py --hours 12
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "monitor.log"
BOT_LOG = ROOT / "logs" / "web.out.log"


def state(base: str) -> dict:
    with urllib.request.urlopen(base + "/api/state", timeout=10) as r:
        return json.loads(r.read().decode())


def write(line: str) -> None:
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%H:%M:%S} {line}\n")


def bot_events(since: int) -> tuple[list[str], int]:
    try:
        lines = BOT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], since
    if len(lines) < since:           # лог почали наново (перезапуск сервера)
        since = 0
    return lines[since:], len(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--hours", type=float, default=12)
    ap.add_argument("--every", type=float, default=300)
    args = ap.parse_args()
    end = time.time() + args.hours * 3600
    write(f"монітор стартував на {args.hours:g} год")
    seen = len(BOT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()) if BOT_LOG.exists() else 0
    window_start = time.time()
    fps: list[float] = []
    pet_missing_since = None
    last_tick = None
    frozen = 0
    while time.time() < end:
        try:
            s = state(args.base)
            w = s["windows"][0]
            p = w["pipelines"]
            fps.append(w["fps"])
            if not s["running"]:
                write(f"!! бот не працює (вручну: {s.get('stopped_by_user')})")
            if w["tick"] == last_tick:
                frozen += 1
                if frozen == 3:
                    write("!! кадри не рухаються 30 с")
            else:
                frozen = 0
            last_tick = w["tick"]
            if "піта нема" in (p.get("pet_heal") or ""):
                pet_missing_since = pet_missing_since or time.time()
                if time.time() - pet_missing_since > 180:
                    write(f"!! пета нема {time.time() - pet_missing_since:.0f} с · {p.get('pet_summon')}")
                    pet_missing_since = time.time()
            else:
                pet_missing_since = None
        except Exception as e:  # монітор не має падати
            write(f"!! немає відповіді: {e}")
        if time.time() - window_start >= args.every:
            new, seen = bot_events(seen)
            kinds = Counter(l.split("] [")[1].split("]")[0] for l in new if "] [" in l)
            alarms = [l for l in new if "!!" in l or "Traceback" in l or "ERROR" in l]
            kills = sum(1 for l in new if "ціль мертва" in l)
            back = sum(1 for l in new if "повернувся на місце" in l or "запам'ятав" in l)
            try:
                p = state(args.base)["windows"][0]["pipelines"]
                snap = f"{p.get('pet_heal', '')} · {p.get('pet_feed', '')} · {p.get('position', '')}"
            except Exception:
                snap = "?"
            avg = sum(fps) / len(fps) if fps else 0
            write(f"зведення: убито {kills}, повернень {back}, fps {avg:.1f} (мін {min(fps) if fps else 0:.1f}), "
                  f"події {dict(kinds)} | {snap}")
            for a in alarms[-5:]:
                write(f"   {a}")
            fps.clear()
            window_start = time.time()
        time.sleep(10)
    write("монітор завершився")


if __name__ == "__main__":
    main()
