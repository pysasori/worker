"""
Повернення після смерті.

Що робить гравець, те й бот:

    вікно «Многие герои умрут…» -> «Ближний город» -> чекаємо, поки місто завантажиться
    -> (якщо ввімкнено політ) клавіша польоту, за потреби чекаємо висоту
    -> «Список» під мінімапою -> подвійний клік по точці фарму -> гра сама веде
    -> (якщо летимо) у вікні «Автопуть» тягнемо повзунок «Высота»: після воскресіння там
       висота землі, і на ній автопуть упирається в стіну міста
    -> прилетіли: (якщо летіли) ще раз клавіша польоту, щоб сісти -> закриваємо «Список»

Дорога після смерті довга, тому й таймаути тут у хвилинах, а не в секундах.
Поки блок працює, він тримає busy: бій, лут, продаж і решта мовчать — мертвий
персонаж усе одно нічого не може, а в дорозі бійка лише заважає.
"""
from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import Field

from app.core.geometry import Region

from app.pipelines.actions import ClickAt, DragTo, PipelineResult, PressKey
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext
from app.pipelines.registry import register
from app.pipelines.return_home import ReturnHomeConfig, ReturnHomePipeline
from app.pipelines.shared import (
    SHARED_HOME, Position, read_altitude, read_player, read_position, set_busy, set_mounted,
)
from app.vision.autopath import AutopathConfig, handle_x_for, read_autopath
from app.vision.template import TemplateSpec, find_template

BUSY = "death_return"
log = logging.getLogger("bot")
ROOT = Path(__file__).resolve().parents[2]


class DeathState(str, Enum):
    IDLE = "idle"
    RESPAWN = "respawn"          # тиснули «Ближний город», чекаємо, поки вікно зникне
    LOADING = "loading"          # місто вантажиться
    TAKEOFF = "takeoff"          # злітаємо
    CLIMB = "climb"              # набираємо висоту
    OPEN_LIST = "open_list"      # відкриваємо «Список»
    SET_HEIGHT = "set_height"    # повзунок висоти у вікні «Автопуть»
    TRAVEL = "travel"            # летимо / біжимо до точки фарму
    LAND = "land"                # сідаємо
    CLOSE_LIST = "close_list"
    VERIFY = "verify"            # звіряємо координати: справді долетів?


