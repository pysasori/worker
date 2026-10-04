"""
Продаж луту в Лавці.

Порядок такий самий, як робить гравець мишею:

    рюкзак (B) -> іконка «Лавка» -> перетягнути предмет у «Продажа»
    -> якщо гра спитала «Кол-во», тиснемо «Максимум» і «Принять»
    -> коли всі лоти зібрані, тиснемо «Продать» -> закриваємо вікна

Які комірки продавати, вибирає користувач: за замовчуванням це останній рядок
рюкзака, куди складається свіжий лут. Комірка записується як «рядок:стовпець»,
рахуючи з одиниці, тому «4:3» — третя комірка четвертого рядка.

Усе прив'язано до заголовків вікон, а не до координат екрана: рюкзак і Лавку в грі
можна тягати мишею, і одного разу ремонт через це вже ламався.
"""
from __future__ import annotations

from enum import Enum

from pydantic import Field

from app.core.geometry import Point, Region
from app.pipelines.actions import ClickAt, DragTo, PipelineResult, PressKey
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext
from app.pipelines.registry import register
from app.pipelines.shared import busy_reasons, read_target, set_busy
from app.vision.icons import IconSearchConfig
from app.vision.template import TemplateSpec, find_template, match

BUSY = "sell"


class SellState(str, Enum):
    IDLE = "idle"
    OPENING_BAG = "opening_bag"
    OPENING_SHOP = "opening_shop"
    ARRANGE = "arrange"            # розсуваємо вікна, щоб не перекривались
    MOVING = "moving"              # перетягуємо предмети в лоти
    AMOUNT = "amount"              # гра спитала кількість
    SELLING = "selling"            # тиснемо «Продать»
    CONFIRM = "confirm"            # «Вы на самом деле хотите продать...»
    CLOSING = "closing"


def _last_row(row: int = 4, columns: int = 8) -> list[str]:
    return [f"{row}:{c}" for c in range(1, columns + 1)]


