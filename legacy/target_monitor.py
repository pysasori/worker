"""
Моніторинг HP цілі у фоновому вікні PW і зміна цілі, коли моб помер.

Як це бачить бот (усе в клієнтських координатах вікна 1417x1076):
  - Рамка цілі малюється вгорі по центру. Її x-позиція і розмір плавають
    залежно від стилю/вмісту (бачив два варіанти: бар на рядках 8..18 з лівим
    краєм x=618, і бар на рядках 11..15 з лівим краєм x=595). Тому нічого не
    прив'язуємо до точних координат, а скануємо смугу рядків BAR_ROWS.
  - HP = довжина червоного відрізка (≈194,46,34), що починається в BAR_X0_RANGE.
    Текст "124/124" поверх бару робить розриви до ~17 px — зливаємо (RED_GAP).
    Повну ширину бару запам'ятовуємо як максимум баченого для поточної цілі.
  - Червоного немає (або ≤ DEAD_MAX_RED px) = моб помер або цілі нема.
    В обох випадках дія одна: Tab. Розрізняти "рамка є з порожнім баром" від
    "рамки нема" по пікселях ненадійно (нічне небо дає такі ж рівні темні смуги),
    і на поведінку це не впливає — тому не розрізняємо.

Запуск:
  python target_monitor.py            # F1 кожні 3с поки ціль жива; помер -> F2 x2 (лут) -> Tab
  python target_monitor.py --dry      # тільки друкує, що бачить, клавіші не шле
  python target_monitor.py --attack f1 --interval 2 --loot f2 --loot-times 3
Зупинка: Ctrl+C. Бот працює і коли гра активна; --pause-fg ставить його на паузу, поки ти в грі.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

from PIL import Image

from bg_window import BgWindow
from pet_monitor import PetGuard

# ---- налаштування (settings.json поруч зі скриптом) --------------------------
import json
import os

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
with open(SETTINGS_PATH, encoding="utf-8") as _f:
    SETTINGS = json.load(_f)

CLIENT_SIZE = tuple(SETTINGS["client_size"])
_tf = SETTINGS["target_frame"]
REGION = tuple(_tf["region"])               # вирізка вгорі вікна, де живе HP-бар цілі
BAR_ROWS = range(*_tf["bar_rows"])          # рядки, в яких шукаємо червоний бар (обидва стилі рамки)
BAR_X0_RANGE = tuple(_tf["bar_x0_range"])   # лівий край бару має бути тут (відсікає червоне зі світу)
RED_GAP = _tf["red_gap"]                    # px: дозволений розрив між пікселями бару/тексту
BRIGHT_MIN = _tf["bright_min"]              # min(R,G,B) вище цього = піксель тексту HP
DEAD_MAX_RED = _tf["dead_max_red"]          # px червоного і менше = вважаємо, що HP=0
KEYS = SETTINGS["keys"]
PERIODIC = {k: float(v) for k, v in SETTINGS.get("periodic", {}).items() if not k.startswith("_")}


def _is_red(p) -> bool:
    return p[0] > 150 and p[1] < 100 and p[2] < 100


def _is_bright(p) -> bool:
    """Білий текст HP ("380/380") поверх бару і його антиаліасинг."""
    return min(p[:3]) > BRIGHT_MIN


def _red_bar(px, y: int, w: int, ox: int) -> tuple[int, int]:
    """
    (довжина, x0) бару в рядку: відрізок, що ПОЧИНАЄТЬСЯ червоним пікселем у BAR_X0_RANGE,
    продовжується червоними або світлими (текст) пікселями з розривами <= RED_GAP,
    а довжина міряється до останнього ЧЕРВОНОГО. Тому текст не рве бар і не подовжує його.
    """
    runs: list[list[int]] = []  # [x0, start_is_red, last_red, last_any]
    for x in range(w):
        p = px[x, y]
        red = _is_red(p)
        if red or _is_bright(p):
            if runs and x - runs[-1][3] <= RED_GAP:
                runs[-1][3] = x
                if red:
                    runs[-1][2] = x
            else:
                runs.append([x, red, x if red else -1, x])
    runs = [r for r in runs if r[1] and BAR_X0_RANGE[0] <= r[0] + ox <= BAR_X0_RANGE[1]]
    if not runs:
        return 0, -1
    x0, _, x1, _ = max(runs, key=lambda r: r[2] - r[0])
    return x1 - x0 + 1, x0 + ox


def _red_count(px, y: int, w: int) -> int:
    return sum(_is_red(px[x, y]) for x in range(w))


@dataclass
class TargetState:
    red_len: int            # довжина червоного бару, px (0 = порожній/немає)
    bar_x0: int             # лівий край бару (клієнтські координати) або -1
    hp: float | None = None  # 0..1 якщо відома повна ширина, інакше None

    @property
    def alive(self) -> bool:
        return self.red_len > DEAD_MAX_RED


def analyze(img: Image.Image, full_width: int | None = None) -> TargetState:
    """img — вирізка REGION."""
    px = img.load()
    w, _ = img.size
    ox, oy = REGION[0], REGION[1]
    # рядок бару = той, де найбільше червоного (світлі рамки бару так не виграють)
    y = max(BAR_ROWS, key=lambda yy: _red_count(px, yy - oy, w))
    red_len, x0 = _red_bar(px, y - oy, w, ox)
    hp = min(1.0, red_len / full_width) if full_width else None
    return TargetState(red_len, x0, hp)


def run(win: BgWindow, attack: str | None, interval: float, dry: bool,
        pause_fg: bool, loot: str | None = "f2", loot_times: int = 2,
        loot_delay: float = 0.5, poll: float = 0.12, pet: bool = True,
        periodic: dict[str, float] | None = None) -> None:
    guard = PetGuard(win, dry=dry) if pet else None
    periodic = periodic or {}
    periodic_last = {k: 0.0 for k in periodic}  # 0 = натиснути одразу на старті
    full_width: int | None = None
    last_attack = 0.0
    last_tab = 0.0
    was_alive = False
    alive_streak = 0  # скільки кадрів підряд бачимо живий бар (захист від шуму в 1 кадр)
    print(f"hwnd={win.hwnd} {win.title!r} attack={attack} interval={interval}s loot={loot} dry={dry}")
    if win.client_size != CLIENT_SIZE:
        print(f"!!! розмір вікна {win.client_size}, а settings.json калібровано під {CLIENT_SIZE}. "
              f"Координати рамки цілі можуть не збігтись — постав у грі {CLIENT_SIZE[0]}x{CLIENT_SIZE[1]}.")

    def log(msg: str) -> None:
        print(f"\n[{time.strftime('%H:%M:%S')}] {msg}")

    while True:
        now = time.time()
        st = analyze(win.screenshot(REGION), full_width)

        if pause_fg and win.is_foreground():
            print(f"\r[пауза: гра активна] red={st.red_len}px   ", end="")
            was_alive = False
            time.sleep(poll)
            continue

        pet_txt = ""
        if guard:
            guard.tick()  # піт лікується незалежно від стану цілі
            pet_txt = f" | {guard.status()}"

        for key, every in periodic.items():  # клавіші по таймеру, незалежно від цілі
            if now - periodic_last[key] >= every:
                log(f"по таймеру ({every:.0f}с) -> {key}")
                if not dry:
                    win.press(key)
                periodic_last[key] = now
                time.sleep(0.1)

        alive_streak = alive_streak + 1 if st.alive else 0
        if st.alive and not was_alive and alive_streak < 2:
            # нова ціль: підтверджуємо одразу другим кадром (захист від шуму в 1 кадр),
            # а не чекаємо наступного опитування — щоб F1 йшов без затримки після Tab
            time.sleep(0.03)
            st = analyze(win.screenshot(REGION), full_width)
            alive_streak = 2 if st.alive else 0

        if not st.alive:
            if was_alive:
                log("моб помер (HP=0)")
                was_alive = False
                full_width = None
                time.sleep(0.2)  # дати клієнту зняти труп, інакше Tab може взяти його ж
                if loot:
                    log(f"збір лута: {loot} x{loot_times}")
                    for _ in range(loot_times):
                        if not dry:
                            win.press(loot)
                        time.sleep(loot_delay)
            if now - last_tab > 1.0:
                log(("немає цілі" if full_width is None else "ціль зникла") + " -> Tab")
                if not dry:
                    win.press("tab")
                last_tab = time.time()
                full_width = None
            time.sleep(poll)
            continue

        # ціль жива
        if not was_alive:
            log(f"нова ціль: бар {st.red_len}px від x={st.bar_x0}")
            was_alive = True
            last_attack = 0.0  # бити одразу
        if full_width is None or st.red_len > full_width:
            full_width = st.red_len
            st.hp = 1.0
        print(f"\rHP {st.hp:4.0%} ({st.red_len}/{full_width}px){pet_txt}   ", end="")
        if attack and now - last_attack >= interval:
            if not dry:
                win.press(attack)
            last_attack = now
        time.sleep(poll)


def main() -> None:
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    k = KEYS  # значення за замовчуванням беруться з settings.json
    ap.add_argument("--attack", default=k["attack"], help=f"клавіша атаки, '' щоб не атакувати (default {k['attack']})")
    ap.add_argument("--interval", type=float, default=k["attack_interval"], help=f"секунд між натисканнями атаки (default {k['attack_interval']})")
    ap.add_argument("--loot", default=k["loot"], help=f"клавіша збору лута після смерті моба, '' щоб не збирати (default {k['loot']})")
    ap.add_argument("--loot-times", type=int, default=k["loot_times"], help=f"скільки разів тиснути лут (default {k['loot_times']})")
    ap.add_argument("--loot-delay", type=float, default=k["loot_delay"], help=f"пауза між натисканнями луту, с (default {k['loot_delay']})")
    ap.add_argument("--dry", action="store_true", help="тільки спостерігати, клавіші не слати")
    ap.add_argument("--pause-fg", action="store_true",
                    help="ставити бота на паузу, поки гра активна (за замовчуванням працює і тоді)")
    ap.add_argument("--no-pet", action="store_true", help="не стежити за HP піта (див. pet_monitor.py)")
    ap.add_argument("--periodic", action="append", metavar="KEY:SEC",
                    help="клавіша по таймеру, напр. --periodic 1:60 (можна кілька разів). "
                         f"За замовчуванням з settings.json: {PERIODIC or 'нема'}")
    ap.add_argument("--no-periodic", action="store_true", help="вимкнути клавіші по таймеру")
    a = ap.parse_args()
    periodic = dict(PERIODIC)
    for item in a.periodic or []:
        key, _, sec = item.partition(":")
        periodic[key] = float(sec or 60)
    if a.no_periodic:
        periodic = {}
    win = BgWindow.pw()
    try:
        run(win, a.attack or None, a.interval, a.dry, pause_fg=a.pause_fg,
            loot=a.loot or None, loot_times=a.loot_times, loot_delay=a.loot_delay, pet=not a.no_pet,
            periodic=periodic)
    except KeyboardInterrupt:
        print("\nстоп")


if __name__ == "__main__":
    main()