class DeathReturnConfig(PipelineConfig):
    use_flight: bool = Field(default=True, title="Летіти після воскресіння",
                             description="вимкнено — просто подвійний клік по точці фарму, і персонаж "
                                         "біжить сам")
    flight_key: str = Field(default="9", title="Клавіша польоту",
                            description="нею й злітаємо, і сідаємо, коли прилетіли")
    flight_height: int = Field(default=70, ge=0, title="Висота польоту",
                               description="ставиться повзунком у вікні «Автопуть»; на 70 персонаж "
                                           "перелітає стіни міста. 0 = не чіпати повзунок")
    land_wait: float = Field(default=10.0, ge=0, title="Чекати приземлення, с",
                             description="після 9 у повітрі персонаж падає до землі кілька секунд")
    climb_to: int = Field(default=0, ge=0, title="Набрати висоту перед дорогою",
                          description="0 = не чекати висоти. Клавішами з фону висота не змінюється — "
                                      "висоту задає «Висота польоту»", json_schema_extra={"tech": True})
    climb_timeout: float = Field(default=25.0, ge=0, title="Чекати висоту не довше, с")
    after_respawn: float = Field(default=12.0, ge=0, title="Пауза на завантаження міста, с")
    takeoff_delay: float = Field(default=3.0, ge=0, title="Пауза після зльоту, с")
    arrive_distance: float = Field(default=6.0, ge=0, title="Прилетіли, якщо ближче ніж")
    travel_timeout: float = Field(default=900.0, gt=0, title="Найдовше чекати дорогу, с",
                                  description="після смерті політ займає багато часу")
    stuck_after: float = Field(default=40.0, gt=0, title="Застряг, якщо стоїть, с")
    retries: int = Field(default=5, ge=1, title="Повторних кліків по точці")
    verify_reads: int = Field(default=2, ge=1, title="Звірок координат після дороги",
                              description="в бій повертаємось, лише коли координати підтвердили, "
                                          "що ми таки на фармі")
    verify_timeout: float = Field(default=25.0, gt=0, title="Чекати підтвердження, с")

    fresh_pos_timeout: float = Field(default=60.0, gt=0, title="Чекати координати в місті, с",
                                     json_schema_extra={"tech": True})
    confirm_frames: int = Field(default=3, ge=1, title="Кадрів із вікном смерті",
                                json_schema_extra={"tech": True})
    check_every: float = Field(default=0.5, ge=0, title="Шукати вікно смерті раз на, с",
                               json_schema_extra={"tech": True},
                               description="щокадру дорого: так уже гальмував сторож діалогів")
    step_timeout: float = Field(default=10.0, gt=0, title="Чекати вікно, с", json_schema_extra={"tech": True})
    hover_delay: float = Field(default=0.35, ge=0, title="Наведення перед кліком, с",
                               json_schema_extra={"tech": True})
    death_signs: list[TemplateSpec] = Field(
        default_factory=lambda: [
            TemplateSpec(name="respawn_city.png", threshold=0.75, area=Region.of(420, 380, 600, 320)),   # «Ближний город»
            TemplateSpec(name="respawn_here.png", threshold=0.75, area=Region.of(420, 380, 600, 320)),   # «Воскрешение»
            TemplateSpec(name="death_text.png", threshold=0.75, area=Region.of(420, 380, 600, 320)),     # текст вікна
        ],
        title="Ознаки вікна смерті", json_schema_extra={"tech": True},
        description="досить будь-якої: над вікном висять «Княжество» та ім'я персонажа, і коли "
                    "вони лягали на текст, збіг падав до 0.67 — смерть не помічали дві години")
    snapshot_dir: str = Field(default="logs/shots", title="Куди зберігати знімки смерті",
                              json_schema_extra={"tech": True},
                              description="кадр у момент смерті і коли HP на нулі, а вікна не видно")
    city_button: TemplateSpec = Field(
        default_factory=lambda: TemplateSpec(name="respawn_city.png", threshold=0.8,
                                             area=Region.of(420, 380, 600, 320)),
        title="Кнопка «Ближний город»", json_schema_extra={"tech": True})
    autopath: AutopathConfig = Field(default_factory=AutopathConfig, title="Вікно «Автопуть»",
                                     json_schema_extra={"tech": True})
    route: ReturnHomeConfig = Field(default_factory=ReturnHomeConfig, title="Дорога через «Список»",
                                    description="назва точки фарму і пошук кнопки — ті самі, що в "
                                                "«Поверненні на місце»")


