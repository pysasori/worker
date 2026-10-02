"""
Ремонт спорядження: рюкзак -> «Лавка» -> «Починить все» -> підтвердження.

Робиться поза боєм і не частіше ніж раз на `every` секунд. Кожен крок перевіряється
за маркером інтерфейсу, тому пайплайн не клікає наосліп: якщо вікно не відкрилось,
він не піде далі й акуратно все закриє.

Дві особливості клієнта, знайдені на живому тесті:
  * кнопки «Починить все» і «Да (Y)» не реагують на клік без наведення — спершу
    треба навести курсор (hover_delay), інакше нічого не станеться;
  * «Лавку» і рюкзак гра відкриває там, де їх лишили мишею, тому кнопки шукаються
    за виглядом (assets/templates), а не за координатами — інакше після кожного
    пересування вікна ремонт клікав у порожнє місце;
  * гарячі клавіші діалогу (Y/N) працюють лише на англійській розкладці, тому
    підтверджуємо кліком по кнопці, а не клавішею.

Поки триває ремонт, виставляється прапорець busy: пошук цілі не бере нову ціль,
атака не б'є.
"""
from __future__ import annotations

import logging
from enum import Enum

from pydantic import Field

from app.core.geometry import Point, Region
from app.pipelines.actions import ClickAt, PipelineResult, PressKey, Wait
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext
from app.pipelines.registry import register
from app.pipelines.shared import read_target, set_busy
from app.vision.icons import (
    ColorCount, IconSearchConfig, changed_area, find_icon, find_icon_anywhere,
)
from app.vision.template import TemplateSpec, find_template
from app.vision.ui import UiMarker, marker_present


log = logging.getLogger("bot")


class RepairState(str, Enum):
    IDLE = "idle"
    OPENING_BAG = "opening_bag"
    OPENING_SHOP = "opening_shop"
    WAITING_CONFIRM = "waiting_confirm"
    CLOSING = "closing"
    VERIFY_CLOSED = "verify_closed"


