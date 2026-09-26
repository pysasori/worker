"""
Робота з вікном гри у фоні (без активації) — аналог того, що робить UOPilot.

Під капотом UOPilot для "кліку в неактивне вікно" (kleft/kright/send з hwnd) шле
віконні повідомлення WM_LBUTTONDOWN/WM_KEYDOWN напряму у вікно через
PostMessage/SendMessage. Це не чіпає реальну мишу/клавіатуру і не змінює
активне вікно. Скрін фонового вікна — через PrintWindow(PW_RENDERFULLCONTENT),
який просить DWM віддати вміст вікна навіть якщо воно перекрите.

Перевірено на клієнті PW 1.3.6 (ComebackPW), вікно перекрите іншим:
  - клік (WM_LBUTTONDOWN/UP) — працює одразу;
  - клавіші (WM_KEYDOWN/UP) — клієнт ігнорує, поки "думає", що неактивний. Лікується
    фейковими WM_ACTIVATEAPP/WM_ACTIVATE/WM_SETFOCUS перед натисканням (див. fake_activate);
  - скрін через BitBlt з DC вікна — повний кадр ~15 мс, вікну нічого не шлеться.
    PrintWindow(PW_RENDERFULLCONTENT) теж працює (~35 мс), але шле вікну WM_PRINT,
    від чого гра помітно моргає — лишений лише як запасний варіант при чорному кадрі.
  - AttachThreadInput + SetActiveWindow/SetFocus НЕ використовувати: витягує гру на передній план.

Обмеження: гра має бути у віконному режимі (не fullscreen exclusive) і НЕ згорнута.
"""
from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass

import win32api
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image

user32 = ctypes.windll.user32