@register
class DeathReturnPipeline(Pipeline):
    type_name = "death_return"
    label = "Повернення після смерті"
    category = "nav"
    config_model = DeathReturnConfig

    def __init__(self, config: DeathReturnConfig, window: str = "") -> None:
        super().__init__(config, window)
        self.nav = ReturnHomePipeline(config.route, window)   # вміє знаходити «Список» і точку
        self.reset()

    def reset(self) -> None:
        self.state = DeathState.IDLE
        self.seen = 0
        self.deadline = 0.0
        self.wait_until = 0.0
        self.started = 0.0
        self.respawned_at = 0.0
        self.clicked_at = 0.0
        self.verified = 0
        self.closed_at = 0.0
        self.height_tries = 0
        self.tries = 0
        self.last_pos: Position | None = None
        self.moved_at = 0.0
        self.flying = False
        self.deaths = 0
        self.last_check = float("-inf")
        self.hp_zero_since: float | None = None
        self.last_shot = float("-inf")

    # ---- допоміжне ----------------------------------------------------------------
    def _dead(self, frame) -> bool:
        return any(find_template(frame, sign) is not None for sign in self.config.death_signs)

    def _snapshot(self, ctx: PipelineContext, why: str) -> str | None:
        """Кадр на диск: щоб наступного разу було видно, що саме сталося."""
        cfg: DeathReturnConfig = self.config
        if ctx.now - self.last_shot < 600:
            return None
        self.last_shot = ctx.now
        folder = ROOT / cfg.snapshot_dir
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{datetime.now():%Y%m%d-%H%M%S}-{why}.png"
            ctx.frame.image.save(path)
            try:
                return str(path.relative_to(ROOT))
            except ValueError:                      # тека поза проєктом
                return str(path)
        except OSError as exc:
            log.warning("[%s] не вдалось зберегти знімок: %s", self.window, exc)
            return None

    def _watch_hp(self, ctx: PipelineContext) -> list[str]:
        """HP на нулі, а вікна смерті не бачимо — зберігаємо кадр для розбору."""
        hp = read_player(ctx.shared)
        if hp is None or hp.present:
            self.hp_zero_since = None
            return []
        if self.hp_zero_since is None:
            self.hp_zero_since = ctx.now
            return []
        if ctx.now - self.hp_zero_since < 8:
            return []
        shot = self._snapshot(ctx, "hp-zero")
        return [f"!! HP персонажа на нулі, а вікна смерті не бачу — знімок {shot}"] if shot else []

    def _home(self, ctx: PipelineContext) -> Position | None:
        return ctx.shared.get(SHARED_HOME)

    def _press(self, key: str, why: str, after: float = 0.4) -> PressKey:
        return PressKey(key, delay_after=after, reason=why)

    def _click(self, x: int, y: int, why: str, double: bool = False) -> ClickAt:
        return ClickAt(x, y, hover_delay=self.config.hover_delay, delay_after=0.5,
                       reason=why, double=double)

    def _finish(self, ctx: PipelineContext, why: str) -> PipelineResult:
        self.state = DeathState.IDLE
        self.seen = 0
        set_busy(ctx.shared, BUSY, False)
        return PipelineResult(status="", events=[why])

    # ---- цикл ------------------------------------------------------------------------
    def process(self, ctx: PipelineContext) -> PipelineResult:
        cfg: DeathReturnConfig = self.config
        frame = ctx.frame.image
        handler = {
            DeathState.IDLE: self._idle,
            DeathState.RESPAWN: self._respawn,
            DeathState.LOADING: self._loading,
            DeathState.TAKEOFF: self._takeoff,
            DeathState.CLIMB: self._climb,
            DeathState.OPEN_LIST: self._open_list,
            DeathState.SET_HEIGHT: self._set_height,
            DeathState.TRAVEL: self._travel,
            DeathState.LAND: self._land,
            DeathState.CLOSE_LIST: self._close_list,
            DeathState.VERIFY: self._verify,
        }[self.state]
        if self.state is DeathState.IDLE:
            if ctx.now - self.last_check < cfg.check_every:
                return PipelineResult.idle("схоже, персонаж загинув" if self.seen else "")
            self.last_check = ctx.now
        elif self.state is not DeathState.RESPAWN and ctx.now - self.last_check >= cfg.check_every:
            self.last_check = ctx.now
            if self._dead(frame):   # у дорозі знову загинув
                self.state, self.seen = DeathState.IDLE, cfg.confirm_frames - 1
                return self._idle(ctx)
        return handler(ctx)

    def _idle(self, ctx: PipelineContext) -> PipelineResult:
        cfg: DeathReturnConfig = self.config
        if not self._dead(ctx.frame.image):
            # Скидаємо безумовно: після перепідключення/reset лічильник `seen`
            # вже нульовий, але старий busy міг лишитися у спільному стані.
            set_busy(ctx.shared, BUSY, False)
            self.seen = 0
            events = self._watch_hp(ctx)
            return PipelineResult(status="", events=events) if events else PipelineResult.idle()
        self.seen += 1
        set_busy(ctx.shared, BUSY, True)             # мертвим нічого не тиснемо
        if self.seen < cfg.confirm_frames:
            return PipelineResult.idle("схоже, персонаж загинув")
        button = find_template(ctx.frame.image, cfg.city_button)
        if button is None:
            return PipelineResult.idle("персонаж загинув · не бачу «Ближний город»")
        self.deaths += 1
        shot = self._snapshot(ctx, "death")
        set_mounted(ctx.shared, False)               # гра сама знімає зі звіра
        self.state = DeathState.RESPAWN
        self.deadline = ctx.now + cfg.step_timeout
        self.started = ctx.now
        return PipelineResult(
            actions=[self._click(button.x, button.y, "воскреснути в місті")],
            status="смерть: воскресаю в місті",
            events=[f"!! персонаж загинув (раз {self.deaths}) — воскресаю в місті"
                    + (f", знімок {shot}" if shot else "")])

    def _respawn(self, ctx: PipelineContext) -> PipelineResult:
        cfg: DeathReturnConfig = self.config
        if not self._dead(ctx.frame.image):
            self.state = DeathState.LOADING
            self.respawned_at = ctx.now
            self.wait_until = ctx.now + cfg.after_respawn
            return PipelineResult.idle("смерть: місто вантажиться")
        if ctx.now > self.deadline:                  # клік не спрацював — ще раз
            self.state, self.seen = DeathState.IDLE, cfg.confirm_frames - 1
        return PipelineResult.idle("смерть: чекаю воскресіння")

    def _loading(self, ctx: PipelineContext) -> PipelineResult:
        cfg: DeathReturnConfig = self.config
        pos = read_position(ctx.shared)
        # координати мають бути прочитані вже в місті: стара точка з місця фарму
        # означала б «уже вдома», і бот сідав би, не злетівши
        fresh = pos is not None and pos.known and pos.at > self.respawned_at
        if ctx.now < self.wait_until:
            return PipelineResult.idle("смерть: місто вантажиться")
        if not fresh:
            if ctx.now < self.wait_until + cfg.fresh_pos_timeout:
                return PipelineResult.idle("смерть: чекаю координати в місті")
            return self._finish(ctx, "!! після воскресіння не читаються координати — далі сам")
        if self._home(ctx) is None:
            return self._finish(ctx, "!! воскрес, але місце фарму невідоме — далі сам")
        if cfg.use_flight:
            self.state = DeathState.TAKEOFF
            self.wait_until = ctx.now + cfg.takeoff_delay
            self.flying = True
            set_mounted(ctx.shared, True)
            return PipelineResult(actions=[self._press(cfg.flight_key, "злетіти")],
                                  status="повернення: злітаю", events=["воскрес у місті, злітаю"])
        self.state = DeathState.OPEN_LIST
        self.deadline = ctx.now + cfg.step_timeout
        return PipelineResult(status="повернення: відкриваю «Список»",
                              events=["воскрес у місті, біжу на фарм"])

    def _takeoff(self, ctx: PipelineContext) -> PipelineResult:
        cfg: DeathReturnConfig = self.config
        if ctx.now < self.wait_until:
            return PipelineResult.idle("повернення: злітаю")
        self.state = DeathState.CLIMB if cfg.climb_to else DeathState.OPEN_LIST
        self.wait_until = ctx.now + cfg.climb_timeout
        self.deadline = ctx.now + cfg.step_timeout
        return PipelineResult.idle("повернення: набираю висоту" if cfg.climb_to else "")

    def _climb(self, ctx: PipelineContext) -> PipelineResult:
        cfg: DeathReturnConfig = self.config
        alt = read_altitude(ctx.shared)
        high = alt is not None and alt.known and alt.z >= cfg.climb_to
        if high or ctx.now > self.wait_until:
            self.state = DeathState.OPEN_LIST
            self.deadline = ctx.now + cfg.step_timeout
            note = [] if high else [f"висоти {cfg.climb_to} не набрав — лечу як є"]
            return PipelineResult(status="повернення: відкриваю «Список»", events=note)
        now_z = alt.z if alt and alt.known else "?"
        return PipelineResult.idle(f"повернення: висота {now_z}/{cfg.climb_to}")

    def _open_list(self, ctx: PipelineContext) -> PipelineResult:
        cfg: DeathReturnConfig = self.config
        nav = self.nav
        title = nav._list_title(ctx)
        if title is None:
            if ctx.now > self.deadline:
                return self._finish(ctx, "!! «Список» не відкрився — повернутись не вдалось")
            button = find_template(ctx.frame.image, cfg.route.button)
            if button is None:
                return PipelineResult.idle("повернення: шукаю кнопку «Список»")
            self.deadline = ctx.now + cfg.step_timeout
            return PipelineResult(actions=[self._click(button.x, button.y, "відкрити «Список»")],
                                  status="повернення: відкриваю «Список»")
        point = nav._find_point(ctx, title)
        if point is None:
            return self._finish(ctx, f"!! у «Списку» нема точки «{cfg.route.point_name}»")
        self.tries += 1
        self.state = DeathState.TRAVEL
        if self.flying and cfg.flight_height:
            self.state = DeathState.SET_HEIGHT
            self.height_tries = 0
            self.deadline = ctx.now + 5.0
            self.wait_until = ctx.now + 1.0          # вікно «Автопуть» з'являється не одразу
        self.moved_at = ctx.now
        self.clicked_at = ctx.now
        self.last_pos = read_position(ctx.shared)
        return PipelineResult(
            actions=[self._click(point.x, point.y, f"до «{cfg.route.point_name}»", double=True)],
            status=f"повернення: {'лечу' if self.flying else 'біжу'} на фарм",
            events=[f"подвійний клік по «{cfg.route.point_name}» (спроба {self.tries})"])

    def _set_height(self, ctx: PipelineContext) -> PipelineResult:
        cfg: DeathReturnConfig = self.config
        if ctx.now < self.wait_until:
            return PipelineResult.idle("повернення: ставлю висоту")
        path = read_autopath(ctx.frame.image, cfg.autopath)
        if path is None or path.handle_x is None:
            if ctx.now < self.deadline:
                return PipelineResult.idle("повернення: чекаю вікно «Автопуть»")
            return self._fly(ctx, "!! вікна «Автопуть» не бачу — лечу на тій висоті, що є")
        if abs(path.height - cfg.flight_height) <= 3:
            return self._fly(ctx, f"висота автопуті {path.height}")
        if self.height_tries >= 3:
            return self._fly(ctx, f"!! повзунок висоти не слухається ({path.height}) — лечу як є")
        self.height_tries += 1
        self.wait_until = ctx.now + 1.0
        self.deadline = ctx.now + 5.0
        y = path.title.y + cfg.autopath.track_y
        target = handle_x_for(path.title, cfg.flight_height, cfg.autopath)
        return PipelineResult(
            actions=[DragTo(path.handle_x, y, target, y, hover_delay=cfg.hover_delay, delay_after=0.3,
                            reason=f"висота {path.height} -> {cfg.flight_height}")],
            status="повернення: ставлю висоту")

    def _fly(self, ctx: PipelineContext, event: str) -> PipelineResult:
        self.state = DeathState.TRAVEL
        self.moved_at = ctx.now
        return PipelineResult(status="повернення: лечу на фарм", events=[event])

    def _travel(self, ctx: PipelineContext) -> PipelineResult:
        cfg: DeathReturnConfig = self.config
        pos, home = read_position(ctx.shared), self._home(ctx)
        if pos is not None and pos.at < self.clicked_at:
            pos = None                                  # прочитано ще до кліку — не віримо
        if pos and pos.known and self.last_pos and self.last_pos.known \
                and (pos.x, pos.y) != (self.last_pos.x, self.last_pos.y):
            self.moved_at = ctx.now
        if pos and pos.known:
            self.last_pos = pos
        away = pos.distance_to(home) if (pos and pos.known and home) else None
        if away is not None and away <= cfg.arrive_distance:
            return self._arrived(ctx, pos)
        if ctx.now - self.started > cfg.travel_timeout:
            return self._arrived(ctx, pos, failed=True)
        if ctx.now - self.moved_at > cfg.stuck_after:
            if self.tries >= cfg.retries:
                return self._arrived(ctx, pos, failed=True)
            self.state = DeathState.OPEN_LIST
            self.deadline = ctx.now + cfg.step_timeout
            return PipelineResult(status="повернення: стою, клікаю ще раз",
                                  events=[f"стою {cfg.stuck_after:.0f}с на {pos} — пробую ще раз"])
        left = f"{away:.0f}" if away is not None else "?"
        return PipelineResult.idle(f"повернення: {'лечу' if self.flying else 'біжу'}, лишилось {left}")

    def _arrived(self, ctx: PipelineContext, pos, failed: bool = False) -> PipelineResult:
        cfg: DeathReturnConfig = self.config
        events = ([f"!! до фарму не дістався ({pos}) — далі сам"] if failed
                  else [f"долетів до фарму ({pos})"])
        if self.flying:
            self.flying = False
            self.state = DeathState.LAND
            self.wait_until = ctx.now + cfg.land_wait
            set_mounted(ctx.shared, False)
            return PipelineResult(actions=[self._press(cfg.flight_key, "сісти")],
                                  status="повернення: сідаю", events=events)
        self.state = DeathState.CLOSE_LIST
        self.deadline = ctx.now + cfg.step_timeout
        return PipelineResult(status="повернення: закриваю «Список»", events=events)

    def _land(self, ctx: PipelineContext) -> PipelineResult:
        cfg: DeathReturnConfig = self.config
        if ctx.now < self.wait_until:
            return PipelineResult.idle("повернення: сідаю")
        self.state = DeathState.CLOSE_LIST
        self.deadline = ctx.now + cfg.step_timeout
        return PipelineResult.idle("повернення: закриваю «Список»")

    def _verify(self, ctx: PipelineContext) -> PipelineResult:
        """Дорога скінчилась — але фармити йдемо лише тоді, коли координати це підтвердили."""
        cfg: DeathReturnConfig = self.config
        pos, home = read_position(ctx.shared), self._home(ctx)
        if pos is not None and pos.known and pos.at >= self.closed_at and home is not None:
            away = pos.distance_to(home)
            if away <= cfg.route.max_distance:
                self.verified += 1
                if self.verified >= cfg.verify_reads:
                    return self._finish(ctx, f"повернувся після смерті на {pos}, фармлю далі")
                return PipelineResult.idle("повернення: звіряю координати")
            self.verified = 0
            if self.tries < cfg.retries:
                self.state = DeathState.OPEN_LIST       # не долетів — ще одна спроба
                self.deadline = ctx.now + cfg.step_timeout
                return PipelineResult(status="повернення: не долетів, пробую ще раз",
                                      events=[f"!! після дороги я на {pos}, а фарм {home} "
                                              f"({away:.0f}) — пробую ще раз"])
        if ctx.now > self.deadline:
            return self._finish(ctx, "!! координати після дороги не звірив — фармлю так")
        return PipelineResult.idle("повернення: звіряю координати")

    def _close_list(self, ctx: PipelineContext) -> PipelineResult:
        cfg: DeathReturnConfig = self.config
        title = self.nav._list_title(ctx)
        if title is None or ctx.now > self.deadline:
            self.state = DeathState.VERIFY
            self.verified = 0
            self.closed_at = ctx.now
            self.deadline = ctx.now + cfg.verify_timeout
            return PipelineResult(status="повернення: звіряю координати",
                                  events=["«Список» закрито, звіряю координати"])
        if ctx.now < self.wait_until:
            return PipelineResult.idle("повернення: закриваю «Список»")
        self.wait_until = ctx.now + 1.2               # не клікаємо щокадру
        cross = (title.x + cfg.route.close.x, title.y + cfg.route.close.y)
        return PipelineResult(actions=[self._click(*cross, "закрити «Список»")],
                              status="повернення: закриваю «Список»")
