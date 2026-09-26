"""
Моніторинг HP піта у фоновому вікні PW: HP нижче порогу -> клавіша лікування (F3).

Рамка піта стоїть під рамкою персонажа, зліва вгорі, і не плаває (на відміну
від рамки цілі), тому координати фіксовані (settings.json -> "pet"):
  - червоний HP-бар: рядки ~190..191, починається з x=46, повна ширина 82 px;
  - порожня частина бару темна (≈26,46,49). Якщо в рядку нема ні червоного, ні
    цього темного тла — рамки піта нема (піт не викликаний), нічого не тиснемо.

Окремий запуск:
  python pet_monitor.py            # F3 коли HP піта < 50%
  python pet_monitor.py --dry      # тільки друкує HP піта
  python pet_monitor.py --below 0.6 --heal f4
В target_monitor.py підключений автоматично (вимикається --no-pet).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass

from PIL import Image

from bg_window import BgWindow

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
with open(SETTINGS_PATH, encoding="utf-8") as _f:
    PET = json.load(_f)["pet"]

REGION = tuple(PET["region"])          # вирізка з HP-баром піта
BAR_ROWS = range(*PET["bar_rows"])     # рядки, серед яких шукаємо бар
BAR_X0 = PET["bar_x0"]                 # лівий край бару (клієнтські координати)
BAR_WIDTH = PET["bar_width"]           # повна ширина бару, px
EMPTY_BG = tuple(PET["empty_bg"])      # колір порожньої частини бару
EMPTY_TOL = PET["empty_tol"]


def _is_red(p) -> bool:
    return p[0] > 150 and p[1] < 100 and p[2] < 100


def _is_empty_bg(p) -> bool:
    return all(abs(p[i] - EMPTY_BG[i]) <= EMPTY_TOL for i in range(3))


@dataclass
class PetState:
    present: bool      # рамка піта на екрані
    red_len: int       # px червоного в барі
    hp: float          # 0..1 (0 якщо рамки нема)


def analyze_pet(img: Image.Image) -> PetState:
    """img — вирізка REGION."""
    px = img.load()
    ox, oy = REGION[0], REGION[1]
    x_start = BAR_X0 - ox
    best_red, best_empty = 0, 0
    for y in BAR_ROWS:
        yy = y - oy
        red = empty = 0
        for x in range(x_start, x_start + BAR_WIDTH):
            p = px[x, yy]
            if _is_red(p):
                red += 1
            elif _is_empty_bg(p):
                empty += 1
        if red + empty > best_red + best_empty:
            best_red, best_empty = red, empty
    # рамка є, якщо бар (червоне + порожнє тло) заповнює більшість своєї ширини
    present = (best_red + best_empty) >= BAR_WIDTH * 0.7
    hp = min(1.0, best_red / BAR_WIDTH) if present else 0.0
    return PetState(present, best_red, hp)


class PetGuard:
    """Викликати tick() з будь-якого циклу; сам стежить за cooldown."""

    def __init__(self, win: BgWindow, heal: str | None = None, below: float | None = None,
                 cooldown: float | None = None, dry: bool = False) -> None:
        self.win = win
        self.heal = PET["heal_key"] if heal is None else heal
        self.below = PET["heal_below"] if below is None else below
        self.cooldown = PET["heal_cooldown"] if cooldown is None else cooldown
        self.dry = dry
        self.last_heal = 0.0
        self.state = PetState(False, 0, 0.0)

    def tick(self) -> PetState:
        st = analyze_pet(self.win.screenshot(REGION))
        self.state = st
        now = time.time()
        if (self.heal and st.present and 0 < st.hp < self.below
                and now - self.last_heal >= self.cooldown):
            print(f"\n[{time.strftime('%H:%M:%S')}] піт HP {st.hp:.0%} < {self.below:.0%} -> {self.heal}")
            if not self.dry:
                self.win.press(self.heal)
            self.last_heal = now
        return st

    def status(self) -> str:
        st = self.state
        return f"піт {st.hp:.0%}" if st.present else "піта нема"


def main() -> None:
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--heal", default=PET["heal_key"], help=f"клавіша лікування піта (default {PET['heal_key']})")
    ap.add_argument("--below", type=float, default=PET["heal_below"], help=f"лікувати, коли HP нижче (default {PET['heal_below']})")
    ap.add_argument("--cooldown", type=float, default=PET["heal_cooldown"], help="мінімум секунд між лікуваннями")
    ap.add_argument("--dry", action="store_true", help="тільки спостерігати")
    a = ap.parse_args()
    win = BgWindow.pw()
    guard = PetGuard(win, a.heal, a.below, a.cooldown, a.dry)
    print(f"hwnd={win.hwnd} heal={a.heal} below={a.below:.0%} dry={a.dry}")
    try:
        while True:
            st = guard.tick()
            print(f"\r{guard.status()} ({st.red_len}/{BAR_WIDTH}px)   ", end="")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nстоп")


if __name__ == "__main__":
    main()