class RepairConfig(PipelineConfig):
    every: float = Field(default=3600.0, gt=0, title="Раз на, с",
                         description="запасний таймер, якщо ознаки зносу не видно")
    worn_icon: TemplateSpec = Field(
        default_factory=lambda: TemplateSpec(name="gear_worn.png", threshold=0.65,
                                             area=Region.of(1380, 220, 60, 90)),
        title="Іконка зношеного спорядження", json_schema_extra={"tech": True},
        description="квадратик зі зброєю справа вгорі; він є, лише поки щось зношене. "
                    "Кольором його шукати не можна: жовто-зелена трава під ним підпадала "
                    "під той самий діапазон, і бот «ремонтував» щодесять хвилин, а потім "
                    "писав, ніби не вистачило монет")
    broken_markers: list[UiMarker] = Field(
        default_factory=lambda: [
            UiMarker(region=Region.of(1402, 240, 40, 42),      # червона = зламане
                     min_rgb=(100, 0, 0), max_rgb=(255, 60, 60), min_pixels=25),
        ], title="Ознака поломки (червона)", json_schema_extra={"tech": True},
        description="зламана річ не працює зовсім, тому її лагодимо, не чекаючи години")
    min_interval: float = Field(default=600.0, ge=0, title="За ознакою зносу не частіше ніж, с",
                                description="скільки чекати між ремонтами, коли справа видно звичайний знос. "
                                            "Червона поломка ремонтується одразу після поточної цілі. "
                                            "Раз на годину — то запасний таймер нижче")
    useless_pause: float = Field(default=1800.0, ge=0, title="Пауза, якщо ремонт не допоміг, с",
                                 json_schema_extra={"tech": True},
                                 description="ознака зносу лишилась після ремонту — найчастіше це "
                                             "брак монет. Бігати в Лавку щодесять хвилин марно")
    confirm_damage: float = Field(default=4.0, ge=0, title="Ознака має триматись, с",
                                  json_schema_extra={"tech": True},
                                  description="над цим місцем пролітають цифри урону («405» "
                                              "червоним) — через них бот бігав у Лавку щотри "
                                              "хвилини. Іконка спорядження стоїть на місці, "
                                              "цифра зникає за секунду")
    idle_before: float = Field(json_schema_extra={"tech": True}, default=2.0, ge=0, title="Спокою перед ремонтом, с")
    step_timeout: float = Field(json_schema_extra={"tech": True}, default=9.0, gt=0, title="Чекати вікно, с")
    click_again_after: float = Field(json_schema_extra={"tech": True}, default=3.0, gt=0,
                                     title="Повторити клік через, с",
                                     description="клік по іконці «Лавка» іноді не спрацьовує, "
                                                 "і ремонт зривався на порожньому місці")
    click_retries: int = Field(json_schema_extra={"tech": True}, default=2, ge=0,
                               title="Повторів кліку на крок")
    hover_delay: float = Field(json_schema_extra={"tech": True}, default=0.35, ge=0, title="Наведення перед кліком, с")
    step_delay: float = Field(json_schema_extra={"tech": True}, default=0.7, ge=0, title="Пауза між кроками, с")

    bag_key: str = Field(default="b", title="Клавіша рюкзака")
    close_key: str = Field(default="esc", title="Клавіша закриття")

    bag_title: TemplateSpec = Field(
        default_factory=lambda: TemplateSpec(name="bag_title.png", threshold=0.8),
        title="Заголовок вікна «Рюкзак»", json_schema_extra={"tech": True},
        description="рюкзак відкривається там, де його лишили мишею; по заголовку видно і те, "
                    "що він відкритий, і де саме")
    shop_offset: Point = Field(default=Point(x=22, y=424), title="Іконка «Лавка» від заголовка",
                               json_schema_extra={"tech": True},
                               description="виміряно на кількох розкладках вікна — офсет сталий")
    shop_icon_search: IconSearchConfig | None = Field(
        default_factory=lambda: IconSearchConfig(
            fill=ColorCount(min_rgb=(150, 110, 0), max_rgb=(255, 255, 130), min_pixels=140),
            ring=ColorCount(min_rgb=(0, 60, 90), max_rgb=(130, 220, 255), min_pixels=60)),
        title="Уточнення іконки «Лавка»", json_schema_extra={"tech": True},
        description="дрібне підправлення точки за виглядом іконки; якщо не спрацює, "
                    "клікаємо за офсетом від заголовка")
    repair_all: TemplateSpec = Field(
        default_factory=lambda: TemplateSpec(name="repair_all.png", threshold=0.7),
        title="Кнопка «Починить все»", json_schema_extra={"tech": True},
        description="шукається за виглядом, тому вікно «Лавка» можна тримати де завгодно")
    confirm: TemplateSpec = Field(
        default_factory=lambda: TemplateSpec(name="repair_confirm.png", threshold=0.7),
        title="Кнопка «Да» в підтвердженні", json_schema_extra={"tech": True},
        description="вона ж і ознака того, що діалог підтвердження відкрився")
    shop_open: TemplateSpec = Field(
        default_factory=lambda: TemplateSpec(name="shop_title.png", threshold=0.7),
        title="Ознака: Лавка відкрита", json_schema_extra={"tech": True},
        description="заголовок вікна «Лавка»")


