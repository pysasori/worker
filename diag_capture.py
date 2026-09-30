"""
Діагностика захоплення вікна на цій машині — окремо від бота, без запуску сервера.

Навіщо: на одній з нод бот бачить вікно гри (EnumWindows знаходить), але кадр
узяти не може («вікно гри недоступне»). Причин кілька — вікно раптом не те,
клієнтська область нульова, DWM-композиція вимкнена, або процес бота й процес
гри сидять у РІЗНИХ Windows-сесіях (GetDC/BitBlt між сесіями не працюють,
хоч EnumWindows вікно все одно бачить). Цей скрипт перевіряє все це по черзі
й каже прямо, а не змушує здогадуватись по колу «недоступне».

Запуск (з теки backend):
    .venv\\Scripts\\python diag_capture.py
"""
from __future__ import annotations

import ctypes
import os
import sys
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import win32gui
import win32process

from app.capture.window_finder import WindowMatch, find_windows
from app.capture.win32 import Win32WindowCapture

_kernel32 = ctypes.windll.kernel32
_dwmapi = ctypes.windll.dwmapi


def my_session_id() -> int | None:
    sid = ctypes.c_ulong()
    if _kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid)):
        return sid.value
    return None


def session_of(pid: int) -> int | None:
    sid = ctypes.c_ulong()
    if _kernel32.ProcessIdToSessionId(pid, ctypes.byref(sid)):
        return sid.value
    return None


def composition_enabled() -> bool | None:
    val = ctypes.c_int()
    hr = _dwmapi.DwmIsCompositionEnabled(ctypes.byref(val))
    return bool(val.value) if hr == 0 else None


def main() -> int:
    print(f"процес бота: pid={os.getpid()}, сесія={my_session_id()}")
    comp = composition_enabled()
    print(f"DWM-композиція увімкнена: {comp}"
          f"{' (!! якщо False — BitBlt і PrintWindow можуть віддавати чорноту)' if comp is False else ''}")

    hwnds = find_windows(WindowMatch())
    if not hwnds:
        print("!! жодного вікна гри не знайдено (EnumWindows нічого не дав) — "
              "перевір, чи запущений ElementClient.exe на ЦІЙ машині/сесії")
        return 1
    print(f"знайдено вікон: {len(hwnds)}")

    for hwnd in hwnds:
        print(f"\n--- hwnd={hwnd} ---")
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        sess = session_of(pid)
        same = sess == my_session_id()
        print(f"заголовок: {win32gui.GetWindowText(hwnd)!r}")
        print(f"клас: {win32gui.GetClassName(hwnd)!r}")
        print(f"pid процесу гри: {pid}, сесія: {sess}"
              f"{'' if same else '  !! ІНША сесія, ніж у бота — тому й недоступне'}")
        print(f"IsWindow={win32gui.IsWindow(hwnd)} IsIconic={win32gui.IsIconic(hwnd)} "
              f"IsWindowVisible={win32gui.IsWindowVisible(hwnd)}")
        try:
            print(f"GetClientRect={win32gui.GetClientRect(hwnd)}")
        except Exception as e:
            print(f"!! GetClientRect впав: {e!r}")

        try:
            img = Win32WindowCapture(hwnd).grab()
            lo, hi = img.convert("L").getextrema()
            out = f"diag_frame_{hwnd}.png"
            img.save(out)
            print(f"grab() OK: {img.size}, яскравість {lo}-{hi}"
                  f"{'  !! ЧОРНИЙ кадр' if hi <= 8 else ''} -> збережено {out}")
        except Exception:
            print("!! grab() впав:")
            traceback.print_exc()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
