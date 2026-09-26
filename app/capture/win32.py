"""
Захоплення кадру вікна без його активації.

BitBlt копіює поверхню вікна з композитора (DWM) і НІЧОГО вікну не шле — гра не моргає,
~15 мс на кадр 1440x1080. PrintWindow(PW_RENDERFULLCONTENT) теж працює, але шле вікну
WM_PRINT (примусове перемальовування), від чого клієнт помітно смикається — тому він
лише запасний варіант, коли BitBlt віддав порожній (чорний) кадр.

Обмеження: вікно має бути НЕ згорнуте (згорнуте не рендериться) і не в fullscreen exclusive.
"""
from __future__ import annotations

import ctypes
import time

import win32con
import win32gui
import win32ui
from PIL import Image

from app.capture.abstract import CapturePort
from app.core.exceptions import CaptureError, WindowGoneError
from app.core.settings import settings

_user32 = ctypes.windll.user32
_PW_CLIENTONLY = 0x1
_PW_RENDERFULLCONTENT = 0x2
_BLACK_MAX_LUM = 8


class Win32WindowCapture(CapturePort):
    def __init__(self, hwnd: int, name: str = "window", retries: int | None = None) -> None:
        self.hwnd = hwnd
        self.name = name
        self.retries = settings.CAPTURE_RETRIES if retries is None else retries

    # ---- інфо ---------------------------------------------------------------
    def client_size(self) -> tuple[int, int]:
        _, _, w, h = win32gui.GetClientRect(self.hwnd)
        return w, h

    def is_available(self) -> bool:
        return bool(win32gui.IsWindow(self.hwnd) and not win32gui.IsIconic(self.hwnd))

    # ---- захоплення ---------------------------------------------------------
    def grab(self) -> Image.Image:
        if not win32gui.IsWindow(self.hwnd):
            raise WindowGoneError(self.name, "закрите")
        if win32gui.IsIconic(self.hwnd):
            raise WindowGoneError(self.name, "згорнуте")
        last: Image.Image | None = None
        for attempt in range(max(1, self.retries)):
            last = self._grab_once(use_print_window=attempt > 0)
            if last.convert("L").getextrema()[1] > _BLACK_MAX_LUM:
                return last
            time.sleep(0.03)
        if last is None:
            raise CaptureError(f"{self.name}: кадр не отримано")
        return last  # усі спроби чорні — віддаємо як є, детектори побачать порожнечу

    def _grab_once(self, use_print_window: bool = False) -> Image.Image:
        w, h = self.client_size()
        if w == 0 or h == 0:
            raise WindowGoneError(self.name, "порожня клієнтська область")
        hwnd_dc = win32gui.GetDC(self.hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        try:
            if use_print_window:
                _user32.PrintWindow(self.hwnd, save_dc.GetSafeHdc(), _PW_CLIENTONLY | _PW_RENDERFULLCONTENT)
            else:
                save_dc.BitBlt((0, 0), (w, h), mfc_dc, (0, 0), win32con.SRCCOPY)
            info = bmp.GetInfo()
            return Image.frombuffer(
                "RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        finally:
            win32gui.DeleteObject(bmp.GetHandle())
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(self.hwnd, hwnd_dc)
