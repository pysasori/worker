"""
Читання смужок HP. Чисті функції над зображенням — без вікон, клавіш і станів,
тому легко тестуються на збережених кадрах (див. tests/).

Дві стратегії:
  scan_bar  — смужка плаває по екрану (рамка цілі центрується і зсувається залежно
              від довжини назви моба), тому скануємо рядки і шукаємо червоний відрізок,
              що починається у заданому діапазоні x.
  fixed_bar — смужка на фіксованому місці (рамка піта під рамкою персонажа):
              рахуємо частку червоного у відомому прямокутнику.

Важливо: білий текст "380/380" лежить ПОВЕРХ смужки і рве червоний відрізок навпіл.
Тому світлі пікселі вважаються продовженням смужки, а довжина міряється до останнього
ЧЕРВОНОГО пікселя — інакше повний бар читався б як половина.
"""
from __future__ import annotations

from PIL import Image

from app.core.geometry import Region
from app.vision.colors import is_bright, is_close, is_red
from app.vision.schemas import BarFixedConfig, BarReading, BarScanConfig


def _row_red_count(px, y: int, width: int) -> int:
    return sum(1 for x in range(width) if is_red(px[x, y]))


def _row_scan(px, y: int, width: int, region: Region, cfg: BarScanConfig) -> tuple[int, int, int]:
    """
    (заповнено, вся ширина смужки, x0 у координатах вікна) в одному рядку вирізки.

    Заповнено — до останнього ЧЕРВОНОГО пікселя. Вся ширина рахує ще й порожню
    частину смужки: по ній видно рамку цілі, навіть коли від HP лишилась смужечка
    в три пікселі (моб на 12 HP із 726).
    """
    runs: list[list[int]] = []  # [start, has_red, last_red, last_any]
    for x in range(width):
        p = px[x, y]
        red = is_red(p)
        if not (red or is_bright(p, cfg.bright_min) or is_close(p, cfg.empty_rgb, cfg.empty_tol)):
            continue
        if runs and x - runs[-1][3] <= cfg.gap:
            runs[-1][3] = x
            if red:
                runs[-1][2] = x
        else:
            runs.append([x, 1 if red else 0, x if red else -1, x])
    valid = [r for r in runs if r[1]]
    if cfg.x0_range:                      # обмеження по X — лише якщо задане вручну
        lo, hi = cfg.x0_range
        valid = [r for r in valid if lo <= region.to_window(r[0], 0)[0] <= hi]
    if not valid:
        return 0, 0, -1
    best = max(valid, key=lambda r: r[3] - r[0])
    return best[2] - best[0] + 1, best[3] - best[0] + 1, region.to_window(best[0], 0)[0]


def scan_bar(crop: Image.Image, region: Region, cfg: BarScanConfig,
             total: int | None = None) -> BarReading:
    """Смужка, що плаває. crop — вирізка region із кадру."""
    px = crop.load()
    width, _ = crop.size
    # рядок смужки = той, де найбільше червоного (рамки й текст так не виграють)
    rows = [y for y in range(*cfg.rows) if region.y <= y < region.y + region.h]
    if not rows:
        return BarReading()
    best_row = max(rows, key=lambda y: _row_red_count(px, y - region.y, width))
    filled, span, x0 = _row_scan(px, best_row - region.y, width, region, cfg)
    # ціль є, поки видно рамку: червоне + порожня частина смужки. Без цього моб,
    # добитий майже до нуля, читався як «мертвий», бот кидав його і брав нового
    if filled < cfg.min_filled or span < cfg.min_span:
        return BarReading(present=False, filled=0, total=total or 0, row=best_row)
    return BarReading(present=True, filled=filled, total=total or span, x0=x0, row=best_row)


def _bar_runs(px, y: int, w: int, cfg: BarFixedConfig) -> list[tuple[int, int, int]]:
    """Відрізки «заповнене або порожнє тло» у рядку: (x0, довжина, скільки заповненого)."""
    runs: list[list[int]] = []  # [x0, x1, filled]
    for x in range(w):
        p = px[x, y]
        red = is_close(p, cfg.fill_rgb, cfg.fill_tol) if cfg.fill_rgb else is_red(p)
        if red or is_close(p, cfg.empty_rgb, cfg.empty_tol):
            if runs and x - runs[-1][1] <= 1:
                runs[-1][1] = x
                runs[-1][2] += red
            else:
                runs.append([x, x, 1 if red else 0])
    return [(r[0], r[1] - r[0] + 1, r[2]) for r in runs]


def fixed_bar(crop: Image.Image, region: Region, cfg: BarFixedConfig) -> BarReading:
    """
    Смужка сталого розміру (рамка піта). Шукається за ОЗНАКАМИ, не за координатами:
    у смузі рядків беремо найдовший відрізок «червоне + порожнє тло», ширина якого
    близька до очікуваної. Тому смужка знаходиться навіть якщо інтерфейс зсунувся.

    Порожня частина має свій колір, тому рамку видно й при HP=0. Пастка: рівний темний
    фон (нічне небо) теж підходить під цей колір — рятує вимога саме потрібної ширини.
    """
    px = crop.load()
    w, _ = crop.size
    lo, hi = cfg.width * (1 - cfg.width_tol), cfg.width * (1 + cfg.width_tol)

    best = None  # (x0, length, red, row)
    for y in range(*cfg.rows):
        if not (region.y <= y < region.y + region.h):
            continue
        for x0, length, red in _bar_runs(px, y - region.y, w, cfg):
            if not (lo <= length <= hi):
                continue
            if best is None or red > best[2] or (red == best[2] and length > best[1]):
                best = (x0, length, red, y)

    if best is None:
        return BarReading(present=False, total=cfg.width)
    x0, length, red, row = best
    # відсоток рахуємо від ЗАЯВЛЕНОЇ ширини: знайдений відрізок буває на кілька
    # пікселів довшим за смужку (тло рамки того ж кольору), і 100% ставало б 93%
    return BarReading(present=True, filled=min(red, cfg.width), total=cfg.width,
                      x0=region.to_window(x0, 0)[0], row=row)
