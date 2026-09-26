"""
Сервіс для веб-інтерфейсу: володіє конфігом і оркестратором, віддає стан,
приймає ручні натискання і кадри для калібрування.

Веб-шар (server.py) сам нічого не знає про вікна й пайплайни — тільки викликає це.
"""
from __future__ import annotations

import io
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from app.capture.win32 import Win32WindowCapture
from app.capture.window_finder import WindowMatch, find_windows, resolve_window
from app.config.loader import load_config, save_config
from app.config.schemas import BotConfig, CharacterConfig, WindowConfig
from app.core.exceptions import BotError, ConfigError, WindowNotFoundError
from app.core.logging import window_logger
from app.core.settings import settings
from app.input.win32 import Win32MessageInput
from app.pipelines import config_schema, get_pipeline_class, known_types
from app.pipelines.loot import LootConfig
from app.runtime.orchestrator import Orchestrator
from app.runtime.scanner import FoundWindow, WindowScanner
from app.vision.nick import same_nick
from app.vision.ground import center_square, scan_ground


class BotService:
    """Один на процес. Потокобезпечний: веб і потоки вікон ходять сюди паралельно."""

    def __init__(self, config_path: Path | None = None) -> None:
        self.log = window_logger("web")
        self.config_path = Path(config_path or settings.CONFIG_PATH)
        self.config: BotConfig = load_config(self.config_path)
        self.orchestrator: Orchestrator | None = None
        self.dry_run = False
        # зупинку руками наглядач поважає і бота назад не піднімає
        self.stopped_by_user = False
        self._lock = threading.RLock()
        # вікна гри й ніки персонажів: сканер оновлює їх у фоні раз на scan_interval
        self.scanner = WindowScanner()
        self.found: list[FoundWindow] = []
        self.scanned_at = 0.0
        self._scan_stop = threading.Event()
        self._scan_thread: threading.Thread | None = None
        self._guard_thread: threading.Thread | None = None
        self._ticks: dict[str, tuple[int, float]] = {}      # останній тік сесії й коли він змінився
        self._autostarted = False

    # ---- сканер вікон --------------------------------------------------------
    def start_scanner(self) -> None:
        """Фонове опитування вікон. Працює й коли бот зупинений — інтерфейс бачить клієнти."""
        if self._scan_thread and self._scan_thread.is_alive():
            return
        self._scan_stop.clear()
        self._scan_thread = threading.Thread(target=self._scan_loop, name="scanner", daemon=True)
        self._scan_thread.start()
        self.start_guard()

    def stop_scanner(self) -> None:
        self._scan_stop.set()

    def _scan_loop(self) -> None:
        while not self._scan_stop.is_set():
            try:
                self.scan_once()
                self._maybe_autostart()
            except Exception:
                self.log.exception("сканер вікон: збій, пробую далі")
            with self._lock:
                pause = self.config.settings.scan_interval
            self._scan_stop.wait(pause)

    def scan_once(self) -> list[FoundWindow]:
        """Знайти вікна, прочитати ніки, додати нових персонажів і підігнати сесії."""
        with self._lock:
            known = self.config.known_nicks()
            self.scanner.nick_cfg = self.config.settings.nick
        found = self.scanner.scan(known)              # довго (OCR), тому без блокування
        with self._lock:
            self.found = found
            self.scanned_at = time.time()
            if self._adopt(found):
                save_config(self.config, self.config_path)
            if self.is_running():
                self.orchestrator.sync(self._desired())
        return found

    # ---- закріплення вікон ------------------------------------------------------
    def _found(self, name: str) -> FoundWindow:
        for f in self.found:
            if name in (f.nick, self.window_ident(f)):
                return f
        raise BotError(f"вікна '{name}' зараз нема серед клієнтів гри")

    def assign_window(self, name: str, nick: str) -> dict[str, Any]:
        """
        Людина каже, чий це клієнт. Закріплюємо hwnd за ніком (до закриття клієнта) і,
        якщо OCR читав щось інше, запам'ятовуємо те прочитання як псевдонім персонажа:
        наступного разу таке ж хибне читання впізнається само.
        """
        nick = " ".join(nick.split())
        limit = self.config.settings.nick.max_length
        if not nick or len(nick) > limit:
            raise BotError(f"нік має бути від 1 до {limit} символів")
        with self._lock:
            f = self._found(name)
            rival = next((o for o in self.found if o.hwnd != f.hwnd and o.nick == nick), None)
            if rival is not None:
                raise BotError(f"нік «{nick}» уже закріплено за вікном #{rival.hwnd} — "
                               f"спершу перечитай його")
            cfg = self.config
            char = cfg.characters.get(nick)
            if char is None:
                char = cfg.characters[nick] = CharacterConfig(profile=cfg.new_character_profile())
            raw = f.raw
            if raw and raw != nick and not same_nick(raw, nick, cfg.settings.nick.same_ratio) \
                    and raw not in char.aliases:
                char.aliases.append(raw)
            old = f.nick
            self.scanner.pin(f.hwnd, nick)
            if old and old != nick and self._pristine(old):
                del cfg.characters[old]               # хибний «персонаж», створений автоматично
            save_config(cfg, self.config_path)
        self.scan_once()
        return self.state()

    def _pristine(self, nick: str) -> bool:
        """Персонаж, якого створив сканер і якого ніхто не чіпав: безпечно прибрати."""
        char = self.config.characters.get(nick)
        return bool(char and not char.enabled and not char.overrides and not char.label
                    and not char.aliases and char.profile == self.config.new_character_profile())

    def unpin_window(self, name: str) -> dict[str, Any]:
        """Скинути закріплення й прочитати нік заново."""
        with self._lock:
            self.scanner.unpin(self._found(name).hwnd)
        self.scan_once()
        return self.state()

    # ---- наглядач ---------------------------------------------------------------
    def start_guard(self) -> None:
        if self._guard_thread and self._guard_thread.is_alive():
            return
        self._guard_thread = threading.Thread(target=self._guard_loop, name="guard", daemon=True)
        self._guard_thread.start()

    def _guard_loop(self) -> None:
        while not self._scan_stop.is_set():
            with self._lock:
                every = self.config.settings.guard_every
            self._scan_stop.wait(every)
            try:
                self.guard_once()
            except Exception:
                self.log.exception("наглядач: збій, пробую далі")

    def guard_once(self, now: float | None = None) -> list[str]:
        """
        Одна перевірка. Сесія, чиї кадри не рухаються довше stall_after, або чий потік
        помер, перезапускається — інші вікна не чіпаються. Повертає імена перезапущених.
        """
        now = time.time() if now is None else now
        restarted: list[str] = []
        with self._lock:
            cfg = self.config.settings
            if not cfg.guard or not self.is_running():
                self._ticks.clear()
                return restarted
            orch = self.orchestrator
            for name in orch.dead():
                self.log.warning("!! потік сесії %s помер — перезапускаю", name)
                if orch.restart(name):
                    restarted.append(name)
            for st in orch.statuses():
                prev = self._ticks.get(st.window)
                if not st.connected:
                    self._ticks.pop(st.window, None)        # вікна нема — це не зависання
                elif prev is None or prev[0] != st.tick:
                    self._ticks[st.window] = (st.tick, now)
                elif now - prev[1] >= cfg.stall_after and st.window not in restarted:
                    self.log.warning("!! кадри вікна %s стоять %.0fс — перезапускаю сесію",
                                     st.window, now - prev[1])
                    self._ticks.pop(st.window, None)
                    if orch.restart(st.window):
                        restarted.append(st.window)
        return restarted

    def _maybe_autostart(self) -> None:
        """Автостарт: один раз, після першого скану, якщо бота не зупинено рукою."""
        with self._lock:
            if self._autostarted or not self.config.settings.autostart:
                return
            self._autostarted = True
            if not self.is_running() and not self.stopped_by_user:
                self.log.info("автостарт: запускаю бота")
                self.start()

    def _adopt(self, found: list[FoundWindow]) -> bool:
        """Внести в конфіг нових персонажів. True, якщо конфіг змінився."""
        cfg = self.config
        changed = False
        if not cfg.migrated_windows and cfg.windows:
            placed = 0
            # «клієнт №N» — це порядок вікон у системі, а він міняється, щойно відкрили ще
            # один клієнт. Коли вікон більше, ніж було налаштовано, невідомо, чи справді
            # той самий персонаж у тому ж місці, тому прапорець «запускати» не переносимо:
            # бот не має сам стартувати в клієнті, який людина щойно відкрила
            ambiguous = len(found) != len(cfg.windows)
            for legacy in cfg.windows:
                # старе вікно було «клієнт №N» — його налаштування переходять персонажу,
                # який зараз сидить у цьому клієнті
                target = next((f for f in found if f.index == legacy.match.index and f.nick), None)
                if target is None:
                    continue
                placed += 1
                if target.nick not in cfg.characters:
                    cfg.characters[target.nick] = CharacterConfig(
                        profile=legacy.profile, enabled=legacy.enabled and not ambiguous,
                        poll_interval=legacy.poll_interval, overrides=legacy.overrides)
                    self.log.info("перенесено налаштування вікна «%s» персонажу %s%s",
                                  legacy.name, target.nick,
                                  " (вимкнений: клієнтів більше, ніж було налаштовано)" if ambiguous else "")
                    changed = True
            if placed == len(cfg.windows):
                cfg.migrated_windows = True
                changed = True
        for f in found:
            if f.nick and f.nick not in cfg.characters:
                cfg.characters[f.nick] = CharacterConfig(profile=cfg.new_character_profile())
                self.log.info("новий персонаж %s (вікно %s) — профіль «%s», бот вимкнений",
                              f.nick, f.hwnd, cfg.characters[f.nick].profile)
                changed = True
        return changed

    def _desired(self) -> list[WindowConfig]:
        """Вікна, для яких мають працювати сесії: знайдений клієнт + налаштування персонажа."""
        out: list[WindowConfig] = []
        taken: set[str] = set()
        for f in self.found:
            char = self.config.characters.get(f.nick)
            if char is None or not char.enabled:
                continue
            if f.nick in taken:                       # два клієнти з одним ніком — другий пропускаємо
                continue
            taken.add(f.nick)
            out.append(self.config.window_for(f.nick, f.hwnd))
        return out

    # ---- конфіг --------------------------------------------------------------
    def get_config(self) -> dict[str, Any]:
        with self._lock:
            return self.config.model_dump(mode="json")

    def update_config(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Зберегти конфіг. Якщо бот працює — перезапустити з новими налаштуваннями."""
        try:
            new_config = BotConfig(**raw)
        except Exception as e:
            raise ConfigError(str(e)) from e
        with self._lock:
            self.config = new_config
            self.scanner.nick_cfg = new_config.settings.nick
            save_config(new_config, self.config_path)
            self._apply_running()
        return self.get_config()

    def _apply_running(self) -> None:
        """
        Донести нові налаштування до працюючих сесій. Перезапускаються лише ті, чиї
        налаштування справді змінились: правка профілю одного персонажа не смикає інших.
        """
        if self.is_running():
            self.orchestrator.set_config(self.config)
            self.orchestrator.sync(self._desired())

    # ---- каталог блоків конструктора ----------------------------------------
    def catalog(self) -> list[dict[str, Any]]:
        out = []
        for type_name in known_types():
            cls = get_pipeline_class(type_name)
            out.append({
                "type": type_name,
                "label": cls.label or type_name,
                "category": cls.category,
                "provides": sorted(cls.provides),
                "requires": sorted(cls.requires),
                "schema": config_schema(type_name),
                "defaults": cls.config_model.model_construct().model_dump(mode="json")
                if not cls.config_model.model_fields_set else {},
            })
        return out

    # ---- керування -----------------------------------------------------------
    def is_running(self) -> bool:
        return bool(self.orchestrator and not self.orchestrator.stop_flag.is_set())

    def start(self, dry_run: bool = False, only: list[str] | None = None) -> None:
        with self._lock:
            if self.is_running():
                return
            self.dry_run = dry_run
            self.stopped_by_user = False
            if not self.found:
                self.scan_once()                      # перший «Запустити» не чекає фонового скану
            self.orchestrator = Orchestrator(self.config, dry_run=dry_run, only=only,
                                             windows=self._desired())
            self.orchestrator.start()

    def stop(self, by_user: bool = True) -> None:
        with self._lock:
            self.stopped_by_user = by_user
            if self.orchestrator:
                self.orchestrator.stop()
                self.orchestrator = None

    def state(self) -> dict[str, Any]:
        with self._lock:
            statuses = {s.window: s for s in (self.orchestrator.statuses() if self.orchestrator else [])}
            windows: list[dict[str, Any]] = []
            online: set[str] = set()

            def entry(name: str, nick: str, f: FoundWindow | None) -> dict[str, Any]:
                char = self.config.characters.get(nick)
                st = statuses.get(name)
                return {
                    "name": name, "nick": nick,
                    "label": (char.label if char and char.label else nick) or name,
                    "hwnd": f.hwnd if f else None, "online": f is not None,
                    "width": f.width if f else 0, "height": f.height if f else 0,
                    "available": f.available if f else False,
                    "known": char is not None,
                    "pinned": bool(f and f.pinned), "manual": bool(f and f.manual),
                    "raw": f.raw if f else "",
                    "profile": char.profile if char else None,
                    "enabled": bool(char and char.enabled),
                    "connected": bool(st and st.connected),
                    "tick": st.tick if st else 0,
                    "fps": round(st.fps, 1) if st else 0.0,
                    "error": st.last_error if st else "",
                    "pipelines": dict(st.pipelines) if st else {},
                }

            for f in self.found:
                name = f.nick or self.window_ident(f)
                if f.nick:
                    online.add(f.nick)
                windows.append(entry(name, f.nick, f))
            for nick in self.config.characters:          # персонажі, чиїх вікон зараз нема
                if nick not in online:
                    windows.append(entry(nick, nick, None))
            return {"running": self.is_running(), "dry_run": self.dry_run,
                    "stopped_by_user": self.stopped_by_user and not self.is_running(),
                    "scanned_at": self.scanned_at, "windows": windows}

    # ---- вікна ---------------------------------------------------------------
    @staticmethod
    def window_ident(f: FoundWindow) -> str:
        """Ім'я вікна, нік якого ще не прочитано: за ним же працюють кадр і ручні кнопки."""
        return f"hwnd:{f.hwnd}"

    def discover_windows(self, rescan: bool = True) -> list[dict[str, Any]]:
        """Клієнти гри з нориками. rescan — прочитати заново, а не віддати результат фонового скану."""
        found = self.scan_once() if rescan else list(self.found)
        return [{**f.public(), "name": f.nick or self.window_ident(f)} for f in found]

    def _window_cfg(self, name: str) -> WindowConfig:
        """Налаштування вікна за іменем: персонаж (нік) або старе вікно з конфіга."""
        with self._lock:
            char = self.config.characters.get(name)
            if char is not None:
                return WindowConfig(name=name, profile=char.profile, overrides=char.overrides,
                                    enabled=char.enabled, poll_interval=char.poll_interval)
            for w in self.config.windows:
                if w.name == name:
                    return w
        raise BotError(f"вікна '{name}' нема в конфізі")

    def _hwnd(self, name: str) -> int:
        with self._lock:
            for f in self.found:
                if name in (f.nick, self.window_ident(f)):
                    return f.hwnd
            if name in self.config.characters:
                raise WindowNotFoundError(f"вікно персонажа {name} не знайдено — клієнт закритий?")
        return resolve_window(self._window_cfg(name).match)

    def press(self, name: str, key: str, times: int = 1, interval: float = 0.3) -> dict[str, Any]:
        """Ручна кнопка з інтерфейсу. Працює і коли бот зупинений."""
        import time

        inp = Win32MessageInput(self._hwnd(name))
        for i in range(max(1, times)):
            if i:
                time.sleep(interval)
            inp.press(key)
        return {"window": name, "key": key, "times": times}

    # ---- кадр для калібрування ------------------------------------------------
    def frame_png(self, name: str, overlay: str = "", scale: float = 1.0) -> bytes:
        cap = Win32WindowCapture(self._hwnd(name), name=name)
        image = cap.grab()
        if overlay == "loot":
            image = self._draw_loot_overlay(name, image)
        if scale != 1.0:
            image = image.resize((int(image.width * scale), int(image.height * scale)))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()

    def loot_check(self, name: str) -> dict[str, Any]:
        """Що детектор бачить на землі просто зараз — для підбору порогів."""
        cfg = self._loot_config(name)
        if cfg is None:
            return {"enabled": False, "labels": 0, "has_loot": False}
        cap = Win32WindowCapture(self._hwnd(name), name=name)
        image = cap.grab()
        region = center_square(image.size, cfg.check)
        reading = scan_ground(image.crop(region.box), region, cfg.check)
        return {"enabled": cfg.check.enabled, "labels": reading.labels,
                "has_loot": reading.has_loot, "boxes": reading.boxes,
                "region": region.model_dump()}

    # ---- калібрування кліком по превʼю ---------------------------------------
    CALIBRATION = {
        # кнопки «Починить все» і «Да» більше не калібруються точкою: вони
        # шукаються за зразком (app/vision/template.py), тому переживають
        # пересування вікна «Лавка»
        "pet_frame": ("pet_heal", "search", "рамка пета"),
    }
    SEARCH_BOX = (200, 140)      # розмір зони пошуку рамки пета, яку ставимо кліком

    def calibrate(self, name: str, what: str, x: int, y: int) -> dict[str, Any]:
        """Запамʼятати точку, на яку показав користувач у превʼю."""
        if what not in self.CALIBRATION:
            raise BotError(f"невідома точка «{what}»")
        pipeline, field, title = self.CALIBRATION[what]
        with self._lock:
            window = self._window_cfg(name)
            profile = self.config.profiles[window.profile]
            spec = next((s for s in profile.pipelines if s.type == pipeline), None)
            if spec is None:
                raise BotError(f"у профілі нема блока «{pipeline}»")
            if field == "search":
                w, h = self.SEARCH_BOX
                spec.config[field] = {"x": max(0, int(x) - w // 2), "y": max(0, int(y) - h // 2),
                                      "w": w, "h": h}
            else:
                spec.config[field] = {"x": int(x), "y": int(y)}
            save_config(self.config, self.config_path)
            self._apply_running()
        self.log.info("калібрування: %s -> (%s, %s)", title, x, y)
        return {"what": what, "title": title, "x": int(x), "y": int(y)}

    def _loot_config(self, name: str) -> LootConfig | None:
        window = self._window_cfg(name)
        for spec in self.config.specs_for(window):
            if spec.type == "loot":
                return LootConfig(**spec.config)
        return None

    def _draw_loot_overlay(self, name: str, image: Image.Image) -> Image.Image:
        cfg = self._loot_config(name)
        if cfg is None:
            return image
        region = center_square(image.size, cfg.check)
        reading = scan_ground(image.crop(region.box), region, cfg.check)
        out = image.copy()
        draw = ImageDraw.Draw(out)
        x, y, w, h = region.x, region.y, region.w, region.h
        draw.rectangle([x, y, x + w - 1, y + h - 1], outline=(80, 220, 120), width=2)
        ex = cfg.check.exclude // 2
        cx, cy = x + w // 2, y + h // 2
        draw.rectangle([cx - ex, cy - ex, cx + ex, cy + ex], outline=(220, 120, 80), width=1)
        for bx, by, bw, bh in reading.boxes:
            draw.rectangle([bx - 2, by - 2, bx + bw + 2, by + bh + 2], outline=(255, 220, 60), width=2)
        return out