class SellConfig(PipelineConfig):
    every: float = Field(default=900.0, gt=0, title="Продавати раз на, с",
                         description="за замовчуванням 15 хвилин")
    cells: list[str] = Field(default_factory=_last_row, title="Комірки рюкзака на продаж",
                             description="«рядок:стовпець» з одиниці; за замовчуванням увесь "
                                         "останній рядок, куди падає свіжий лут")
    only_out_of_combat: bool = Field(default=True, title="Лише поза боєм")
    bag_key: str = Field(default="b", title="Клавіша рюкзака")
    close_key: str = Field(default="esc", title="Клавіша закриття")

    step_timeout: float = Field(default=9.0, gt=0, title="Чекати вікно, с", json_schema_extra={"tech": True})
    max_total: float = Field(default=90.0, gt=0, title="Найдовший продаж, с", json_schema_extra={"tech": True},
                             description="стеля на весь цикл: що б не сталось, вікна закриються, "
                                         "а бот повернеться до бою")
    hover_delay: float = Field(default=0.3, ge=0, title="Наведення перед дією, с",
                               json_schema_extra={"tech": True})
    step_delay: float = Field(default=0.6, ge=0, title="Пауза після дії, с", json_schema_extra={"tech": True})

    bag_title: TemplateSpec = Field(
        default_factory=lambda: TemplateSpec(name="bag_title.png", threshold=0.8),
        title="Заголовок «Рюкзак»", json_schema_extra={"tech": True})
    shop_title: TemplateSpec = Field(
        default_factory=lambda: TemplateSpec(name="shop_title.png", threshold=0.7),
        title="Заголовок «Лавка»", json_schema_extra={"tech": True})
    park_tolerance: int = Field(default=40, ge=0, title="Допуск місця вікна, px",
                                json_schema_extra={"tech": True})
    park_offset: int = Field(default=430, ge=0, title="Відсунути «Лавку» від рюкзака на, px",
                             json_schema_extra={"tech": True},
                             description="0 = ставити в «Куди ставити «Лавку»». Інакше вбік від рюкзака: "
                                         "на (300,300) вона накривала ліву половину рюкзака, і перші "
                                         "чотири комірки рядка не продавались — бот тягнув з-під неї")
    park_edge: int = Field(default=1250, ge=0, title="Далі цього краю не ставити, px",
                           json_schema_extra={"tech": True})
    shop_park: Point = Field(default=Point(x=300, y=300), title="Куди ставити «Лавку» (запасне)",
                             json_schema_extra={"tech": True},
                             description="вікна в грі відкриваються одне поверх одного і "
                                         "перекривають то комірки рюкзака, то кнопку «Продать», "
                                         "тому бот розставляє їх по різних кутах")
    bag_park: Point = Field(default=Point(x=1050, y=300), title="Куди ставити «Рюкзак»",
                            json_schema_extra={"tech": True},
                            description="зараз не використовується: рюкзак не соваємо, бо одного "
                                        "разу він поїхав за край екрана. Соваємо лише «Лавку»")
    confirm: TemplateSpec = Field(
        default_factory=lambda: TemplateSpec(name="repair_confirm.png", threshold=0.7),
        title="Кнопка «Да» в підтвердженні", json_schema_extra={"tech": True},
        description="після «Продать» гра перепитує, чи справді продаємо")
    amount_title: TemplateSpec = Field(
        default_factory=lambda: TemplateSpec(name="amount_title.png", threshold=0.8),
        title="Вікно «Кол-во»", json_schema_extra={"tech": True})
    shop_offset: Point = Field(default=Point(x=22, y=424), title="Іконка «Лавка» від заголовка рюкзака",
                               json_schema_extra={"tech": True})
    shop_icon_search: IconSearchConfig | None = Field(default=None, title="Уточнення іконки",
                                                      json_schema_extra={"tech": True})

    cell_first: Point = Field(default=Point(x=-122, y=285), title="Перша комірка від заголовка рюкзака",
                              json_schema_extra={"tech": True})
    cell_step: Point = Field(default=Point(x=33, y=34), title="Крок комірок, px",
                             json_schema_extra={"tech": True})
    cell_size: int = Field(default=26, gt=4, title="Сторона комірки, px", json_schema_extra={"tech": True})
    protected: list[TemplateSpec] = Field(
        default_factory=lambda: [TemplateSpec(name="protect_book.png", threshold=0.75)],
        title="Предмети, які не продавати", json_schema_extra={"tech": True},
        description="знімки іконок (assets/templates). Комірка, що збіглась із будь-яким, "
                    "пропускається: так жовта книжка з лавки Karasu не йде в лоти")
    empty_cell: TemplateSpec = Field(
        default_factory=lambda: TemplateSpec(name="bag_empty_cell.png", threshold=0.7),
        title="Знімок порожньої комірки", json_schema_extra={"tech": True},
        description="комірку звіряємо з цим знімком: схожа — порожня, інакше там предмет. "
                    "Кольорові правила не працювали: темні предмети рівні, як порожнє місце")
    sell_panel: TemplateSpec = Field(
        default_factory=lambda: TemplateSpec(name="sell_panel.png", threshold=0.8),
        title="Напис «Продажа»", json_schema_extra={"tech": True},
        description="від нього рахуються лоти й кнопка: висота вікна «Лавка» гуляє залежно "
                    "від вмісту, тому від заголовка вікна міряти не можна")
    panel_near_shop: int = Field(default=260, ge=0, title="Шукати «Продажу» біля «Лавки», px",
                                 json_schema_extra={"tech": True},
                                 description="0 = по всьому екрану. Інакше лише під заголовком «Лавка»: "
                                             "напис у чаті збігався на 0.79, і бот перетягував лут "
                                             "у чат замість лотів")
    lot_first: Point = Field(default=Point(x=-16, y=23), title="Перший лот від напису «Продажа»",
                             json_schema_extra={"tech": True})
    lot_step: Point = Field(default=Point(x=33, y=32), title="Крок лотів, px",
                            json_schema_extra={"tech": True})
    lot_columns: int = Field(default=4, ge=1, title="Лотів у рядку", json_schema_extra={"tech": True})
    sell_button: Point = Field(default=Point(x=71, y=142), title="Кнопка «Продать» від напису",
                               json_schema_extra={"tech": True})
    amount_max: Point = Field(default=Point(x=9, y=52), title="Кнопка «Максимум» від заголовка",
                              json_schema_extra={"tech": True})
    amount_ok: Point = Field(default=Point(x=110, y=52), title="Кнопка «Принять» від заголовка",
                             json_schema_extra={"tech": True})


