"""Пошук вікон гри за класом / процесом / заголовком. Спільне для capture та input."""
from __future__ import annotations

import subprocess

import win32gui
import win32process
from pydantic import BaseModel

from app.core.exceptions import WindowNotFoundError


class WindowMatch(BaseModel):
    """Як знайти вікно. Порожні поля не перевіряються."""

    class_name: str | None = "ElementClient Window"
    process: str | None = "ElementClient.exe"
    title: str | None = None          # підрядок заголовка
    index: int = 0                    # який за ліком з підхожих (для кількох клієнтів)
    hwnd: int | None = None           # жорстко задати handle

    def describe(self) -> str:
        parts = [f"{k}={v!r}" for k, v in
                 (("hwnd", self.hwnd), ("class", self.class_name),
                  ("process", self.process), ("title", self.title), ("index", self.index))
                 if v not in (None, "")]
        return ", ".join(parts)


def _pids_of(process: str) -> set[int]:
    out = subprocess.check_output(
        f'tasklist /FI "IMAGENAME eq {process}" /FO CSV /NH', shell=True).decode("cp866", "ignore")
    return {int(line.split('","')[1]) for line in out.splitlines() if line.startswith('"')}


def find_windows(match: WindowMatch) -> list[int]:
    """Усі hwnd, що підходять під match, у порядку EnumWindows."""
    if match.hwnd:
        return [match.hwnd] if win32gui.IsWindow(match.hwnd) else []
    pids = _pids_of(match.process) if match.process else set()
    if match.process and not pids:
        return []
    found: list[int] = []

    def cb(hwnd: int, _) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        if match.class_name and win32gui.GetClassName(hwnd) != match.class_name:
            return
        if match.title and match.title.lower() not in win32gui.GetWindowText(hwnd).lower():
            return
        if pids and win32process.GetWindowThreadProcessId(hwnd)[1] not in pids:
            return
        found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return found


def resolve_window(match: WindowMatch) -> int:
    """Один hwnd або WindowNotFoundError."""
    found = find_windows(match)
    if len(found) <= match.index:
        raise WindowNotFoundError(match.describe())
    return found[match.index]
