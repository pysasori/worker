"""
Повернення на місце фарму.

Персонаж відходить сам: ганяється за мобом, моби його відтягують. Рух клавішами
гра з фонових повідомлень не приймає, тому ходимо так, як ходить гравець мишею:

    кнопка під мінімапою -> вікно «Список» -> подвійний клік по точці «фарм»

і далі гра сама веде персонажа до точки. Пайплайн лише стежить за координатами:
прийшов — закриває «Список», застряг — клікає ще раз, не вийшло — чесно пише в лог
і пробує пізніше.

Висота фарму задається окремо: не вказана — сідаємо на землю (після автопуті персонаж
лишається верхи й у повітрі, а там гра не дає ні кликати пета, ні спокійно фармити);
вказана — летимо на неї тим самим автопуттю, бо клавішами з фону висота не змінюється.

Де саме «дім», гра в «Списку» не показує, тому його дає блок «Координати», а після
першого вдалого повернення пайплайн запам'ятовує, де персонаж зупинився: це і є
справжнє місце точки «фарм».
"""
from __future__ import annotations

from enum import Enum
from statistics import median

from PIL import Image, ImageChops, ImageOps
from pydantic import Field

from app.core.geometry import Point, Region
from app.pipelines.actions import ClickAt, DragTo, PipelineResult, PressKey
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext
from app.pipelines.registry import register
from app.pipelines.shared import (
    SHARED_HOME, SHARED_POS, Position, busy_reasons, is_mounted, read_altitude, read_position,
    read_target, set_busy, set_combat_ready, set_mounted,
)
from app.vision.autopath import AutopathConfig, handle_x_for, read_autopath
from app.vision.template import TemplateSpec, find_template
from app.vision.text import OcrConfig, best_match, read_lines

BUSY = "return_home"


class ReturnState(str, Enum):
    IDLE = "idle"
    OPENING = "opening"          # клікнули кнопку, чекаємо «Список»
    PICKING = "picking"          # шукаємо точку в списку
    TAKEOFF = "takeoff"          # злітаємо, щоб летіти на висоті фарму
    SET_HEIGHT = "set_height"    # повзунок висоти у вікні «Автопуть»
    RUNNING = "running"          # гра веде персонажа
    CLOSING = "closing"          # закриваємо «Список»
    VERIFY = "verify"            # звіряємо координати: справді дійшли?


