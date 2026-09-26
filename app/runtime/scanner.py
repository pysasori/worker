"""
Сканер вікон: знаходить усі клієнти гри й читає нік кожного.

Вікно від запуску до запуску змінює hwnd, а заголовок в усіх однаковий, тому
персонажа впізнаємо по табличці з ніком. Читання OCR не ідеальне, тож нік
«стабілізується» окремо для кожного вікна:

  * уже відомий персонаж (нечітко збігається з тим, що в конфізі) — приймаємо з першого читання;
  * невідомий нік — приймаємо лише коли він прочитався двічі підряд. Екран вибору
    персонажа чи завантаження дає щоразу різне сміття, і воно не має ставати
    новим персонажем у списку;
  * порожнє або зіпсуте читання нічого не міняє: у бою табличку може перекрити вікно,
    а сесія через це не повинна втрачати персонажа.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from PIL import Image

from app.capture.win32 import Win32WindowCapture
from app.capture.window_finder import WindowMatch, find_windows
from app.core.exceptions import BotError
from app.vision.nick import NickConfig, read_nick, same_nick

log = logging.getLogger("bot")


@dataclass
class FoundWindow:
    hwnd: int
    title: str = ""
    width: int = 0
    height: int = 0
    available: bool = True                # згорнуте вікно не рендериться — нік не прочитати
    nick: str = ""                        # порожньо = ще не прочитаний
    index: int = 0                        # порядок у EnumWindows (для переносу старих налаштувань)
    pinned: bool = False                  # нік закріплено за цим вікном
    manual: bool = False                  # закріплено вручну, а не прочитано з екрана
    raw: str = ""                         # що OCR прочитав востаннє (для діагностики й псевдонімів)

    def public(self) -> dict:
        return {"hwnd": self.hwnd, "title": self.title, "width": self.width,
                "height": self.height, "available": self.available, "nick": self.nick,
                "index": self.index, "pinned": self.pinned, "manual": self.manual,
                "raw": self.raw}


@dataclass
class _Seen:
    nick: str = ""                        # прийнятий нік
    pending: str = ""                     # кандидат, який чекає підтвердження
    pending_reads: int = 0
    locked: bool = False                  # нік закріплено: читання його не міняють
    manual: bool = False                  # закріплено людиною
    raw: str = ""                         # останнє читання OCR


@dataclass
class WindowScanner:
    nick_cfg: NickConfig = field(default_factory=NickConfig)
    confirm_reads: int = 2                # скільки разів підряд має збігтись новий нік
    # підміни для тестів: як шукати вікна, знімати кадр і читати нік
    find: Callable[[], list[int]] = lambda: find_windows(WindowMatch())
    grab: Callable[[int], Image.Image] | None = None
    reader: Callable[[Image.Image], str] | None = None
    info: Callable[[int], tuple[str, tuple[int, int], bool]] | None = None
    _seen: dict[int, _Seen] = field(default_factory=dict)

    # ---- читання ----------------------------------------------------------------
    def _info(self, hwnd: int) -> tuple[str, tuple[int, int], bool]:
        """Заголовок, розмір клієнта і чи вікно доступне (згорнуте не рендериться)."""
        if self.info is not None:
            return self.info(hwnd)
        import win32gui

        cap = Win32WindowCapture(hwnd)
        return win32gui.GetWindowText(hwnd), cap.client_size(), cap.is_available()

    def _frame(self, hwnd: int) -> Image.Image:
        if self.grab is not None:
            return self.grab(hwnd)
        return Win32WindowCapture(hwnd).grab()

    def _read(self, image: Image.Image) -> str:
        return self.reader(image) if self.reader else read_nick(image, self.nick_cfg)

    def settle(self, hwnd: int, read: str, known: dict[str, list[str]] | list[str]) -> str:
        """
        Прийняти або відкинути нове читання ніка для вікна. Повертає поточний нік.
        known — персонажі з конфіга: {нік: [псевдоніми]}. Псевдонім — те, що OCR звично
        читає замість справжнього ніка; людина підтвердила, що це той самий персонаж.
        """
        seen = self._seen.setdefault(hwnd, _Seen())
        if read:
            seen.raw = read
        if seen.locked:
            return seen.nick                       # закріплено: читання нічого не міняють
        ratio = self.nick_cfg.same_ratio
        if not read:
            return seen.nick
        names = known if isinstance(known, dict) else {k: [] for k in known}
        canon = next((nick for nick, aliases in names.items()
                      if any(same_nick(read, n, ratio) for n in [nick, *aliases])), None)
        if canon is not None:                      # відомий персонаж — віримо одразу
            seen.nick, seen.pending, seen.pending_reads = canon, "", 0
            seen.locked = True
            return seen.nick
        if seen.pending and same_nick(read, seen.pending, ratio):
            seen.pending_reads += 1
        else:
            seen.pending, seen.pending_reads = read, 1
        if seen.pending_reads >= self.confirm_reads:
            seen.nick, seen.pending, seen.pending_reads = seen.pending, "", 0
            seen.locked = True
        return seen.nick

    def pin(self, hwnd: int, nick: str) -> None:
        """Людина каже, чий це клієнт: нік закріплюється, читання його вже не міняють."""
        seen = self._seen.setdefault(hwnd, _Seen())
        seen.nick, seen.locked, seen.manual = nick, True, True
        seen.pending, seen.pending_reads = "", 0

    def unpin(self, hwnd: int) -> None:
        """Забути закріплення: нік буде прочитано заново."""
        self._seen[hwnd] = _Seen()

    def raw_of(self, hwnd: int) -> str:
        return self._seen.get(hwnd, _Seen()).raw

    def scan(self, known: dict[str, list[str]] | list[str] | None = None) -> list[FoundWindow]:
        """Усі вікна гри з нашими найкращими здогадками про ніки."""
        known = known or {}
        hwnds = self.find()
        for stale in set(self._seen) - set(hwnds):
            del self._seen[stale]                  # вікно закрили — hwnd може дістатись іншому
        out: list[FoundWindow] = []
        for index, hwnd in enumerate(hwnds):
            window = FoundWindow(hwnd=hwnd, index=index)
            try:
                window.title, (window.width, window.height), window.available = self._info(hwnd)
                if window.available and not self._seen.get(hwnd, _Seen()).locked:
                    # закріпленому вікну OCR не потрібен — і економимо ~150 мс на кожному скані
                    window.nick = self.settle(hwnd, self._read(self._frame(hwnd)), known)
                else:
                    window.nick = self._seen.get(hwnd, _Seen()).nick
            except BotError as exc:
                log.debug("сканер: вікно %s пропущено: %s", hwnd, exc.message)
                window.nick = self._seen.get(hwnd, _Seen()).nick
            except Exception:
                log.exception("сканер: збій на вікні %s", hwnd)
            seen = self._seen.get(hwnd, _Seen())
            window.pinned, window.manual, window.raw = seen.locked, seen.manual, seen.raw
            out.append(window)
        return out
