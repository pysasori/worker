"""
Ввід у вікно через віконні повідомлення (PostMessage) — те саме, що робить UOPilot.

Реальні миша й клавіатура не задіяні, активне вікно не змінюється, тому можна працювати
у фоні і паралельно робити своє.

Особливість клієнта PW 1.3.6: WM_KEYDOWN він ігнорує, поки вважає себе неактивним. Тому
перед клавішею шлемо фейкові WM_ACTIVATEAPP/WM_ACTIVATE/WM_SETFOCUS — гра думає, що активна,
а справжній фокус лишається у твого вікна. Миша працює і без цього.
НЕ використовувати AttachThreadInput + SetFocus: воно витягує гру на передній план.
"""
from __future__ import annotations

import ctypes
import time

import win32api
import win32con
import win32gui

from app.input.abstract import InputPort

_user32 = ctypes.windll.user32

VK: dict[str, int] = {
    "tab": win32con.VK_TAB, "enter": win32con.VK_RETURN, "esc": win32con.VK_ESCAPE,
    "space": win32con.VK_SPACE, "shift": win32con.VK_SHIFT, "ctrl": win32con.VK_CONTROL,
    "alt": win32con.VK_MENU, "backspace": win32con.VK_BACK, "delete": win32con.VK_DELETE,
    "up": win32con.VK_UP, "down": win32con.VK_DOWN, "left": win32con.VK_LEFT, "right": win32con.VK_RIGHT,
    "home": win32con.VK_HOME, "end": win32con.VK_END, "pgup": win32con.VK_PRIOR, "pgdn": win32con.VK_NEXT,
    "insert": win32con.VK_INSERT,
    **{f"f{i}": win32con.VK_F1 + i - 1 for i in range(1, 13)},
    **{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"},
}
_EXTENDED = {win32con.VK_INSERT, win32con.VK_DELETE, win32con.VK_HOME, win32con.VK_END,
             win32con.VK_PRIOR, win32con.VK_NEXT, win32con.VK_UP, win32con.VK_DOWN,
             win32con.VK_LEFT, win32con.VK_RIGHT, win32con.VK_RCONTROL, win32con.VK_RMENU}

_BUTTONS = {
    "left": (win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP, win32con.MK_LBUTTON),
    "right": (win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP, win32con.MK_RBUTTON),
    "middle": (win32con.WM_MBUTTONDOWN, win32con.WM_MBUTTONUP, win32con.MK_MBUTTON),
}


def vk_code(key: str | int) -> int:
    if isinstance(key, int):
        return key
    k = str(key).lower()
    if k not in VK:
        raise ValueError(f"невідома клавіша {key!r}")
    return VK[k]


def key_lparam(vk: int, down: bool) -> int:
    lp = 1 | (_user32.MapVirtualKeyW(vk, 0) << 16)
    if vk in _EXTENDED:
        lp |= 1 << 24
    if not down:
        lp |= (1 << 30) | (1 << 31)
    return lp


class Win32MessageInput(InputPort):
    def __init__(self, hwnd: int) -> None:
        self.hwnd = hwnd

    # ---- службове -----------------------------------------------------------
    def is_foreground(self) -> bool:
        return win32gui.GetForegroundWindow() == self.hwnd

    def fake_activate(self) -> None:
        pm = win32api.PostMessage
        pm(self.hwnd, win32con.WM_ACTIVATEAPP, 1, 0)
        pm(self.hwnd, win32con.WM_NCACTIVATE, 1, 0)
        pm(self.hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
        pm(self.hwnd, win32con.WM_SETFOCUS, 0, 0)

    @staticmethod
    def _lp(x: int, y: int) -> int:
        return (y << 16) | (x & 0xFFFF)

    # ---- клавіатура ---------------------------------------------------------
    def key_down(self, key: str | int) -> None:
        vk = vk_code(key)
        if not self.is_foreground():
            self.fake_activate()
        win32api.PostMessage(self.hwnd, win32con.WM_KEYDOWN, vk, key_lparam(vk, True))

    def key_up(self, key: str | int) -> None:
        vk = vk_code(key)
        win32api.PostMessage(self.hwnd, win32con.WM_KEYUP, vk, key_lparam(vk, False))

    def press(self, key: str | int, hold: float = 0.05) -> None:
        self.key_down(key)
        time.sleep(hold)
        self.key_up(key)

    def type_text(self, text: str, delay: float = 0.03) -> None:
        if not self.is_foreground():
            self.fake_activate()
        for ch in text:
            win32api.PostMessage(self.hwnd, win32con.WM_CHAR, ord(ch), 0)
            time.sleep(delay)

    # ---- миша (координати клієнтської області) ------------------------------
    def move(self, x: int, y: int) -> None:
        win32api.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, 0, self._lp(x, y))

    def click(self, x: int, y: int, button: str = "left", hold: float = 0.05) -> None:
        down, up, flag = _BUTTONS[button]
        lp = self._lp(x, y)
        win32api.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, 0, lp)
        time.sleep(0.02)
        win32api.PostMessage(self.hwnd, down, flag, lp)
        time.sleep(hold)
        win32api.PostMessage(self.hwnd, up, 0, lp)

    def double_click(self, x: int, y: int) -> None:
        self.click(x, y)
        time.sleep(0.05)
        lp = self._lp(x, y)
        win32api.PostMessage(self.hwnd, win32con.WM_LBUTTONDBLCLK, win32con.MK_LBUTTON, lp)
        time.sleep(0.05)
        win32api.PostMessage(self.hwnd, win32con.WM_LBUTTONUP, 0, lp)

    def drag(self, x1: int, y1: int, x2: int, y2: int, steps: int = 10, delay: float = 0.02) -> None:
        win32api.PostMessage(self.hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, self._lp(x1, y1))
        for i in range(1, steps + 1):
            x = x1 + (x2 - x1) * i // steps
            y = y1 + (y2 - y1) * i // steps
            win32api.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, win32con.MK_LBUTTON, self._lp(x, y))
            time.sleep(delay)
        win32api.PostMessage(self.hwnd, win32con.WM_LBUTTONUP, 0, self._lp(x2, y2))

    def scroll(self, x: int, y: int, ticks: int) -> None:
        sx, sy = win32gui.ClientToScreen(self.hwnd, (x, y))
        win32api.PostMessage(self.hwnd, win32con.WM_MOUSEWHEEL,
                             (ticks * win32con.WHEEL_DELTA) << 16, self._lp(sx, sy))