class ReturnHomeConfig(PipelineConfig):
    max_distance: float = Field(default=15.0, gt=0, title="Повертатись, якщо відійшов далі ніж",
                                description="в одиницях координат гри; персонаж пробігає ~0.4 за секунду")
    arrive_distance: float = Field(default=3.0, ge=0, title="Прийшов, якщо ближче ніж")
    point_name: str = Field(default="фарм", title="Назва точки у «Списку»",
                            description="так, як вона записана в грі; дрібні помилки читання не заважають")
    only_out_of_combat: bool = Field(default=True, title="Лише поза боєм",
                                     description="спершу добиваємо ціль, потім ідемо")
    run_timeout: float = Field(default=180.0, gt=0, title="Найдовше чекати дорогу, с")
    stuck_after: float = Field(default=10.0, gt=0, title="Застряг, якщо стоїть, с")
    retries: int = Field(default=3, ge=1, title="Спроб на одне повернення")
    cooldown: float = Field(default=30.0, ge=0, title="Пауза після невдачі, с")
    land_key: str = Field(default="9", title="Клавіша польоту (щоб сісти)",
                          description="порожньо = не сідати. Автопуть привозить персонажа верхи на "
                                      "літаючому звірі, а в повітрі гра не дає ні кликати пета "
                                      "(«Здесь невозможно призвать питомца»), ні нормально фармити")
    land_above: float = Field(default=6.0, gt=0, title="Сідати, якщо вище за землю на",
                              description="земля — медіана висот, прочитаних на самому місці фарму")
    ground_reads: int = Field(default=15, ge=1, title="Читань висоти для «землі»",
                              json_schema_extra={"tech": True},
                              description="медіана з останніх N: одне зіпсуте число («6» замість "
                                          "«22») більше не стає землею назавжди")
    fly: bool = Field(default=False, title="Повертатись на польоті",
                      description="бот сідає на літаючого звіра (клавіша польоту) і летить автопуттю: "
                                  "довга дорога виходить коротшою, і по землі нема за що зачепитись")
    fly_beyond: float = Field(default=40.0, ge=0, title="Летіти, якщо відійшов далі ніж",
                              description="ближче — просто біжимо: злітати заради десятка одиниць "
                                          "довше, ніж дійти")
    fly_height: int = Field(default=55, ge=0, title="Висота польоту",
                            description="ставиться повзунком у вікні «Автопуть». Якщо задана «Висота "
                                        "фарму», береться вона")
    idle_walk_after: float = Field(default=180.0, ge=0, title="Вести через «Список», якщо цілей нема, с",
                                   description="0 = не стежити. Рятує, коли персонаж стоїть там, звідки "
                                               "не дістати мобів: автопуть саджав його на дерево, і бот "
                                               "чотири години нікого не бачив")
    verify_reads: int = Field(default=2, ge=1, title="Звірок координат після прибуття",
                              description="дійшли — тільки коли координати це підтвердили, і аж тоді в бій")
    verify_timeout: float = Field(default=20.0, gt=0, title="Чекати підтвердження, с")
    farm_altitude: int = Field(default=0, ge=0, title="Висота фарму",
                               description="0 = фармимо на землі, з повітря просто падаємо. Інакше бот "
                                           "летить автопуттю на цю висоту: третє число біля координат")
    altitude_gap: float = Field(default=8.0, gt=0, title="Допустима різниця висоти",
                                description="ближче — вважаємо, що висота та сама")
    takeoff_delay: float = Field(default=3.0, ge=0, title="Пауза після зльоту, с",
                                 json_schema_extra={"tech": True})
    land_wait: float = Field(default=8.0, ge=0, title="Чекати приземлення, с")
    min_ground: int = Field(default=5, ge=0, title="Нижче цього висоту не брати за землю",
                            json_schema_extra={"tech": True},
                            description="нуль замість висоти — зіпсуте читання; якщо взяти його за "
                                        "землю, бот вважатиме, що завжди висить у повітрі")

    step_timeout: float = Field(default=5.0, gt=0, title="Чекати вікно, с", json_schema_extra={"tech": True})
    hover_delay: float = Field(default=0.3, ge=0, title="Наведення перед кліком, с",
                               json_schema_extra={"tech": True})
    step_delay: float = Field(default=0.6, ge=0, title="Пауза після кліку, с", json_schema_extra={"tech": True})
    button: TemplateSpec = Field(
        default_factory=lambda: TemplateSpec(name="nav_button.png", threshold=0.8,
                                             area=Region.of(1150, 0, 290, 260)),
        title="Кнопка «Список» під мінімапою", json_schema_extra={"tech": True})
    title: TemplateSpec = Field(
        default_factory=lambda: TemplateSpec(name="nav_list_title.png", threshold=0.85),
        title="Заголовок вікна «Список»", json_schema_extra={"tech": True},
        description="вікно можна пересунути — шукається по всьому екрану")
    pane: Region = Field(default=Region.of(98, 39, 137, 293), title="Права колонка відносно заголовка",
                         json_schema_extra={"tech": True})
    close: Point = Field(default=Point(x=228, y=-1), title="Хрестик відносно заголовка",
                         json_schema_extra={"tech": True})
    text_threshold: int = Field(default=140, ge=0, le=255, title="Поріг яскравості тексту",
                                json_schema_extra={"tech": True})
    min_ratio: float = Field(default=0.5, gt=0, le=1, title="Схожість назви точки",
                             json_schema_extra={"tech": True},
                             description="на одній машині «фарм» стабільно читалось як «форм» "
                                         "(а↔о, згладжування шрифту інше) зі збігом 0.75 — на межі "
                                         "старого порога 0.6. Назви зон у списку зовсім інші "
                                         "(«Копи», «Город Драконов»), тож навіть 0.5 не плутає")
    autopath: AutopathConfig = Field(default_factory=AutopathConfig, title="Вікно «Автопуть»",
                                     json_schema_extra={"tech": True})
    ocr: OcrConfig = Field(default_factory=lambda: OcrConfig(scale=4), title="Читання списку",
                           json_schema_extra={"tech": True})


