"""
Оркестратор «одне вікно — один процес».

Усі сесії в одному процесі ділять одне ядро через GIL: аналіз кадру на Python тримає
його, і п'ять вікон по черзі чекають одне одного. Тут кожна сесія живе у своєму
процесі, тож вікна не гальмують сусідів. Зовні інтерфейс той самий, що в Orchestrator
(statuses/dead/restart/sync/stop), тому веб-сервіс і наглядач нічого не помічають.

Між процесами ходять лише дві черги: знімки стану (SessionStatus) і записи логу.
Дочірні процеси стартують через spawn (Windows) і гинуть разом із батьком (daemon).
"""
from __future__ import annotations

import copy
import logging
import logging.handlers
import multiprocessing as mp
import threading
from dataclasses import dataclass, field
from queue import Empty, Full

from app.config.schemas import BotConfig, WindowConfig
from app.runtime.orchestrator import Orchestrator, signature_of
from app.runtime.session import SessionStatus

_ctx = mp.get_context("spawn")
STATUS_EVERY = 0.5


def _worker(window: WindowConfig, config: BotConfig, dry_run: bool, level: int,
            stop, status_q, log_q) -> None:
    """Тіло дочірнього процесу: одна сесія + потік, що віддає її стан батькові."""
    root = logging.getLogger()
    root.handlers[:] = [logging.handlers.QueueHandler(log_q)]
    root.setLevel(level)
    from app.core.background import enable
    from app.runtime.session import WindowSession

    enable()                      # важкі перевірки кадру — у фоні й тут
    session = WindowSession(window, config, dry_run=dry_run)

    def pump() -> None:
        while not stop.is_set():
            try:
                status_q.put_nowait((window.name, copy.deepcopy(session.status)))
            except Full:
                pass
            stop.wait(STATUS_EVERY)

    threading.Thread(target=pump, name="status-pump", daemon=True).start()
    session.run_forever(stop)


@dataclass
class _ProcSlot:
    window: WindowConfig
    signature: str
    status: SessionStatus
    stop: object = field(default_factory=_ctx.Event)
    proc: object = None


class ProcessOrchestrator(Orchestrator):
    def __init__(self, config: BotConfig, dry_run: bool = False,
                 only: list[str] | None = None,
                 windows: list[WindowConfig] | None = None) -> None:
        self._status_q = _ctx.Queue(maxsize=256)
        self._log_q = _ctx.Queue()
        self._reader_stop = threading.Event()
        super().__init__(config, dry_run=dry_run, only=only, windows=windows)
        self._listener = logging.handlers.QueueListener(
            self._log_q, *logging.getLogger().handlers, respect_handler_level=False)
        self._listener.start()
        threading.Thread(target=self._read_statuses, name="proc-status", daemon=True).start()

    # ---- стан із дочірніх процесів ---------------------------------------------
    def _read_statuses(self) -> None:
        while not self._reader_stop.is_set():
            try:
                name, status = self._status_q.get(timeout=0.5)
            except Empty:
                continue
            except (OSError, ValueError):
                return
            slot = self._slots.get(name)
            if slot is not None:
                slot.status = status

    # ---- слоти (перевизначені під процеси) --------------------------------------
    @property
    def sessions(self) -> list:
        return []

    def _add(self, window: WindowConfig) -> None:
        self._slots[window.name] = _ProcSlot(window=window, signature=signature_of(window, self.config),
                                             status=SessionStatus(window=window.name))

    def _launch(self, slot: _ProcSlot) -> None:
        slot.stop = _ctx.Event()
        slot.proc = _ctx.Process(
            target=_worker, name=f"win-{slot.window.name}", daemon=True,
            args=(slot.window, self.config, self.dry_run, logging.getLogger().level,
                  slot.stop, self._status_q, self._log_q))
        slot.proc.start()

    @staticmethod
    def _end(slot: _ProcSlot, wait: float = 3.0) -> None:
        slot.stop.set()
        proc = slot.proc
        if proc is None:
            return
        proc.join(timeout=wait)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)

    def _drop(self, name: str) -> None:
        slot = self._slots.pop(name, None)
        if slot is not None:
            self._end(slot)

    def dead(self) -> list[str]:
        with self._lock:
            return [n for n, s in self._slots.items()
                    if self._launched and s.proc is not None and not s.proc.is_alive()
                    and not s.stop.is_set()]

    def statuses(self) -> list[SessionStatus]:
        return [s.status for s in list(self._slots.values())]

    def stop(self) -> None:
        self.stop_flag.set()
        with self._lock:
            slots = list(self._slots.values())
        for slot in slots:
            slot.stop.set()
        for slot in slots:
            self._end(slot)
        self._reader_stop.set()
        try:
            self._listener.stop()
        except Exception:
            pass