# ---- віртуальні коди клавіш, які найчастіше потрібні -----------------------
VK = {
    "tab": win32con.VK_TAB, "enter": win32con.VK_RETURN, "esc": win32con.VK_ESCAPE,
    "space": win32con.VK_SPACE, "shift": win32con.VK_SHIFT, "ctrl": win32con.VK_CONTROL,
    "alt": win32con.VK_MENU, "backspace": win32con.VK_BACK, "delete": win32con.VK_DELETE,
    "up": win32con.VK_UP, "down": win32con.VK_DOWN, "left": win32con.VK_LEFT, "right": win32con.VK_RIGHT,
    "home": win32con.VK_HOME, "end": win32con.VK_END, "pgup": win32con.VK_PRIOR, "pgdn": win32con.VK_NEXT,
    "insert": win32con.VK_INSERT,
    **{f"f{i}": win32con.VK_F1 + i - 1 for i in range(1, 13)},
    **{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"},
}
# клавіші з "extended" бітом у lParam
_EXTENDED = {win32con.VK_INSERT, win32con.VK_DELETE, win32con.VK_HOME, win32con.VK_END,
             win32con.VK_PRIOR, win32con.VK_NEXT, win32con.VK_UP, win32con.VK_DOWN,
             win32con.VK_LEFT, win32con.VK_RIGHT, win32con.VK_RCONTROL, win32con.VK_RMENU}


def _vk(key: str | int) -> int:
    if isinstance(key, int):
        return key
    k = key.lower()
    if k not in VK:
        raise ValueError(f"unknown key {key!r}")
    return VK[k]


def _key_lparam(vk: int, down: bool) -> int:
    scan = user32.MapVirtualKeyW(vk, 0)
    lp = 1 | (scan << 16)
    if vk in _EXTENDED:
        lp |= 1 << 24
    if not down:
        lp |= (1 << 30) | (1 << 31)  # previous state = down, transition = release
    return lp


@dataclass
class BgWindow:
    hwnd: int

    # ---------- пошук ----------------------------------------------------------
    @classmethod
    def find(cls, title: str | None = None, cls_name: str | None = None,
             process: str | None = None) -> "BgWindow":
        """Знайти видиме top-level вікно за заголовком (підрядок), класом або ім'ям процесу."""
        pids = set()
        if process:
            import subprocess
            out = subprocess.check_output(
                f'tasklist /FI "IMAGENAME eq {process}" /FO CSV /NH', shell=True).decode("cp866", "ignore")
            pids = {int(line.split('","')[1]) for line in out.splitlines() if line.startswith('"')}
        found: list[int] = []

        def cb(h, _):
            if not win32gui.IsWindowVisible(h):
                return
            if title and title.lower() not in win32gui.GetWindowText(h).lower():
                return
            if cls_name and win32gui.GetClassName(h) != cls_name:
                return
            if pids and win32process.GetWindowThreadProcessId(h)[1] not in pids:
                return
            found.append(h)

        win32gui.EnumWindows(cb, None)
        if not found:
            raise RuntimeError(f"window not found (title={title!r}, class={cls_name!r}, process={process!r})")
        return cls(found[0])

    @classmethod
    def pw(cls) -> "BgWindow":
        """Клієнт Perfect World 1.3.6."""
        return cls.find(cls_name="ElementClient Window", process="ElementClient.exe")

    # ---------- інфо -----------------------------------------------------------
    @property
    def title(self) -> str:
        return win32gui.GetWindowText(self.hwnd)

    @property
    def client_size(self) -> tuple[int, int]:
        _, _, w, h = win32gui.GetClientRect(self.hwnd)
        return w, h

    def is_minimized(self) -> bool:
        return bool(win32gui.IsIconic(self.hwnd))

    def is_foreground(self) -> bool:
        return win32gui.GetForegroundWindow() == self.hwnd

    # ---------- клавіатура (PostMessage, вікно лишається у фоні) ----------------
    def fake_activate(self) -> None:
        """
        Клієнт PW ігнорує WM_KEYDOWN поки вважає себе неактивним (прапорець, який
        він виставляє на WM_ACTIVATEAPP/WM_ACTIVATE/WM_SETFOCUS). Шлемо ці повідомлення
        самі — гра думає, що активна, але реальний фокус/foreground не змінюється.
        Миша (клік) працює і без цього.
        """
        pm = win32api.PostMessage
        pm(self.hwnd, win32con.WM_ACTIVATEAPP, 1, 0)
        pm(self.hwnd, win32con.WM_NCACTIVATE, 1, 0)
        pm(self.hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
        pm(self.hwnd, win32con.WM_SETFOCUS, 0, 0)

    def key_down(self, key: str | int) -> None:
        vk = _vk(key)
        if not self.is_foreground():
            self.fake_activate()
        win32api.PostMessage(self.hwnd, win32con.WM_KEYDOWN, vk, _key_lparam(vk, True))

    def key_up(self, key: str | int) -> None:
        vk = _vk(key)
        win32api.PostMessage(self.hwnd, win32con.WM_KEYUP, vk, _key_lparam(vk, False))

    def press(self, key: str | int, hold: float = 0.05) -> None:
        """Натиснути й відпустити. hold — скільки тримати, деякі ігри ігнорують <30мс."""
        self.key_down(key)
        time.sleep(hold)
        self.key_up(key)

    def type_text(self, text: str, delay: float = 0.03) -> None:
        """Ввести текст через WM_CHAR (для чату/полів вводу)."""
        if not self.is_foreground():
            self.fake_activate()
        for ch in text:
            win32api.PostMessage(self.hwnd, win32con.WM_CHAR, ord(ch), 0)
            time.sleep(delay)

    # ---------- миша (координати КЛІЄНТСЬКОЇ області вікна) --------------------
    @staticmethod
    def _lp(x: int, y: int) -> int:
        return (y << 16) | (x & 0xFFFF)

    def move(self, x: int, y: int) -> None:
        win32api.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, 0, self._lp(x, y))

    def click(self, x: int, y: int, button: str = "left", hold: float = 0.05) -> None:
        down, up, flag = {
            "left": (win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP, win32con.MK_LBUTTON),
            "right": (win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP, win32con.MK_RBUTTON),
            "middle": (win32con.WM_MBUTTONDOWN, win32con.WM_MBUTTONUP, win32con.MK_MBUTTON),
        }[button]
        lp = self._lp(x, y)
        win32api.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, 0, lp)
        time.sleep(0.02)
        win32api.PostMessage(self.hwnd, down, flag, lp)
        time.sleep(hold)
        win32api.PostMessage(self.hwnd, up, 0, lp)

    def double_click(self, x: int, y: int) -> None:
        self.click(x, y)
        time.sleep(0.05)
        win32api.PostMessage(self.hwnd, win32con.WM_LBUTTONDBLCLK, win32con.MK_LBUTTON, self._lp(x, y))
        time.sleep(0.05)
        win32api.PostMessage(self.hwnd, win32con.WM_LBUTTONUP, 0, self._lp(x, y))

    def drag(self, x1: int, y1: int, x2: int, y2: int, steps: int = 10, delay: float = 0.02) -> None:
        win32api.PostMessage(self.hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, self._lp(x1, y1))
        for i in range(1, steps + 1):
            x = x1 + (x2 - x1) * i // steps
            y = y1 + (y2 - y1) * i // steps
            win32api.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, win32con.MK_LBUTTON, self._lp(x, y))
            time.sleep(delay)
        win32api.PostMessage(self.hwnd, win32con.WM_LBUTTONUP, 0, self._lp(x2, y2))

    def scroll(self, x: int, y: int, ticks: int) -> None:
        """ticks > 0 — вгору. WM_MOUSEWHEEL хоче ЕКРАННІ координати."""
        sx, sy = win32gui.ClientToScreen(self.hwnd, (x, y))
        win32api.PostMessage(self.hwnd, win32con.WM_MOUSEWHEEL,
                             (ticks * win32con.WHEEL_DELTA) << 16, self._lp(sx, sy))

    # ---------- скріншот фонового вікна ----------------------------------------
    def screenshot(self, region: tuple[int, int, int, int] | None = None,
                   retries: int = 3) -> Image.Image:
        """
        Скрін клієнтської області через PrintWindow(PW_RENDERFULLCONTENT).
        Працює коли вікно перекрите іншим, але НЕ коли згорнуте.
        region = (x, y, w, h) у координатах клієнта.

        Приблизно 1 кадр з 40 приходить повністю чорним (гонка з D3D Present).
        Такий кадр перезнімаємо, тому retries.
        """
        # Спершу BitBlt: просто копіює поверхню вікна з DWM, вікну нічого не шле,
        # тому гра не "моргає". PrintWindow шле вікну WM_PRINT (примусове
        # перемальовування) — від нього клієнт смикається; лишаємо як запасний.
        for attempt in range(retries):
            img = self._grab(print_window=attempt > 0)
            if img.convert("L").getextrema()[1] > 8:  # не суцільно чорний
                break
            time.sleep(0.03)
        if region:
            x, y, rw, rh = region
            img = img.crop((x, y, x + rw, y + rh))
        return img

    def _grab(self, print_window: bool = False) -> Image.Image:
        w, h = self.client_size
        if w == 0 or h == 0:
            raise RuntimeError("window has empty client area (minimized?)")
        hwnd_dc = win32gui.GetDC(self.hwnd)  # DC клієнтської області
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        try:
            if print_window:
                PW_CLIENTONLY, PW_RENDERFULLCONTENT = 0x1, 0x2
                ok = user32.PrintWindow(self.hwnd, save_dc.GetSafeHdc(), PW_CLIENTONLY | PW_RENDERFULLCONTENT)
            else:
                save_dc.BitBlt((0, 0), (w, h), mfc_dc, (0, 0), win32con.SRCCOPY)
                ok = True
            info = bmp.GetInfo()
            data = bmp.GetBitmapBits(True)
            img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), data, "raw", "BGRX", 0, 1)
        finally:
            win32gui.DeleteObject(bmp.GetHandle())
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(self.hwnd, hwnd_dc)
        if not ok:
            raise RuntimeError("PrintWindow failed")
        return img

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        return self.screenshot((x, y, 1, 1)).getpixel((0, 0))[:3]


if __name__ == "__main__":
    # Приклад: кожні 3 секунди Tab + F1 у вікно PW, поки ти сидиш в іншому вікні.
    win = BgWindow.pw()
    print(f"hwnd={win.hwnd} title={win.title!r} client={win.client_size} fg={win.is_foreground()}")
    while True:
        win.press("tab")
        time.sleep(0.2)
        win.press("f1")
        time.sleep(2.8)
        win.press("f2")
        time.sleep(1)