def _text_mask(image: Image.Image, threshold: int) -> Image.Image:
    """Світлий текст списку -> чорне на білому; синя підсвітка рядка зникає."""
    r, g, b = image.split()

    def keep(v: int) -> int:
        return 255 if v > threshold else 0

    mask = ImageChops.multiply(ImageChops.multiply(r.point(keep), g.point(keep)), b.point(keep))
    return ImageOps.invert(mask).convert("RGB")


@register
class ReturnHomePipeline(Pipeline):
    type_name = "return_home"
    label = "Повернення на місце"
    category = "nav"
    config_model = ReturnHomeConfig
    requires = frozenset({SHARED_POS, SHARED_HOME})

    def __init__(self, config: ReturnHomeConfig, window: str = "") -> None:
        super().__init__(config, window)
        self.reset()

    def reset(self) -> None:
        self.state = ReturnState.IDLE
        self.deadline = 0.0
        self.tries = 0
        self.run_started = 0.0
        self.last_move_at = 0.0
        self.last_pos: Position | None = None
        self.moved = False
        self.retry_at = 0.0
        self.learned_home: Position | None = None
        self.next_close_at = 0.0
        self.ground: int | None = None        # висота землі на місці фарму (медіана читань)
        self.ground_reads: list[int] = []
        self.land_at = float("-inf")
        self.land_tries = 0
        self.saw_target_at: float | None = None   # коли востаннє була ціль
        self.wait_until = 0.0
        self.height_tries = 0
        self.for_height = False       # пішли не за координатами, а за висотою
        self.last_alt: int | None = None
        self.took_off = False         # клавіша польоту — перемикач, за дорогу тиснемо раз
        self.flying_trip = False      # цю дорогу долаємо в повітрі
        self.verified = 0
        self.closed_at = 0.0
        self.combat_unlocked = False   # на старті спершу підтверджуємо місце фарму

    # ---- дані -----------------------------------------------------------------
    def _home(self, ctx: PipelineContext) -> Position | None:
        return self.learned_home or ctx.shared.get(SHARED_HOME)

    def _list_title(self, ctx: PipelineContext) -> Point | None:
        return find_template(ctx.frame.image, self.config.title)

    def _find_point(self, ctx: PipelineContext, title: Point) -> Point | None:
        """Рядок із назвою точки в правій колонці «Списку»."""
        cfg: ReturnHomeConfig = self.config
        box = Region.of(title.x + cfg.pane.x, title.y + cfg.pane.y, cfg.pane.w, cfg.pane.h)
        pane = _text_mask(ctx.frame.image.crop(box.box), cfg.text_threshold)
        best: tuple[float, Point] | None = None
        for text, (x0, y0, x1, y1) in read_lines(pane, cfg.ocr):
            hit = best_match(text, [cfg.point_name], cfg.min_ratio)
            if hit and (best is None or hit[1] > best[0]):
                best = (hit[1], Point(x=box.x + (x0 + x1) // 2, y=box.y + (y0 + y1) // 2))
        return best[1] if best else None

    # ---- цикл -------------------------------------------------------------------
    def process(self, ctx: PipelineContext) -> PipelineResult:
        # Закрито за замовчуванням на кожному кадрі. _idle відкриє бій лише коли
        # координати підтверджені й ми на місці, або коли добиваємо вже початий бій.
        set_combat_ready(ctx.shared, False)
        pos = read_position(ctx.shared)
        home = self._home(ctx)
        if self.state is ReturnState.IDLE:
            landing = self._land(ctx)
            if landing is not None:
                return landing
            stuck = self._no_targets(ctx, pos, home)
            return stuck if stuck is not None else self._idle(ctx, pos, home)
        if self.state is ReturnState.OPENING:
            return self._opening(ctx)
        if self.state is ReturnState.PICKING:
            return self._picking(ctx)
        if self.state is ReturnState.TAKEOFF:
            return self._takeoff(ctx)
        if self.state is ReturnState.SET_HEIGHT:
            return self._set_height(ctx)
        if self.state is ReturnState.VERIFY:
            return self._verify(ctx, pos, home)
        if self.state is ReturnState.RUNNING:
            return self._running(ctx, pos, home)
        return self._closing(ctx)

    def _no_targets(self, ctx: PipelineContext, pos: Position | None,
                    home: Position | None) -> PipelineResult | None:
        """
        Цілей нема надто довго. Найчастіше це означає, що персонаж стоїть там, звідки
        мобів не дістати (гра лишила його на дереві після автопуті). Координати при
        цьому домашні, висота нічого не підказує — тому просто ведемо його наново.
        """
        cfg: ReturnHomeConfig = self.config
        target = read_target(ctx.shared)
        if not cfg.idle_walk_after or target is None:
            return None
        if target.present or busy_reasons(ctx.shared) - {BUSY}:
            # поки хтось зайнятий (дорога після смерті, продаж, ремонт), цілей і не мусить
            # бути: інакше бот одразу після прильоту йшов у дорогу ще раз
            self.saw_target_at = ctx.now
            return None
        if self.saw_target_at is None:
            self.saw_target_at = ctx.now
            return None
        if ctx.now - self.saw_target_at < cfg.idle_walk_after or ctx.now < self.retry_at:
            return None
        self.saw_target_at = ctx.now
        self.retry_at = ctx.now + cfg.cooldown
        return self._go_home(ctx, f"{cfg.idle_walk_after:.0f}с без цілей — веду через «Список»")

    def _go_home(self, ctx: PipelineContext, why: str) -> PipelineResult:
        """Почати дорогу через «Список» — байдуже, збігаються координати чи ні."""
        cfg: ReturnHomeConfig = self.config
        set_busy(ctx.shared, BUSY, True)
        self.tries = 0
        self.took_off = False
        self.flying_trip = False
        self.run_started = ctx.now
        if self._list_title(ctx) is not None:
            self.state = ReturnState.PICKING
            return PipelineResult(status="повернення: шукаю точку", events=[why])
        button = find_template(ctx.frame.image, cfg.button)
        if button is None:
            set_busy(ctx.shared, BUSY, False)
            return PipelineResult.idle("не бачу кнопку «Список»")
        self.state = ReturnState.OPENING
        self.deadline = ctx.now + cfg.step_timeout
        return PipelineResult(actions=[self._click(button, "відкрити «Список»")],
                              status="повернення: відкриваю «Список»", events=[why])

    def _idle(self, ctx: PipelineContext, pos: Position | None, home: Position | None) -> PipelineResult:
        cfg: ReturnHomeConfig = self.config
        if not (pos and pos.known and home):
            return PipelineResult.idle()
        away = pos.distance_to(home)
        off = self._wrong_height(ctx)
        self.for_height = away <= cfg.max_distance
        if away <= cfg.max_distance and off is None:
            self.combat_unlocked = True
            set_combat_ready(ctx.shared, True)
            return PipelineResult.idle()
        why = (f"висота {off:+.0f} від потрібної {cfg.farm_altitude}" if self.for_height
               else f"далеко від місця ({away:.0f})")
        if ctx.now < self.retry_at:
            return PipelineResult.idle(f"{why} · нова спроба через {self.retry_at - ctx.now:.0f}с")
        if cfg.only_out_of_combat:
            target = read_target(ctx.shared)
            if self.combat_unlocked and target is not None and target.present:
                set_combat_ready(ctx.shared, True)
                return PipelineResult.idle(f"{why} · добиваю ціль")
        others = busy_reasons(ctx.shared) - {BUSY}
        if others:
            return PipelineResult.idle(f"{why} · чекаю: {', '.join(sorted(others))}")

        set_busy(ctx.shared, BUSY, True)
        self.tries = 0
        self.took_off = False
        self.flying_trip = bool(cfg.farm_altitude) or (cfg.fly and away >= cfg.fly_beyond)
        self.run_started = ctx.now
        events = [f"{why} — лечу на місце" if self.for_height
                  else f"відійшов на {away:.0f} ({pos}, дім {home}) — повертаюсь"]
        if self._list_title(ctx) is not None:                 # «Список» уже відкритий
            self.state = ReturnState.PICKING
            return PipelineResult(status="повернення: шукаю точку", events=events)
        button = find_template(ctx.frame.image, cfg.button)
        if button is None:
            res = self._fail(ctx, "не бачу кнопку «Список» під мінімапою")
            res.events[:0] = events
            return res
        self.state = ReturnState.OPENING
        self.deadline = ctx.now + cfg.step_timeout
        return PipelineResult(actions=[self._click(button, "відкрити «Список»")],
                              status="повернення: відкриваю «Список»", events=events)

    def _wrong_height(self, ctx: PipelineContext) -> float | None:
        """Наскільки висота не така, як треба. None — висоти не знаємо або вона потрібна така, як є."""
        cfg: ReturnHomeConfig = self.config
        alt = read_altitude(ctx.shared)
        if not (cfg.farm_altitude and alt is not None and alt.known):
            return None
        off = alt.z - cfg.farm_altitude
        return off if abs(off) > cfg.altitude_gap else None

    def _land(self, ctx: PipelineContext) -> PipelineResult | None:
        """
        Висимо в повітрі після автопуті — тиснемо клавішу польоту й падаємо на землю
        (шкоди від падіння нема, перевірено). Поки не сядемо, пет не приклинеться.
        Якщо висота фарму задана, сідати не треба: туди летимо автопуттю.

        Клавішу тиснемо ЛИШЕ коли знаємо, що персонаж верхи, — бо бот сам його туди
        й посадив. Це перемикач: на землі вона не саджає, а САДЖАЄ НА ЗВІРА, і далі
        бот фармить верхи, без пета й з перерваними вміннями.

        Висота землі — медіана читань на самому місці фарму. Мінімум за сесію не
        годився: одне зіпсуте число («6» замість «22») лишалось у пам'яті назавжди,
        та й за сотню кроків звідси, біля води, земля справді інша.
        """
        cfg: ReturnHomeConfig = self.config
        alt = read_altitude(ctx.shared)
        if not (cfg.land_key and alt is not None and alt.known):
            return None
        if alt.z < cfg.min_ground:
            return None                              # нуль замість висоти — читання зіпсуте
        pos, home = read_position(ctx.shared), self._home(ctx)
        at_home = (pos is not None and pos.known and home is not None
                   and pos.distance_to(home) <= cfg.arrive_distance)
        if at_home and not is_mounted(ctx.shared):
            self.ground_reads.append(alt.z)          # землю вчимо лише стоячи на ній
            del self.ground_reads[:-cfg.ground_reads]
            self.ground = int(median(self.ground_reads))
        if cfg.farm_altitude or self.ground is None:
            return None
        if not is_mounted(ctx.shared):
            self.land_tries = 0
            return None                  # не верхи — 9 тут не саджає, а садить на звіра
        above = alt.z - self.ground
        if above <= cfg.land_above:
            if self.land_tries:
                set_mounted(ctx.shared, False)       # висота впала — таки сіли
            self.land_tries = 0
            return None
        if busy_reasons(ctx.shared) - {BUSY} or ctx.now - self.land_at < cfg.land_wait:
            return PipelineResult.idle(f"висну на {above:.0f} над землею · сідаю")
        if self.land_tries >= 3:
            self.land_tries = 0
            self.land_at = ctx.now + cfg.cooldown
            set_mounted(ctx.shared, None)            # що там насправді — вже не знаємо
            return self._go_home(ctx, f"стою на {above:.0f} над землею — веду через «Список»")
        # прапорець «верхи» знімаємо не тут, а коли висота справді впаде: інакше
        # одна невдала спроба видавала б нас за пішого, і бот кинув би сідати
        self.land_at = ctx.now
        self.land_tries += 1
        events = [f"висну в повітрі (висота {alt.z}, земля {self.ground}) — сідаю"]
        if self.land_tries == 3:
            events.append(f"!! {cfg.land_key} не саджає персонажа — фармлю в повітрі")
        return PipelineResult(actions=[PressKey(cfg.land_key, delay_after=0.4, reason="сісти")],
                              status="сідаю", events=events)

    def _opening(self, ctx: PipelineContext) -> PipelineResult:
        if self._list_title(ctx) is not None:
            self.state = ReturnState.PICKING
            return PipelineResult.idle("повернення: шукаю точку")
        if ctx.now > self.deadline:
            return self._fail(ctx, "«Список» не відкрився")
        return PipelineResult.idle("повернення: чекаю «Список»")

    def _picking(self, ctx: PipelineContext) -> PipelineResult:
        cfg: ReturnHomeConfig = self.config
        title = self._list_title(ctx)
        if title is None:
            return self._fail(ctx, "«Список» закрився")
        point = self._find_point(ctx, title)
        if point is None:
            return self._fail(ctx, f"у «Списку» нема точки «{cfg.point_name}»")
        if self.flying_trip and not self.took_off and self._on_the_ground(ctx):
            self.state = ReturnState.TAKEOFF          # автопуть несе тільки верхи
            self.took_off = True
            self.wait_until = ctx.now + cfg.takeoff_delay
            set_mounted(ctx.shared, True)
            return PipelineResult(actions=[PressKey(cfg.land_key, delay_after=0.4, reason="злетіти")],
                                  status="повернення: злітаю", events=["злітаю"])
        self.tries += 1
        self.state = ReturnState.RUNNING
        self.last_move_at = ctx.now
        self.last_pos = read_position(ctx.shared)
        self.moved = False
        self.last_alt = None
        if self.flying_trip:
            self.state = ReturnState.SET_HEIGHT
            self.height_tries = 0
            self.wait_until = ctx.now + 1.0           # вікно «Автопуть» з'являється не одразу
            self.deadline = ctx.now + 5.0
        return PipelineResult(
            actions=[self._click(point, f"біжу до «{cfg.point_name}»", double=True)],
            status=f"повернення: біжу до «{cfg.point_name}»",
            events=[f"подвійний клік по «{cfg.point_name}» (спроба {self.tries})"])

    def _on_the_ground(self, ctx: PipelineContext) -> bool:
        alt = read_altitude(ctx.shared)
        if alt is None or not alt.known:
            return False
        if self.ground is None:
            return True      # землю знаємо лише вдома; в дорозі вважаємо, що стоїмо
        return alt.z - self.ground <= self.config.land_above

    def _takeoff(self, ctx: PipelineContext) -> PipelineResult:
        if ctx.now < self.wait_until:
            return PipelineResult.idle("повернення: злітаю")
        self.state = ReturnState.PICKING              # злетіли — клікаємо точку
        return PipelineResult.idle("повернення: шукаю точку")

    def _set_height(self, ctx: PipelineContext) -> PipelineResult:
        """Повзунок «Высота» у вікні «Автопуть»: на цій висоті гра й летить."""
        cfg: ReturnHomeConfig = self.config
        if ctx.now < self.wait_until:
            return PipelineResult.idle("повернення: ставлю висоту")
        path = read_autopath(ctx.frame.image, cfg.autopath)
        if path is None or path.handle_x is None:
            if ctx.now < self.deadline:
                return PipelineResult.idle("повернення: чекаю вікно «Автопуть»")
            return self._fly(ctx, "!! вікна «Автопуть» не бачу — лечу на тій висоті, що є")
        want = self._trip_height()
        if abs(path.height - want) <= 3:
            return self._fly(ctx, f"висота автопуті {path.height}")
        if self.height_tries >= 3:
            return self._fly(ctx, f"!! повзунок висоти не слухається ({path.height}) — лечу як є")
        self.height_tries += 1
        self.wait_until = ctx.now + 1.0
        self.deadline = ctx.now + 5.0
        y = path.title.y + cfg.autopath.track_y
        target = handle_x_for(path.title, want, cfg.autopath)
        return PipelineResult(
            actions=[DragTo(path.handle_x, y, target, y, hover_delay=cfg.hover_delay,
                            delay_after=0.3, reason=f"висота {path.height} -> {want}")],
            status="повернення: ставлю висоту")

    def _trip_height(self) -> int:
        cfg: ReturnHomeConfig = self.config
        return cfg.farm_altitude or cfg.fly_height

    def _fly(self, ctx: PipelineContext, event: str) -> PipelineResult:
        self.state = ReturnState.RUNNING
        self.last_move_at = ctx.now
        return PipelineResult(status="повернення: лечу на місце", events=[event])

    def _running(self, ctx: PipelineContext, pos: Position | None, home: Position | None) -> PipelineResult:
        cfg: ReturnHomeConfig = self.config
        if pos and pos.known and self.last_pos and self.last_pos.known:
            if (pos.x, pos.y) != (self.last_pos.x, self.last_pos.y):
                self.moved = True
                self.last_move_at = ctx.now
        if pos and pos.known:
            self.last_pos = pos
        alt = read_altitude(ctx.shared)
        if alt is not None and alt.known and alt.z != self.last_alt:
            # набір висоти — теж рух: інакше бот вирішить, що застряг, поки лізе вгору
            self.last_alt, self.moved, self.last_move_at = alt.z, True, ctx.now
        away = pos.distance_to(home) if (pos and pos.known and home) else None
        off = self._wrong_height(ctx)

        if self.for_height and off is None and away is not None and away <= cfg.max_distance:
            return self._arrived(ctx, f"став на висоту {alt.z if alt else '?'}, фармлю далі")
        if away is not None and away <= cfg.arrive_distance and off is None:
            return self._arrived(ctx, f"повернувся на місце ({pos})")
        if ctx.now - self.run_started > cfg.run_timeout:
            return self._fail(ctx, f"не дійшов за {cfg.run_timeout:.0f}с")

        standing = ctx.now - self.last_move_at
        if standing < cfg.stuck_after:
            left = f"{away:.0f}" if away is not None else "?"
            return PipelineResult.idle(f"повернення: біжу, лишилось {left}")
        # стоїть: або дійшов до самої точки, або застряг
        if self.moved and away is not None and away <= cfg.max_distance:
            # гра зупинила персонажа на точці — вона трохи не там, де думали
            self.learned_home = pos
            return self._arrived(ctx, f"дійшов до «{cfg.point_name}» ({pos}), запам'ятав це місце")
        if self.tries >= cfg.retries:
            return self._fail(ctx, f"застряг ({pos}) після {self.tries} спроб")
        self.state = ReturnState.PICKING                     # клікнемо точку ще раз
        return PipelineResult(status="повернення: стою, клікаю ще раз",
                              events=[f"стою {standing:.0f}с на {pos} — пробую ще раз"])

    def _arrived(self, ctx: PipelineContext, why: str) -> PipelineResult:
        self.state = ReturnState.CLOSING
        self.deadline = ctx.now + self.config.step_timeout
        self.next_close_at = ctx.now
        return PipelineResult(status="повернення: закриваю «Список»", events=[why])

    def _closing(self, ctx: PipelineContext) -> PipelineResult:
        cfg: ReturnHomeConfig = self.config
        title = self._list_title(ctx)
        if title is None:
            return self._start_verify(ctx, "«Список» закрито")
        if ctx.now > self.deadline:
            return self._start_verify(ctx, "!! не вдалось закрити «Список» — фармлю так")
        if ctx.now < self.next_close_at:
            return PipelineResult.idle("повернення: закриваю «Список»")
        self.next_close_at = ctx.now + cfg.step_delay * 2 + cfg.hover_delay
        cross = Point(x=title.x + cfg.close.x, y=title.y + cfg.close.y)
        return PipelineResult(actions=[self._click(cross, "закрити «Список»")],
                              status="повернення: закриваю «Список»")

    def _start_verify(self, ctx: PipelineContext, why: str) -> PipelineResult:
        """Дорога скінчилась — але в бій ідемо лише після звірки координат."""
        self.state = ReturnState.VERIFY
        self.verified = 0
        self.closed_at = ctx.now
        self.deadline = ctx.now + self.config.verify_timeout
        return PipelineResult(status="повернення: звіряю координати", events=[why])

    def _verify(self, ctx: PipelineContext, pos: Position | None,
                home: Position | None) -> PipelineResult:
        cfg: ReturnHomeConfig = self.config
        fresh = pos is not None and pos.known and pos.at >= self.closed_at
        if fresh and home:
            away = pos.distance_to(home)
            if away <= cfg.max_distance:
                self.verified += 1
                if self.verified >= cfg.verify_reads:
                    self.state = ReturnState.IDLE
                    set_busy(ctx.shared, BUSY, False)
                    if self.flying_trip and not cfg.farm_altitude and cfg.land_key:
                        # летіли — значить, висимо верхи: сідаємо одразу, не чекаючи, поки
                        # різниця висот перевалить поріг. Одного разу бот так провисів 4 години
                        self.flying_trip = False
                        self.land_at = ctx.now
                        set_mounted(ctx.shared, False)
                        return PipelineResult(
                            actions=[PressKey(cfg.land_key, delay_after=0.4, reason="сісти")],
                            status="", events=[f"на місці ({pos}), сідаю і в бій"])
                    return PipelineResult(status="", events=[f"на місці ({pos}), в бій"])
                return PipelineResult.idle("повернення: звіряю координати")
            self.verified = 0
            if self.tries < cfg.retries:                 # не дійшов — ще одна спроба
                self.state = ReturnState.PICKING if self._list_title(ctx) else ReturnState.IDLE
                if self.state is ReturnState.IDLE:
                    set_busy(ctx.shared, BUSY, False)    # «Список» закритий — почнемо спочатку
                return PipelineResult(status="повернення: не дійшов, пробую ще раз",
                                      events=[f"!! після дороги я на {pos}, а треба {home} "
                                              f"({away:.0f}) — пробую ще раз"])
        if ctx.now > self.deadline:
            self.state = ReturnState.IDLE
            set_busy(ctx.shared, BUSY, False)
            return PipelineResult(events=["!! координати після дороги не звірив — фармлю так"])
        return PipelineResult.idle("повернення: звіряю координати")

    def _fail(self, ctx: PipelineContext, why: str) -> PipelineResult:
        cfg: ReturnHomeConfig = self.config
        self.retry_at = ctx.now + cfg.cooldown
        if self._list_title(ctx) is not None:                # не лишаємо вікно відкритим
            self.state = ReturnState.CLOSING
            self.deadline = ctx.now + cfg.step_timeout
            self.next_close_at = ctx.now
            return PipelineResult(status="повернення: закриваю «Список»",
                                  events=[f"!! повернення не вдалось: {why}"])
        self.state = ReturnState.IDLE
        set_busy(ctx.shared, BUSY, False)
        return PipelineResult(status="", events=[f"!! повернення не вдалось: {why}"])

    def _click(self, point: Point, reason: str, double: bool = False) -> ClickAt:
        cfg: ReturnHomeConfig = self.config
        return ClickAt(point.x, point.y, hover_delay=cfg.hover_delay, delay_after=cfg.step_delay,
                       reason=reason, double=double)
