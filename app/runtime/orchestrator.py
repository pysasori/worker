"""Оркестратор: кожне вікно — своя сесія у своєму потоці. Набір вікон можна міняти на ходу."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field

from app.config.schemas import BotConfig, WindowConfig
from app.core.logging import setup_logging, window_logger
from app.runtime.session import SessionStatus, WindowSession


@dataclass
class _Slot:
    """Одна сесія зі своїм потоком і своєю зупинкою: вікно можна вимкнути, не чіпаючи решти."""

    session: WindowSession
    signature: str
    window: WindowConfig
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


def signature_of(window: WindowConfig, config: BotConfig) -> str:
    """
    Що визначає сесію: інший hwnd, інший профіль чи змінене налаштування будь-якого
    блока профілю — це вже нова сесія. Вміст профілю входить сюди, щоб правка профілю
    одного персонажа перезапускала лише його, а не всіх.
    """
    specs = [s.model_dump(mode="json") for s in config.specs_for(window)]
    return window.model_dump_json() + json.dumps(specs, sort_keys=True, ensure_ascii=False)


class Orchestrator:
    def __init__(self, config: BotConfig, dry_run: bool = False,
                 only: list[str] | None = None,
                 windows: list[WindowConfig] | None = None) -> None:
        """
        windows — які вікна запускати. None = вікна з конфіга (старий режим); сервіс
        передає сюди вікна, знайдені сканером за ніками персонажів.
        """
        setup_logging()
        self.log = window_logger("bot")
        self.config = config
        self.dry_run = dry_run
        self.only = only
        self.stop_flag = threading.Event()
        self._lock = threading.RLock()
        self._slots: dict[str, _Slot] = {}
        self._launched = False
        source = config.windows if windows is None else windows
        for w in source:
            if w.enabled and (not only or w.name in only):
                self._add(w)

    # ---- слоти ---------------------------------------------------------------
    @property
    def sessions(self) -> list[WindowSession]:
        with self._lock:
            return [s.session for s in self._slots.values()]

    def _add(self, window: WindowConfig) -> None:
        session = WindowSession(window, self.config, dry_run=self.dry_run)
        self._slots[window.name] = _Slot(session=session, window=window,
                                         signature=signature_of(window, self.config))

    def _launch(self, slot: _Slot) -> None:
        slot.thread = threading.Thread(target=slot.session.run_forever, args=(slot.stop,),
                                       name=f"win-{slot.session.cfg.name}", daemon=True)
        slot.thread.start()

    def _drop(self, name: str) -> None:
        slot = self._slots.pop(name, None)
        if slot is None:
            return
        slot.stop.set()
        if slot.thread:
            slot.thread.join(timeout=3)

    def dead(self) -> list[str]:
        """Сесії, чий потік помер сам (не через зупинку): бот у цьому вікні мовчить."""
        with self._lock:
            return [n for n, s in self._slots.items()
                    if self._launched and s.thread is not None and not s.thread.is_alive()
                    and not s.stop.is_set()]

    def restart(self, name: str) -> bool:
        """Перезапустити одну сесію з тими самими налаштуваннями. Решта не чіпається."""
        with self._lock:
            slot = self._slots.get(name)
            if slot is None or self.stop_flag.is_set():
                return False
            window = slot.window
            self._drop(name)
            self._add(window)
            if self._launched:
                self._launch(self._slots[name])
        self.log.info("сесію %s перезапущено", name)
        return True

    def set_config(self, config: BotConfig) -> None:
        """Нові налаштування: сесії, чиї налаштування змінились, перезапустить наступний sync."""
        with self._lock:
            self.config = config

    def sync(self, desired: list[WindowConfig]) -> tuple[list[str], list[str]]:
        """
        Привести працюючі сесії до списку desired: зникле вікно зупиняється, нове
        стартує, змінене (інший hwnd чи профіль) перезапускається. Решта не чіпається —
        бот у інших клієнтах не помічає, що десь відкрили ще одне вікно.
        Повертає (запущені, зупинені) для логу.
        """
        started: list[str] = []
        stopped: list[str] = []
        with self._lock:
            if self.stop_flag.is_set():
                return started, stopped
            wanted = {w.name: w for w in desired
                      if w.enabled and (not self.only or w.name in self.only)}
            for name in list(self._slots):
                slot = self._slots[name]
                if name not in wanted or slot.signature != signature_of(wanted[name], self.config):
                    self._drop(name)
                    stopped.append(name)
            for name, window in wanted.items():
                if name in self._slots:
                    continue
                self._add(window)
                if self._launched:
                    self._launch(self._slots[name])
                started.append(name)
        if started:
            self.log.info("додано вікна: %s", ", ".join(started))
        if stopped:
            self.log.info("прибрано вікна: %s", ", ".join(stopped))
        return started, stopped

    # ---- керування -------------------------------------------------------------
    def statuses(self) -> list[SessionStatus]:
        """Для консолі й веб-API."""
        return [s.session.status for s in list(self._slots.values())]

    def start(self) -> None:
        with self._lock:
            self._launched = True
            if not self._slots:
                self.log.info("поки нема вікон для запуску — чекаю, поки сканер їх знайде")
                return
            self.log.info("запускаю вікна: %s", ", ".join(self._slots))
            for slot in self._slots.values():
                self._launch(slot)

    def stop(self) -> None:
        self.stop_flag.set()
        with self._lock:
            slots = list(self._slots.values())
        for slot in slots:
            slot.stop.set()
        for slot in slots:
            if slot.thread:
                slot.thread.join(timeout=3)

    def run(self, status_interval: float = 1.0) -> None:
        """Блокуючий запуск із періодичним статусом у консоль. Ctrl+C — стоп."""
        import logging

        self.start()
        # на DEBUG рядок статусу тільки заважав би читати лог
        show_status = logging.getLogger().level > logging.DEBUG
        try:
            while not self.stop_flag.is_set():
                time.sleep(status_interval)
                if show_status:
                    for status in self.statuses():
                        print(f"\r{status.line()}   ", end="", flush=True)
        except KeyboardInterrupt:
            print()
            self.log.info("зупинка за Ctrl+C")
        finally:
            self.stop()
