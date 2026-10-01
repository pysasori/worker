"""
Сесія одного вікна: один кадр за тік -> пул пайплайнів -> виконання дій.

Пайплайни виконуються в порядку з конфіга, тому виживання (лік піта) стоїть
раніше за бій. Кадр знімається ОДИН раз і роздається всім — це і швидше,
і всі пайплайни бачать однаковий момент часу.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.capture.abstract import CapturePort
from app.capture.win32 import Win32WindowCapture
from app.capture.window_finder import resolve_window
from app.config.schemas import BotConfig, WindowConfig
from app.core.exceptions import BotError, WindowGoneError, WindowNotFoundError
from app.core.logging import window_logger
from app.input.abstract import InputPort
from app.input.win32 import Win32MessageInput
from app.pipelines.base import Frame, Pipeline, PipelineContext
from app.pipelines.registry import build_pipeline
from app.pipelines.wiring import describe_wiring, order_pipelines
from app.runtime.executor import ActionExecutor


@dataclass
class SessionStatus:
    """Знімок стану для консолі та майбутнього веб-інтерфейсу."""

    window: str
    running: bool = False
    connected: bool = False
    tick: int = 0
    fps: float = 0.0
    last_error: str = ""
    pipelines: dict[str, str] = field(default_factory=dict)

    def body(self) -> str:
        """Рядок статусу без імені вікна — для логера, який ім'я вже додає сам."""
        parts = " | ".join(f"{v}" for v in self.pipelines.values() if v)
        state = "працює" if self.connected else (self.last_error or "чекаю вікно")
        return f"{state}{' | ' + parts if parts else ''}"

    def line(self) -> str:
        return f"[{self.window}] {self.body()}"


class WindowSession:
    def __init__(self, window_cfg: WindowConfig, bot_cfg: BotConfig, dry_run: bool = False) -> None:
        self.cfg = window_cfg
        self.bot_cfg = bot_cfg
        self.dry_run = dry_run
        self.log = window_logger(window_cfg.name)
        self.status = SessionStatus(window=window_cfg.name)

        self.capture: CapturePort | None = None
        self.input: InputPort | None = None
        self.executor: ActionExecutor | None = None
        self.pipelines: list[Pipeline] = self._build_pipelines()
        self.shared: dict = {}
        self._tick = 0
        self._last_frame_ts = 0.0

    # ---- підготовка ----------------------------------------------------------
    def _build_pipelines(self) -> list[Pipeline]:
        pipelines: list[Pipeline] = []
        for spec in self.bot_cfg.specs_for(self.cfg):
            if not spec.enabled:
                continue
            pipelines.append(build_pipeline(spec.type, spec.config, window=self.cfg.name))
        # постачальник даних завжди раніше за споживача, інакше лут побачив би
        # смерть цілі лише наступним кадром
        return order_pipelines(pipelines, window=self.cfg.name)

    def connect(self) -> bool:
        """Знайти вікно і підготувати захоплення/ввід. False, якщо вікна ще нема."""
        try:
            hwnd = resolve_window(self.cfg.match)
        except WindowNotFoundError as e:
            self.status.connected = False
            self.status.last_error = e.message
            return False
        self.capture = Win32WindowCapture(hwnd, name=self.cfg.name)
        self.input = Win32MessageInput(hwnd)
        self.executor = ActionExecutor(self.input, dry_run=self.dry_run)
        size = self.capture.client_size()
        expected = self.bot_cfg.profiles[self.cfg.profile].client_size
        self.log.info("вікно знайдено hwnd=%s, клієнт %sx%s, пайплайни: %s",
                      hwnd, size[0], size[1], describe_wiring(self.pipelines) or "нема")
        if expected and tuple(expected) != size:
            self.log.warning("розмір клієнта %sx%s, а профіль '%s' калібровано під %sx%s — "
                             "координати можуть не збігтись", size[0], size[1], self.cfg.profile,
                             expected[0], expected[1])
        # Після втрати/повторного знаходження вікна тут могли лишитися старі target,
        # busy та координати. Пайплайни скидаються нижче, отже і їхню спільну дошку
        # треба почати заново, інакше живий бот може вічно «чекати: death_return».
        self.shared.clear()
        for p in self.pipelines:
            p.reset()
        self.status.connected = True
        self.status.last_error = ""
        return True

    # ---- один тік ------------------------------------------------------------
    def tick(self) -> SessionStatus:
        if not self.status.connected and not self.connect():
            return self.status
        assert self.capture and self.executor and self.input

        try:
            frame = Frame(image=self.capture.grab())
        except WindowGoneError as e:
            self.log.warning("%s", e.message)
            self.status.connected = False
            self.status.last_error = e.message
            return self.status

        self._tick += 1
        if self._last_frame_ts:
            dt = frame.ts - self._last_frame_ts
            self.status.fps = (1 / dt) if dt > 0 else 0.0
        self._last_frame_ts = frame.ts

        ctx = PipelineContext(
            window=self.cfg.name,
            frame=frame,
            tick=self._tick,
            foreground=getattr(self.input, "is_foreground", lambda: False)(),
            shared=self.shared,
        )

        for pipeline in self.pipelines:
            # і рішення, і виконання дій — під одним захистом: збій у клавішах
            # (вікно моргнуло, гра перемальовується) не має валити решту пайплайнів
            try:
                result = pipeline.process(ctx)
                for event in result.events:
                    self.log.info("[%s] %s", pipeline.name, event)
                if result.actions:
                    done = self.executor.run(result.actions)
                    self.log.debug("[%s] -> %s", pipeline.name, "; ".join(done))
                self.status.pipelines[pipeline.name] = result.status
            except BotError as e:
                self.log.error("[%s] %s", pipeline.name, e.message)
            except Exception:
                self.log.exception("[%s] несподівана помилка", pipeline.name)

        self.status.tick = self._tick
        return self.status

    # ---- цикл ----------------------------------------------------------------
    def run_forever(self, stop_flag) -> None:
        self.status.running = True
        self.log.info("старт%s", " (dry-run: клавіші не шлються)" if self.dry_run else "")
        try:
            while not stop_flag.is_set():
                started = time.perf_counter()
                try:
                    self.tick()
                except Exception:
                    # потік вікна не має вмирати мовчки: логуємо і встаємо далі,
                    # інакше бот «працює», а насправді нічого не робить до ранку
                    self.log.exception("збій тіку, продовжую")
                    self.status.last_error = "збій тіку"
                    self.status.connected = False
                    stop_flag.wait(2.0)
                    continue
                if not self.status.connected:
                    stop_flag.wait(1.0)  # вікно зникло — не крутити цикл на повну
                    continue
                elapsed = time.perf_counter() - started
                stop_flag.wait(max(0.0, self.cfg.poll_interval - elapsed))
        finally:
            self.status.running = False
            self.log.info("стоп")
