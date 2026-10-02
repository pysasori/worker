"""
Сторож діалогів: помічає модальне вікно гри, яке зависло на екрані, і закриває його.

Навіщо: один такий діалог («Эта функция пока недоступна», «Ви впевнені?») з'їдає
клавіші й бот усю ніч тисне в порожнечу. Сторож дивиться на ту саму ознаку, що й
ремонт, тому нічого нового калібрувати не треба.

Безпека: тиснемо клавішу ЗАКРИТТЯ (Esc), а не підтвердження — жодне рішення в грі
випадково не підтверджується. І мовчимо, поки хтось зайнятий довгою дією: під час
ремонту такий діалог очікуваний і закривати його не можна.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from pydantic import Field

from app.pipelines.actions import PipelineResult, PressKey
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext
from app.pipelines.registry import register
from app.pipelines.shared import busy_reasons
from app.core.geometry import Region
from app.core.background import Probe
from app.vision.template import TemplateSpec, find_template
from app.vision.ui import UiMarker, marker_present

log = logging.getLogger("bot")
ROOT = Path(__file__).resolve().parents[2]


class DialogGuardConfig(PipelineConfig):
    templates: list[TemplateSpec] = Field(
        default_factory=lambda: [
            # шукаємо лише там, де ці вікна з'являються: пошук по всьому кадру коштував
            # 86 мс щотіку — більше за всі інші блоки разом, і бот через це «підвисав»
            TemplateSpec(name="repair_confirm.png", threshold=0.7,      # кнопка «Да (Y)»
                         area=Region.of(420, 380, 600, 320)),
            TemplateSpec(name="drop_coins_title.png", threshold=0.8,    # «Бросить монеты»
                         area=Region.of(420, 700, 600, 260)),
        ],
        title="Ознаки діалогів", json_schema_extra={"tech": True},
        description="шукаються за виглядом. Раніше тут були плями кольору у фіксованих "
                    "місцях, але їх давали підписи й монети на землі, і сторож тиснув Esc "
                    "раз на кілька хвилин без жодного діалогу")
    markers: list[UiMarker] = Field(default_factory=list, title="Додаткові ознаки (колір)",
                                    json_schema_extra={"tech": True})
    windows: list[TemplateSpec] = Field(
        default_factory=lambda: [
            TemplateSpec(name="shop_title.png", threshold=0.7),
            TemplateSpec(name="bag_title.png", threshold=0.8),
        ],
        title="Вікна гри, які не можна лишати відкритими", json_schema_extra={"tech": True},
        description="рюкзак і «Лавка». Поки таке вікно на екрані, гра не приймає клавіші: "
                    "бот не п'є банку, не кличе пета й не б'є, а моби б'ють. Через забутий "
                    "рюкзак персонаж за ніч загинув 40 разів")
    window_check_every: float = Field(default=5.0, ge=0, title="Перевіряти вікна раз на, с",
                                      json_schema_extra={"tech": True},
                                      description="шукаються по всьому кадру, тому рідше за діалоги")
    bag_key: str = Field(default="b", title="Клавіша рюкзака")
    close_key: str = Field(default="esc", title="Клавіша закриття")
    confirm_frames: int = Field(json_schema_extra={"tech": True}, default=3, ge=1, title="Кадрів підряд",
                                description="щоб не сплутати з миготінням")
    cooldown: float = Field(default=3.0, ge=0, title="Пауза між спробами, с")
    check_every: float = Field(default=0.5, ge=0, title="Перевіряти раз на, с",
                               json_schema_extra={"tech": True},
                               description="діалог висить секундами, дивитись щокадру немає сенсу")
    calm_check_every: float = Field(
        default=1.5, ge=0, title="Перевіряти у спокої раз на, с",
        json_schema_extra={"tech": True},
        description="поки жодної ознаки діалога не було, шукаємо рідко (кожен пошук ~8 мс на вікно); "
                    "щойно щось побачили — підтверджуємо щопівсекунди (check_every)")
    snapshot_dir: str = Field(default="logs/shots", title="Куди зберігати знімок діалогу",
                              json_schema_extra={"tech": True},
                              description="порожньо = не зберігати. Інакше кадр лягає на диск разом "
                                          "з назвою ознаки, яка спрацювала: без цього по логу не "
                                          "відрізнити справжній діалог від випадкового збігу в бою")
    snapshot_every: float = Field(default=600.0, ge=0, title="Знімок не частіше ніж раз на, с",
                                  json_schema_extra={"tech": True})


@register
class DialogGuardPipeline(Pipeline):
    type_name = "dialog_guard"
    label = "Сторож діалогів"
    category = "service"
    config_model = DialogGuardConfig

    def __init__(self, config: DialogGuardConfig, window: str = "") -> None:
        super().__init__(config, window)
        self.reset()

    def reset(self) -> None:
        self.probe, self.win_probe = Probe(), Probe()
        self.seen = 0
        self.last_close = float("-inf")
        self.last_check = float("-inf")
        self.window_seen = 0
        self.last_window_check = float("-inf")
        self.last_shot = float("-inf")

    def process(self, ctx: PipelineContext) -> PipelineResult:
        cfg: DialogGuardConfig = self.config
        if busy_reasons(ctx.shared):
            self.seen = 0
            return PipelineResult.idle()

        interval = cfg.check_every if (self.seen or self.probe.pending) else max(cfg.check_every,
                                                                              cfg.calm_check_every)
        due = ctx.now - self.last_check >= interval
        if due:
            self.last_check = ctx.now
        image = ctx.frame.image
        stray = self._stray_window(ctx, image)
        if stray is not None:
            return stray
        ready, hit = self.probe.step(self._look, image, due=due)
        if not ready:
            return PipelineResult.idle("бачу діалог" if self.seen else "")
        if hit is None:
            self.seen = 0
            return PipelineResult.idle()

        self.seen += 1
        if self.seen < cfg.confirm_frames or ctx.now - self.last_close < cfg.cooldown:
            return PipelineResult.idle(f"бачу діалог ({hit})")
        self.last_close = ctx.now
        self.seen = 0
        shot = self._snapshot(ctx, "dialog")
        return PipelineResult(
            actions=[PressKey(cfg.close_key, delay_after=0.3, reason="закрити діалог")],
            status="закриваю діалог",
            events=[f"на екрані висів діалог ({hit}) — закрив"
                    + (f", знімок {shot}" if shot else "")])

    def _look(self, image):
        cfg: DialogGuardConfig = self.config
        return (next(("колір" for m in cfg.markers if marker_present(image, m)), None)
                or next((t.name for t in cfg.templates if find_template(image, t) is not None), None))

    def _snapshot(self, ctx: PipelineContext, why: str) -> str | None:
        """Кадр на диск: інакше по логу не сказати, що саме бот побачив."""
        cfg: DialogGuardConfig = self.config
        if not cfg.snapshot_dir or ctx.now - self.last_shot < cfg.snapshot_every:
            return None
        self.last_shot = ctx.now
        folder = ROOT / cfg.snapshot_dir
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{datetime.now():%Y%m%d-%H%M%S}-{why}.png"
            ctx.frame.image.save(path)
            try:
                return str(path.relative_to(ROOT))
            except ValueError:
                return str(path)
        except OSError as exc:
            log.warning("[%s] не вдалось зберегти знімок: %s", self.window, exc)
            return None

    def _stray_window(self, ctx: PipelineContext, image) -> PipelineResult | None:
        """
        Рюкзак чи «Лавка», які лишились відкритими після продажу або ремонту.
        Поки вони на екрані, гра їсть усі клавіші — це найдорожча поламка з усіх,
        бо зовні бот працює (цілі беруться мишею), а насправді не лікується й гине.
        Коли вікна відкрив хтось із пайплайнів, ми мовчимо: busy_reasons не порожній.
        """
        cfg: DialogGuardConfig = self.config
        if not cfg.windows:
            return None
        due = ctx.now - self.last_window_check >= cfg.window_check_every
        if due:
            self.last_window_check = ctx.now
        ready, open_now = self.win_probe.step(
            lambda: [t for t in cfg.windows if find_template(image, t) is not None], due=due)
        if not ready:
            return None
        if not open_now:
            self.window_seen = 0
            return None
        self.window_seen += 1
        if self.window_seen < cfg.confirm_frames or ctx.now - self.last_close < cfg.cooldown:
            return PipelineResult.idle("вікно гри відкрите")
        self.last_close = ctx.now
        # «Лавка» знімається Esc, рюкзак — своєю клавішею; коли відкриті обидва,
        # спершу Лавка, рюкзак дістанеться наступній перевірці
        shop_open = any(t.name.startswith("shop") for t in open_now)
        key = cfg.close_key if shop_open else cfg.bag_key
        names = "«Лавка»" if shop_open else "рюкзак"
        return PipelineResult(
            actions=[PressKey(key, delay_after=0.3, reason="закрити вікно")],
            status=f"закриваю {names}",
            events=[f"{names} лишився відкритим — закриваю, бо гра не приймає клавіші"])