@register
class RepairPipeline(Pipeline):
    type_name = "repair"
    label = "Ремонт спорядження"
    category = "service"
    config_model = RepairConfig

    def __init__(self, config: RepairConfig, window: str = "") -> None:
        super().__init__(config, window)
        self.reset()

    def reset(self) -> None:
        self.useless_until = 0.0                  # доки ремонт за ознакою не має сенсу
        self.check_broken_at: float | None = None
        self.retry_click_at = 0.0                 # коли повторити клік, якщо вікно не з'явилось
        self.retries_left = 0
        self.worn_since: float | None = None      # відколи безперервно видно звичайний знос
        self.broken_since: float | None = None    # відколи безперервно видно червону поломку
        self.state = RepairState.IDLE
        self.close_tries = 0
        self.last_repair: float | None = None   # None = ще не ремонтували
        self.idle_since: float | None = None
        self.deadline = 0.0
        self.before = None              # кадр до відкриття рюкзака
        self._damage_cache = None       # (кадр, (знос, поломка)): _recheck і _due не дивляться вдруге

    # ---- умови --------------------------------------------------------------
    def _in_combat(self, ctx: PipelineContext) -> bool:
        target = read_target(ctx.shared)
        return bool(target and target.present)

    def _due(self, ctx: PipelineContext) -> bool:
        """
        За таймером — раз на `every` (за замовчуванням годину). Раніше, ніж таймер,
        ідемо лише коли щось зламалось (червона іконка): така річ не працює взагалі.
        """
        cfg: RepairConfig = self.config
        if self.last_repair is None:
            self.last_repair = ctx.now      # відлік запасного таймера від старту
        elapsed = ctx.now - self.last_repair
        # Після ремонту спершу чекаємо контрольного кадру. Інакше стара
        # підтверджена поломка одразу запускала б друге коло ремонту.
        if self.check_broken_at is not None:
            return False
        if ctx.now < self.useless_until:
            return elapsed >= cfg.every              # ремонт не допомагає — лише за таймером
        worn, broken = self._confirmed_damage(ctx)
        if broken:
            return True                              # зламана річ не працює — не чекаємо 10 хв
        if worn:
            return elapsed >= cfg.min_interval
        return elapsed >= cfg.every

    def _recheck_broken(self, ctx: PipelineContext) -> None:
        """
        Чи прибрав ремонт ознаку зносу. Якщо ні — найчастіше це брак монет: гра просто
        не ремонтує. Тоді робимо паузу, щоб не бігати в Лавку щодесять хвилин намарно.
        """
        cfg: RepairConfig = self.config
        worn, broken = self._damage_visible(ctx.frame.image)
        still = worn or broken
        if not still:
            self.check_broken_at = None
            return
        if self.check_broken_at is not None and ctx.now >= self.check_broken_at:
            self.check_broken_at = None
            self.useless_until = ctx.now + cfg.useless_pause
            log.warning("[%s] [repair] !! знос лишився після ремонту — схоже, не вистачило "
                        "монет; наступна спроба через %.0f хв",
                        self.window, cfg.useless_pause / 60)

    def _damage_visible(self, frame) -> tuple[bool, bool]:
        """Повернути окремо звичайний знос і червону поломку."""
        cached = self._damage_cache
        if cached is not None and cached[0] is frame:      # на цей кадр уже дивились у цьому ж тіку
            return cached[1]
        cfg: RepairConfig = self.config
        worn = find_template(frame, cfg.worn_icon) is not None
        broken = any(marker_present(frame, marker) for marker in cfg.broken_markers)
        self._damage_cache = (frame, (worn, broken))
        return worn, broken

    def _confirmed_damage(self, ctx: PipelineContext) -> tuple[bool, bool]:
        """
        Знос або поломка, які не зникли за мить: над панеллю пролітають цифри урону
        і на секунду виглядають точнісінько як червона іконка. Іконка спорядження
        стоїть на місці, цифра зникає за секунду.
        """
        cfg: RepairConfig = self.config
        worn, broken = self._damage_visible(ctx.frame.image)

        if not worn:
            self.worn_since = None
        elif self.worn_since is None:
            self.worn_since = ctx.now

        if not broken:
            self.broken_since = None
        elif self.broken_since is None:
            self.broken_since = ctx.now

        worn_confirmed = (self.worn_since is not None
                          and ctx.now - self.worn_since >= cfg.confirm_damage)
        broken_confirmed = (self.broken_since is not None
                            and ctx.now - self.broken_since >= cfg.confirm_damage)
        return worn_confirmed, broken_confirmed

    # ---- цикл ---------------------------------------------------------------
    def process(self, ctx: PipelineContext) -> PipelineResult:
        cfg: RepairConfig = self.config
        frame = ctx.frame.image

        if self.state is RepairState.IDLE:
            self._recheck_broken(ctx)
            due = self._due(ctx)
            if self._in_combat(ctx):
                self.idle_since = None
                set_busy(ctx.shared, "repair", False)
                return PipelineResult.idle("час ремонту · добиваю ціль" if due else "")
            if self.idle_since is None:
                self.idle_since = ctx.now
            if not due:
                set_busy(ctx.shared, "repair", False)
                return PipelineResult.idle()
            # Таймер уже настав: не даємо пошуку взяти наступного моба, інакше на
            # безперервному фармі дві секунди спокою можуть не настати ніколи.
            set_busy(ctx.shared, "repair", True)
            if ctx.now - self.idle_since < cfg.idle_before:
                return PipelineResult.idle("час ремонту · чекаю спокою")
            return self._start(ctx, frame)

        if self.state is RepairState.VERIFY_CLOSED:
            return self._verify_closed(ctx, frame)

        if ctx.now > self.deadline:
            return self._give_up(ctx, frame)

        if self.state is RepairState.OPENING_BAG:
            point = self._shop_icon(frame)
            if point is not None:
                return self._step(ctx, RepairState.OPENING_SHOP, point, "відкриваю Лавку")
            return PipelineResult.idle("чекаю рюкзак")

        if self.state is RepairState.OPENING_SHOP:
            button = find_template(frame, cfg.repair_all)
            if button is not None:
                return self._step(ctx, RepairState.WAITING_CONFIRM, button, "ремонтую все")
            again = self._click_again(ctx, self._shop_icon(frame), "ще раз відкриваю Лавку")
            return again or PipelineResult.idle("чекаю Лавку")

        if self.state is RepairState.WAITING_CONFIRM:
            yes = find_template(frame, cfg.confirm)
            if yes is not None:
                res = self._step(ctx, RepairState.CLOSING, yes, "підтверджую")
                res.events.append("спорядження відремонтовано")
                return res
            return PipelineResult.idle("чекаю підтвердження")

        return self._finish(ctx, frame)

    # ---- кроки --------------------------------------------------------------
    def _shop_icon(self, frame):
        """
        Іконка «Лавка» і водночас ознака відкритого рюкзака.

        Рахуємо від заголовка вікна «Рюкзак»: вікно відкривається там, де його лишили,
        і одного разу через це ремонт зірвався — іконку шукали в радіусі 45 px від старої
        точки, а вікно переїхало на 60. Офсет від заголовка сталий, а пошук за виглядом
        лише трохи уточнює точку.
        """
        cfg: RepairConfig = self.config
        title = find_template(frame, cfg.bag_title)
        if title is None:
            return None
        point = Point(x=title.x + cfg.shop_offset.x, y=title.y + cfg.shop_offset.y)
        if cfg.shop_icon_search and cfg.shop_icon_search.enabled:
            return find_icon(frame, point, cfg.shop_icon_search) or point
        return point

    def _start(self, ctx: PipelineContext, frame) -> PipelineResult:
        cfg: RepairConfig = self.config
        set_busy(ctx.shared, "repair", True)
        self.state = RepairState.OPENING_BAG
        self.deadline = ctx.now + cfg.step_timeout
        actions = []
        if self._shop_icon(frame) is None:
            self.before = frame          # запам'ятовуємо екран без рюкзака
            actions.append(PressKey(cfg.bag_key, delay_after=cfg.step_delay, reason="рюкзак"))
        return PipelineResult(actions=actions, status="ремонт: відкриваю рюкзак",
                              events=["час ремонту, виходжу з бою"])

    def _click_again(self, ctx: PipelineContext, point: Point | None, why: str) -> PipelineResult | None:
        """Повторити клік, якщо вікно не з'явилось: один клік гра іноді не помічає."""
        cfg: RepairConfig = self.config
        if point is None or self.retries_left <= 0 or ctx.now < self.retry_click_at:
            return None
        self.retries_left -= 1
        self.retry_click_at = ctx.now + cfg.click_again_after
        return PipelineResult(
            actions=[ClickAt(point.x, point.y, hover_delay=cfg.hover_delay,
                             delay_after=cfg.step_delay, reason=why)],
            status=f"ремонт: {why}", events=[why])

    def _step(self, ctx: PipelineContext, nxt: RepairState, point: Point, status: str) -> PipelineResult:
        cfg: RepairConfig = self.config
        self.state = nxt
        self.deadline = ctx.now + cfg.step_timeout
        self.retry_click_at = ctx.now + cfg.click_again_after
        self.retries_left = cfg.click_retries
        return PipelineResult(
            actions=[ClickAt(point.x, point.y, hover_delay=cfg.hover_delay,
                             delay_after=cfg.step_delay, reason=status)],
            status=f"ремонт: {status}")

    def _finish(self, ctx: PipelineContext, frame) -> PipelineResult:
        """Закрити все, що відкрили, і повернутись до звичайної роботи."""
        cfg: RepairConfig = self.config
        actions = [PressKey(cfg.close_key, delay_after=cfg.step_delay, reason="закрити Лавку")]
        if self._shop_icon(frame) is not None:
            actions.append(PressKey(cfg.bag_key, delay_after=cfg.step_delay, reason="закрити рюкзак"))
        self.state = RepairState.VERIFY_CLOSED
        self.deadline = ctx.now + cfg.step_timeout
        self.close_tries = 0
        self.last_repair = ctx.now
        self.idle_since = None
        self.worn_since = None
        self.broken_since = None
        self.check_broken_at = ctx.now + cfg.step_timeout    # чи зникла червона ознака
        return PipelineResult(actions=actions, status="ремонт: закриваю вікна")

    def _verify_closed(self, ctx: PipelineContext, frame) -> PipelineResult:
        """
        Найважливіший крок: не лишити відкритими рюкзак і Лавку. З відкритими вікнами
        бот не бере цілі й просто простоює до ранку, тому закриття перевіряється,
        а не робиться наосліп.
        """
        cfg: RepairConfig = self.config
        open_now = []
        if find_template(frame, cfg.shop_open) is not None:
            open_now.append("Лавка")
        if self._shop_icon(frame) is not None:
            open_now.append("рюкзак")

        if not open_now:
            self.state = RepairState.IDLE
            set_busy(ctx.shared, "repair", False)
            return PipelineResult(status="", events=["вікна закрито, працюю далі"])

        if ctx.now > self.deadline:
            self.state = RepairState.IDLE
            set_busy(ctx.shared, "repair", False)
            return PipelineResult(
                status="",
                events=[f"!! не вдалось закрити: {', '.join(open_now)} — спробую наступного разу"])

        self.close_tries += 1
        actions = [PressKey(cfg.close_key, delay_after=cfg.step_delay, reason="закрити")]
        if "рюкзак" in open_now and "Лавка" not in open_now:
            actions = [PressKey(cfg.bag_key, delay_after=cfg.step_delay, reason="закрити рюкзак")]
        return PipelineResult(actions=actions, status=f"ремонт: закриваю {', '.join(open_now)}")

    def _give_up(self, ctx: PipelineContext, frame, why: str = "") -> PipelineResult:
        """
        Вікно не з'явилось у відведений час — нічого не клікаємо наосліп і не тиснемо
        клавіші навмання: Esc закривав рюкзак, а наступний B одразу відкривав його знову,
        і той лишався відкритим до ранку. Тому закриваємось тим самим кроком з перевіркою.
        """
        cfg: RepairConfig = self.config
        was = self.state
        self.last_repair = ctx.now
        self.idle_since = None
        events = ([f"ремонт скасовано: {why}"] if why
                  else [] if was is RepairState.WAITING_CONFIRM
                  else [f"ремонт скасовано: не дочекався ({was.value})"])
        if was is RepairState.WAITING_CONFIRM:
            events.append("ремонтувати нічого")
        self.state = RepairState.VERIFY_CLOSED
        self.deadline = ctx.now + cfg.step_timeout
        self.close_tries = 0
        res = self._verify_closed(ctx, frame)
        res.events[:0] = events
        return res