@register
class SellPipeline(Pipeline):
    type_name = "sell"
    label = "Продаж луту"
    category = "service"
    config_model = SellConfig

    def __init__(self, config: SellConfig, window: str = "") -> None:
        super().__init__(config, window)
        self.reset()

    def reset(self) -> None:
        self.state = SellState.IDLE
        self.deadline = 0.0
        self.started = 0.0
        self.last_sell: float | None = None
        self.queue: list[str] = []          # комірки, які ще не переклали
        self.lots = 0                       # скільки вже поклали в лоти
        self.bag_at: Point | None = None       # де бачили вікна востаннє: вони перекривають
        self.shop_at: Point | None = None      # одне одного, і заголовок може бути не видний
        self.shop_title_at: Point | None = None
        self.close_at = 0.0
        self.why_closed = "лут продано"     # з чим прийшли до закриття вікон

    # ---- геометрія ---------------------------------------------------------------
    def _cell_point(self, bag: Point, cell: str) -> Point | None:
        cfg: SellConfig = self.config
        try:
            row, col = (int(part) for part in cell.split(":"))
        except ValueError:
            return None
        if row < 1 or col < 1:
            return None
        return Point(x=bag.x + cfg.cell_first.x + cfg.cell_step.x * (col - 1),
                     y=bag.y + cfg.cell_first.y + cfg.cell_step.y * (row - 1))

    def _has_item(self, frame, point: Point) -> bool:
        """Чи є що тягнути: звіряємо комірку зі знімком порожньої."""
        cfg: SellConfig = self.config
        half = cfg.cell_size // 2 + 4                 # трохи запасу під пошук зразка
        crop = frame.crop((point.x - half, point.y - half, point.x + half, point.y + half))
        return match(crop, cfg.empty_cell)[0] is None

    def _is_protected(self, frame, point: Point) -> str | None:
        """Назва знімка, якщо в комірці предмет із списку «не продавати»."""
        cfg: SellConfig = self.config
        half = cfg.cell_size // 2 + 4
        crop = frame.crop((point.x - half, point.y - half, point.x + half, point.y + half))
        for spec in cfg.protected:
            if match(crop, spec)[0] is not None:
                return spec.name
        return None

    def _lot_point(self, panel: Point, index: int) -> Point:
        cfg: SellConfig = self.config
        row, col = divmod(index, cfg.lot_columns)
        return Point(x=panel.x + cfg.lot_first.x + cfg.lot_step.x * col,
                     y=panel.y + cfg.lot_first.y + cfg.lot_step.y * row)

    # ---- цикл ---------------------------------------------------------------------
    def process(self, ctx: PipelineContext) -> PipelineResult:
        cfg: SellConfig = self.config
        if self.state not in (SellState.IDLE, SellState.CLOSING) and ctx.now - self.started > cfg.max_total:
            return self._give_up(ctx, ctx.frame.image, f"забагато часу ({self.state.name})")
        result = self._step(ctx)
        if result.actions and self.state is not SellState.IDLE:
            # дія блокує тік (наведення, перетягування, пауза), а таймер крок відлічує від
            # кадру, зробленого ДО неї: на повільній машині 9 с з'їдали самі перетягування
            self.deadline += sum(getattr(a, "hover_delay", 0.0) + getattr(a, "delay_after", 0.0)
                                 + (1.0 if isinstance(a, DragTo) else 0.0) for a in result.actions)
        return result

    def _step(self, ctx: PipelineContext) -> PipelineResult:
        cfg: SellConfig = self.config
        frame = ctx.frame.image

        if self.state is SellState.IDLE:
            return self._idle(ctx, frame)
        if self.state is SellState.CLOSING:
            return self._closing(ctx, frame)
        if ctx.now > self.deadline:
            return self._give_up(ctx, frame, f"не дочекався вікна ({self.state.name})")

        if self.state is SellState.OPENING_BAG:
            if find_template(frame, cfg.bag_title) is not None:
                return self._open_shop(ctx, frame)
            return PipelineResult.idle("продаж: чекаю рюкзак")
        if self.state is SellState.OPENING_SHOP:
            if find_template(frame, cfg.shop_title) is not None:
                self.state = SellState.ARRANGE
                return PipelineResult.idle("продаж: розсуваю вікна")
            return PipelineResult.idle("продаж: чекаю Лавку")
        if self.state is SellState.ARRANGE:
            return self._arrange(ctx, frame)
        if self.state is SellState.AMOUNT:
            return self._amount(ctx, frame)
        if self.state is SellState.CONFIRM:
            return self._confirm(ctx, frame)
        if self.state is SellState.MOVING:
            return self._moving(ctx, frame)
        return self._sell(ctx, frame)

    def _idle(self, ctx: PipelineContext, frame) -> PipelineResult:
        cfg: SellConfig = self.config
        if self.last_sell is None:
            self.last_sell = ctx.now              # від старту не продаємо одразу
            return PipelineResult.idle()
        if ctx.now - self.last_sell < cfg.every or not cfg.cells:
            return PipelineResult.idle()
        if cfg.only_out_of_combat:
            target = read_target(ctx.shared)
            if target is not None and target.present:
                return PipelineResult.idle("час продавати · добиваю ціль")
        others = busy_reasons(ctx.shared) - {BUSY}
        if others:
            return PipelineResult.idle(f"час продавати · чекаю: {', '.join(sorted(others))}")

        set_busy(ctx.shared, BUSY, True)
        self.queue = list(cfg.cells)
        self.lots = 0
        self.started = ctx.now
        self.deadline = ctx.now + cfg.step_timeout
        events = [f"час продавати лут ({len(self.queue)} комірок)"]
        if find_template(frame, cfg.bag_title) is not None:
            return self._open_shop(ctx, frame, events)
        self.state = SellState.OPENING_BAG
        return PipelineResult(actions=[PressKey(cfg.bag_key, delay_after=cfg.step_delay, reason="рюкзак")],
                              status="продаж: відкриваю рюкзак", events=events)

    def _open_shop(self, ctx: PipelineContext, frame, events: list[str] | None = None) -> PipelineResult:
        cfg: SellConfig = self.config
        bag = self.bag_at or find_template(frame, cfg.bag_title)
        if bag is None:
            return self._give_up(ctx, frame, "рюкзак зник")
        self._remember(frame)
        if find_template(frame, cfg.shop_title) is not None:
            self.state = SellState.ARRANGE          # спершу переконаємось, що вікна не накладені
            return PipelineResult(status="продаж: розсуваю вікна", events=events or [])
        icon = Point(x=bag.x + cfg.shop_offset.x, y=bag.y + cfg.shop_offset.y)
        self.state = SellState.OPENING_SHOP
        self.deadline = ctx.now + cfg.step_timeout
        return PipelineResult(
            actions=[ClickAt(icon.x, icon.y, hover_delay=cfg.hover_delay,
                             delay_after=cfg.step_delay, reason="відкрити Лавку")],
            status="продаж: відкриваю Лавку", events=events or [])

    def _remember(self, frame) -> tuple[Point | None, Point | None]:
        """
        Де зараз вікна. Лоти шукаємо за написом «Продажа», а не за заголовком «Лавка»:
        висота вікна гуляє, і предмет летів повз лот — саме через це лут не продавався.
        Якщо напис перекритий іншим вікном, беремо останній відомий.
        """
        cfg: SellConfig = self.config
        self.bag_at = find_template(frame, cfg.bag_title) or self.bag_at
        self.shop_title_at = find_template(frame, cfg.shop_title) or self.shop_title_at
        self.shop_at = find_template(frame, self._panel_spec()) or self.shop_at
        return self.bag_at, self.shop_at

    def _panel_spec(self) -> TemplateSpec:
        """Напис «Продажа» шукаємо під заголовком «Лавка», а не по всьому екрану."""
        cfg: SellConfig = self.config
        title = self.shop_title_at
        if not (cfg.panel_near_shop and title):
            return cfg.sell_panel
        half = cfg.panel_near_shop
        area = Region.of(max(title.x - half, 0), max(title.y - 20, 0), half * 2, half * 2)
        return cfg.sell_panel.model_copy(update={"area": area})

    def _arrange(self, ctx: PipelineContext, frame) -> PipelineResult:
        """
        Відсунути «Лавку» від рюкзака. Вона відкривається поверх нього і накриває
        нижні рядки — саме ті, звідки бот бере лут, — і перетягування тягало вікно.
        """
        cfg: SellConfig = self.config
        self._remember(frame)
        bag, shop = self.bag_at, self.shop_title_at
        if bag is None or shop is None:
            # заголовок рюкзака часто накритий самою «Лавкою» — тому й дивимось на
            # запам'ятовані позиції, інакше продаж зривався на рівному місці
            return self._give_up(ctx, frame, "вікно зникло під час розкладання")
        self.deadline = ctx.now + cfg.step_timeout

        park = self._park_point(bag)
        far = cfg.park_tolerance
        if abs(shop.x - park.x) > far or abs(shop.y - park.y) > far:
            self.shop_at = self.shop_title_at = None      # позиції зараз зміняться
            side = "праворуч" if park.x > bag.x else "ліворуч"
            return PipelineResult(
                actions=[DragTo(shop.x, shop.y, park.x, park.y,
                                hover_delay=cfg.hover_delay, delay_after=cfg.step_delay,
                                reason=f"поставити «Лавку» {side}")],
                status="продаж: розставляю вікна",
                events=[f"ставлю «Лавку» {side} від рюкзака: вікна накривають одне одного"])
        self.state = SellState.MOVING
        return PipelineResult.idle("продаж: перекладаю лут")

    def _park_point(self, bag: Point) -> Point:
        """Куди відсунути «Лавку»: вбік від рюкзака, щоб та не накрила його комірки."""
        cfg: SellConfig = self.config
        if not cfg.park_offset:
            return cfg.shop_park
        right = bag.x + cfg.park_offset
        x = right if right <= cfg.park_edge else bag.x - cfg.park_offset
        return Point(x=max(x, 0), y=cfg.shop_park.y)

    def _moving(self, ctx: PipelineContext, frame) -> PipelineResult:
        cfg: SellConfig = self.config
        if find_template(frame, cfg.amount_title) is not None:
            self.state = SellState.AMOUNT
            self.deadline = ctx.now + cfg.step_timeout
            return PipelineResult.idle("продаж: гра питає кількість")
        bag, shop = self._remember(frame)
        if bag is None or shop is None:
            return self._give_up(ctx, frame, "вікно так і не відкрилось")
        if not self.queue:
            return self._sell(ctx, frame)

        if find_template(frame, cfg.bag_title) is None:
            # тягнути за запам'ятованими координатами не можна: якщо рюкзак з'їхав,
            # миша хапає не предмет, а саме вікно — його й смикало по екрану
            return PipelineResult.idle("продаж: чекаю, поки видно рюкзак")
        cell = self.queue.pop(0)
        src = self._cell_point(bag, cell)
        if src is None:
            return PipelineResult.idle(f"продаж: пропускаю комірку «{cell}»")
        if not self._has_item(frame, src):
            return PipelineResult.idle(f"продаж: комірка {cell} порожня")
        keep = self._is_protected(frame, src)
        if keep:
            return PipelineResult(status=f"продаж: комірка {cell} — не продаю",
                                  events=[f"комірка {cell}: це «{keep}», не продаю"])
        dst = self._lot_point(shop, self.lots)
        self.lots += 1
        self.deadline = ctx.now + cfg.step_timeout
        return PipelineResult(
            actions=[DragTo(src.x, src.y, dst.x, dst.y, hover_delay=cfg.hover_delay,
                            delay_after=cfg.step_delay, reason=f"лот з комірки {cell}")],
            status=f"продаж: комірка {cell} ({len(self.queue)} лишилось)")

    def _amount(self, ctx: PipelineContext, frame) -> PipelineResult:
        """Гра спитала кількість: беремо максимум і підтверджуємо."""
        cfg: SellConfig = self.config
        dialog = find_template(frame, cfg.amount_title)
        if dialog is None:
            self.state = SellState.MOVING
            return PipelineResult.idle("продаж: перекладаю лут")
        self.deadline = ctx.now + cfg.step_timeout
        return PipelineResult(
            actions=[
                ClickAt(dialog.x + cfg.amount_max.x, dialog.y + cfg.amount_max.y,
                        hover_delay=cfg.hover_delay, delay_after=cfg.step_delay, reason="максимум"),
                ClickAt(dialog.x + cfg.amount_ok.x, dialog.y + cfg.amount_ok.y,
                        hover_delay=cfg.hover_delay, delay_after=cfg.step_delay, reason="підтвердити"),
            ],
            status="продаж: підтверджую кількість")

    def _sell(self, ctx: PipelineContext, frame) -> PipelineResult:
        cfg: SellConfig = self.config
        _, panel = self._remember(frame)
        if panel is None:
            return self._give_up(ctx, frame, "Лавка закрилась")
        shop = panel
        if not self.lots:
            # рюкзак і «Лавку» треба закрити ОБОВ'ЯЗКОВО: з відкритим вікном гра не
            # приймає клавіші, і бот не п'є банку, не кличе пета, не б'є — персонаж
            # просто стоїть під мобами й гине (перевірено логами: 40 смертей за ніч)
            self.state = SellState.CLOSING
            self.deadline = ctx.now + cfg.step_timeout
            self.close_at = ctx.now
            self.why_closed = "продавати не було чого"
            return PipelineResult(status="продаж: закриваю вікна",
                                  events=["продавати не було чого, закриваю вікна"])
        self.state = SellState.CONFIRM
        self.deadline = ctx.now + cfg.step_timeout
        return PipelineResult(
            actions=[ClickAt(shop.x + cfg.sell_button.x, shop.y + cfg.sell_button.y,
                             hover_delay=cfg.hover_delay, delay_after=cfg.step_delay, reason="продати")],
            status="продаж: тисну «Продать»",
            events=[f"продаю {self.lots} лотів"])

    def _confirm(self, ctx: PipelineContext, frame) -> PipelineResult:
        """Гра перепитує, чи справді продаємо. Без цього кроку лот просто лишався на місці."""
        cfg: SellConfig = self.config
        yes = find_template(frame, cfg.confirm)
        if yes is None:
            return PipelineResult.idle("продаж: чекаю підтвердження")
        self.state = SellState.CLOSING
        self.deadline = ctx.now + cfg.step_timeout
        self.close_at = ctx.now + cfg.step_delay * 2
        self.why_closed = "лут продано"
        return PipelineResult(
            actions=[ClickAt(yes.x, yes.y, hover_delay=cfg.hover_delay,
                             delay_after=cfg.step_delay, reason="підтвердити продаж")],
            status="продаж: підтверджую", events=["лут продано"])

    def _closing(self, ctx: PipelineContext, frame) -> PipelineResult:
        cfg: SellConfig = self.config
        shop = find_template(frame, cfg.shop_title)
        bag = find_template(frame, cfg.bag_title)
        if shop is None and bag is None:
            return self._done(ctx, f"{self.why_closed}, вікна закрито")
        if ctx.now > self.deadline:
            left = ", ".join(n for n, f in (("Лавка", shop), ("рюкзак", bag)) if f is not None)
            return self._done(ctx, f"!! не вдалось закрити: {left} — з відкритим вікном "
                                   f"гра не приймає клавіші")
        if ctx.now < self.close_at:
            return PipelineResult.idle("продаж: закриваю вікна")
        self.close_at = ctx.now + cfg.step_delay * 2
        # Esc закриває «Лавку»; рюкзак сам по собі Esc не бере — його знімає своя клавіша
        key = cfg.close_key if shop is not None else cfg.bag_key
        return PipelineResult(actions=[PressKey(key, delay_after=cfg.step_delay,
                                                reason="закрити")],
                              status="продаж: закриваю вікна")

    def _done(self, ctx: PipelineContext, why: str) -> PipelineResult:
        self.state = SellState.IDLE
        self.last_sell = ctx.now
        self.queue, self.lots = [], 0
        self.bag_at = self.shop_at = self.shop_title_at = None
        set_busy(ctx.shared, BUSY, False)
        return PipelineResult(status="", events=[why])

    def _give_up(self, ctx: PipelineContext, frame, why: str) -> PipelineResult:
        self.state = SellState.CLOSING
        self.deadline = ctx.now + self.config.step_timeout
        self.close_at = ctx.now
        self.last_sell = ctx.now
        return PipelineResult(status="продаж: закриваю вікна", events=[f"!! продаж скасовано: {why}"])
