"""
Налаштування самого процесу бота під «ВМ із кількома клієнтами гри».

Клієнти гри й так ледь тягнуть (3-5 кадрів/с), тож бот не має відбирати в них процесор:
  - OpenCV за замовчуванням ділить кожен matchTemplate на всі ядра. На сильному ПК це
    лише втрата (виміряно: 4.6 мс в один потік проти 8.8 мс у всі), на ВМ із кількома
    вікнами потоки різних вікон ще й товчуться між собою. Один потік на виклик швидший,
    а паралелізм дають самі вікна.
  - пріоритет нижче за звичайний: коли процесора не вистачає, лагати має бот (йому
    5 кадрів/с вистачає), а не гра. Підпроцеси (tesseract) успадковують пріоритет.
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger("bot")

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


def tune_process(low_priority: bool = True, cv_threads: int = 1) -> None:
    try:
        import cv2
        cv2.setNumThreads(cv_threads)
    except Exception:           # OpenCV без цього виклику — не критично
        pass
    if low_priority and sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            # без явних типів 64-бітний HANDLE обрізається до int і виклик мовчки не діє
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel32.SetPriorityClass.restype = wintypes.BOOL
            if not kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS):
                log.warning("не вдалось знизити пріоритет процесу (код %s)", ctypes.get_last_error())
        except Exception:
            pass
