"""
Пошук цілі: читає смужку HP цілі, віддає стан на спільну дошку і бере нову ціль,
коли цілі нема.

Це єдиний пайплайн, який дивиться на рамку цілі. Атака й лут працюють з його
результатом, тому кадр аналізується один раз.

Смерть моба: клієнт PW не малює 0% — після смерті рамка просто зникає. Тому падіння
з кількох відсотків у "рамки нема" і є момент смерті (died_now). На цьому кадрі Tab
навмисно НЕ тиснеться, щоб лут встиг спрацювати над трупом; нову ціль беремо
наступним кадром — і лише коли ніхто не зайнятий (див. busy у shared.py).
"""
from __future__ import annotations

from pydantic import Field

from app.core.geometry import Region
from app.pipelines.actions import PipelineResult, PressKey
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext
from app.pipelines.registry import register
from app.pipelines.shared import SHARED_TARGET, TargetInfo, busy_reasons, combat_ready, write_target
from app.vision.bars import scan_bar
from app.vision.nameplate import (
    HostileCheckConfig, TargetKind, classify_target, name_region,
)
from app.vision.text import OcrConfig, best_match, read_line
from app.vision.schemas import BarReading, BarScanConfig


class TargetSearchConfig(PipelineConfig):
    region: Region = Field(default=Region.of(460, 0, 520, 24), title="Зона рамки цілі", json_schema_extra={"tech": True})
    bar: BarScanConfig = Field(default_factory=BarScanConfig, title="Смужка HP цілі", json_schema_extra={"tech": True})

    target_key: str | None = Field(default="tab", title="Клавіша вибору цілі",
                                   description="порожньо = тільки спостерігати")
    drop_key: str | None = Field(default=None, title="Клавіша скидання цілі",
                                 description="порожньо (за замовчуванням) = не скидати, "
                                             "а просто перемкнутись на наступну ціль клавішею вибору")
    min_acquire: int = Field(default=2, ge=1, title="Мінімальна смужка нової цілі, px",
                             json_schema_extra={"tech": True},
                             description="від плям на землі захищає ширина рамки цілі, тому тут "
                                         "поріг маленький: інакше бот не брав назад пораненого "
                                         "моба і той лишався недобитим")
    stale_after: float = Field(default=12.0, ge=0, title="Скинути, якщо HP не падає, с",
                               description="так бот не зависає на власному петі чи недосяжному мобі, "
                                           "який відновлює HP швидше, ніж його б'ють")
    min_progress: int = Field(default=2, ge=0, title="Мінімальний прогрес, px",
                              json_schema_extra={"tech": True},
                              description="на скільки має впасти HP, щоб це вважалось ударом")
    hostile_check: HostileCheckConfig = Field(default_factory=HostileCheckConfig,
                                              title="Це точно моб?", json_schema_extra={"tech": True})
    names: NameFilterConfig = Field(default_factory=lambda: NameFilterConfig(),
                                    title="Вибір мобів за назвою")
    retarget_delay: float = Field(default=1.0, ge=0, title="Пауза між пошуками, с")
    confirm_frames: int = Field(default=2, ge=1, title="Кадрів для підтвердження")
    idle_warn: float = Field(default=120.0, ge=0, title="Попередити про простій через, с",
                             description="0 = мовчати. Інакше бот пише в лог, що давно не має "
                                         "цілі: так видно, чому він стоїть")
    warn_after_rejects: int = Field(default=8, ge=1, title="Попередити після N відмов",
                                    json_schema_extra={"tech": True},
                                    description="щоб було видно, що список мобів не підходить до локації")


class NameFilterConfig(PipelineConfig):
    """
    Кого бити. Назва цілі читається з рамки один раз на ціль, тому це дешево.

    Списки порівнюються НЕЧІТКО: OCR плутає окремі літери, тож «Горный варвао»
    все одно збігається з «Горный варвар». Поріг — min_ratio.
    """

    enabled: bool = Field(default=False, title="Вибирати мобів за назвою")
    allow: list[str] = Field(default_factory=list, title="Бити тільки цих",
                             description="порожньо = бити всіх, крім заборонених")
    deny: list[str] = Field(default_factory=list, title="Не чіпати цих")
    min_ratio: float = Field(default=0.75, gt=0, le=1, title="Поріг схожості",
                             description="0.75 = три чверті літер збіглись")
    ocr: OcrConfig = Field(default_factory=OcrConfig, title="Читання тексту")


@register
class TargetSearchPipeline(Pipeline):
    type_name = "target_search"
    label = "Пошук цілі"
    category = "combat"
    run_order = 100
    config_model = TargetSearchConfig
    provides = frozenset({SHARED_TARGET})

    def __init__(self, config: TargetSearchConfig, window: str = "") -> None:
        super().__init__(config, window)
        self.reset()

    def reset(self) -> None:
        self.idle_since: float | None = None   # відколи немає жодної цілі
        self.skip_current = False      # цю ціль уже відкинули, чекаємо наступну
        self.confirmed = False
        self.full_width: int | None = None
        self.acquired_at: float | None = None
        self.died_at: float | None = None
        self.alive_streak = 0
        self.last_search = float("-inf")  # -inf = ще не шукали, тиснути одразу
        self.last_filled = -1             # найнижче HP цілі, яке бачили (px)
        self.changed_at = 0.0
        self.friendly_streak = 0
        self.seen_name = ""               # назва поточної цілі (читаємо раз на ціль)
        self.rejected = 0                 # скільки цілей поспіль відкинув фільтр
        self.warned = False

    def process(self, ctx: PipelineContext) -> PipelineResult:
        cfg: TargetSearchConfig = self.config
        if not combat_ready(ctx.shared):
            self.confirmed = False
            self.skip_current = False
            self.full_width = None
            self.acquired_at = None
            self.alive_streak = 0
            self.last_filled = -1
            self.friendly_streak = 0
            self.seen_name = ""
            write_target(ctx.shared, TargetInfo(updated_at=ctx.now, updated_tick=ctx.tick))
            return PipelineResult.idle("чекаю перевірку місця")
        reading = scan_bar(ctx.frame.crop(cfg.region), cfg.region, cfg.bar, total=self.full_width)
        res = PipelineResult()

        self.alive_streak = self.alive_streak + 1 if reading.present else 0
        died_now = self.confirmed and not reading.present

        # свого пета й гравців у ціль не беремо: у них назва синя, у мобів — жовта
        if reading.present and cfg.hostile_check.enabled and reading.x0 >= 0 and not self.skip_current:
            region = name_region(reading.x0, reading.filled, reading.row, cfg.hostile_check)
            kind, _, _ = classify_target(ctx.frame.crop(region), cfg.hostile_check)
            if kind is TargetKind.FRIENDLY:
                self.friendly_streak += 1
                if self.friendly_streak >= cfg.confirm_frames:
                    return self._drop(ctx, "це не моб (свій)")
            else:
                self.friendly_streak = 0

        # ціль, яку не виходить убити (недосяжний моб) — скидаємо
        if self.confirmed and reading.present:
            # Прогрес — це НОВИЙ мінімум HP. Просто «змінюється» не годиться: недосяжний
            # моб падає від удару до 50% і відновлюється до 100%, і так по колу —
            # колись бот через це стояв біля нього, поки загинув пет.
            if self.last_filled < 0 or reading.filled < self.last_filled - cfg.min_progress:
                self.last_filled = reading.filled
                self.changed_at = ctx.now
            elif cfg.stale_after and ctx.now - self.changed_at >= cfg.stale_after:
                return self._drop(ctx, "HP не падає")

        if died_now:
            self.confirmed = False
            self.skip_current = False
            self.full_width = None
            self.acquired_at = None
            self.died_at = ctx.now
            res.events.append("ціль мертва")
        elif reading.present and self.alive_streak >= cfg.confirm_frames and not self.skip_current:
            if not self.confirmed and reading.filled < cfg.min_acquire:
                reading.present = False      # надто дрібна смужка, це не ціль
            elif not self.confirmed:
                name_res = self._check_name(ctx, reading)
                if name_res is not None:
                    return name_res
                self.confirmed = True
                self.idle_since = None
                self.acquired_at = ctx.now
                self.last_filled = reading.filled
                self.changed_at = ctx.now
                self.rejected = 0
                self.warned = False
                res.events.append(f"нова ціль: смужка {reading.filled}px від x={reading.x0}")
            if self.full_width is None or reading.filled > self.full_width:
                self.full_width = reading.filled
                reading.total = self.full_width

        write_target(ctx.shared, TargetInfo(
            present=self.confirmed,
            bar=reading,
            acquired_at=self.acquired_at,
            died_at=self.died_at,
            died_now=died_now,
            updated_at=ctx.now,
            updated_tick=ctx.tick,
        ))

        if self.confirmed:
            label = f"ціль {reading}"
            if self.seen_name:
                label += f" · {self.seen_name}"
            return PipelineResult(actions=res.actions, events=res.events, status=label)
        if died_now:
            # цей кадр віддаємо луту, ціль візьмемо наступним
            return PipelineResult(events=res.events, status="ціль мертва")
        busy = busy_reasons(ctx.shared)
        if busy:
            # хтось зайнятий довгою дією (збирає лут) — не перебиваємо новою ціллю
            return PipelineResult(events=res.events, status=f"чекаю: {', '.join(sorted(busy))}")
        res.events += self._idle_warning(ctx)
        if cfg.target_key and ctx.now - self.last_search >= cfg.retarget_delay:
            self.last_search = ctx.now
            self.skip_current = False        # перемкнулись — наступну ціль дивимось наново
            res.actions.append(PressKey(cfg.target_key, reason="пошук цілі"))
            res.status = "шукаю ціль" if not self.seen_name else "беру наступну ціль"
            return res
        res.status = "цілі нема"
        return res

    def _idle_warning(self, ctx: PipelineContext) -> list[str]:
        """
        Бот стоїть без цілі. Колись через це здавалось, що він завис: у лозі година
        тиші, бо поруч просто не було мобів. Тепер причина видно одразу.
        """
        cfg: TargetSearchConfig = self.config
        if not cfg.idle_warn:
            return []
        if self.idle_since is None:
            self.idle_since = ctx.now
            return []
        if ctx.now - self.idle_since < cfg.idle_warn:
            return []
        self.idle_since = ctx.now
        return [f"!! {cfg.idle_warn:.0f}с без цілі — поруч нема мобів, вони не в списку "
                f"або клавіша вибору не діє"]

    def _check_name(self, ctx: PipelineContext, reading) -> PipelineResult | None:
        """
        Прочитати назву нової цілі й вирішити, чи вона нам потрібна.
        None означає «бери цю ціль», інакше повертаємо результат зі скиданням.
        """
        cfg: TargetSearchConfig = self.config
        names = cfg.names
        if not names.enabled or not names.ocr.enabled:
            return None
        region = name_region(reading.x0, reading.filled, reading.row, cfg.hostile_check)
        text = read_line(ctx.frame.crop(region), names.ocr)
        self.seen_name = text
        if not text:
            return None                      # прочитати не вдалось — не вередуємо
        if names.deny and best_match(text, names.deny, names.min_ratio):
            return self._drop(ctx, f"«{text}» у списку заборонених")
        if names.allow and not best_match(text, names.allow, names.min_ratio):
            return self._drop(ctx, f"«{text}» не зі списку потрібних")
        return None

    def _drop(self, ctx: PipelineContext, why: str) -> PipelineResult:
        """Скинути ціль, якою бот зайнятий даремно, і одразу шукати наступну."""
        cfg: TargetSearchConfig = self.config
        self.confirmed = False
        self.full_width = None
        self.acquired_at = None
        self.alive_streak = 0
        self.last_filled = -1
        self.friendly_streak = 0
        self.seen_name = ""            # назва поточної цілі (читаємо раз на ціль)
        self.last_search = ctx.now
        write_target(ctx.shared, TargetInfo(present=False, bar=BarReading(),
                                            died_at=self.died_at, updated_at=ctx.now,
                                            updated_tick=ctx.tick))
        # Клавішу вибору тут НЕ тиснемо: наступну ціль візьме звичайний пошук, коли
        # мине retarget_delay. Інакше на чужому списку мобів бот молотив би по кілька
        # перемикань за секунду. Esc теж не тиснемо: він закриває вікна гри (одного разу
        # так зникла рамка пета), тому за замовчуванням просто перемикаємось клавішею вибору.
        self.skip_current = True
        actions = [PressKey(cfg.drop_key, delay_after=0.15, reason="скинути ціль")] if cfg.drop_key else []
        events = [f"ціль скинуто: {why}"]
        self.rejected += 1
        if (cfg.names.enabled and cfg.names.allow and not self.warned
                and self.rejected >= cfg.warn_after_rejects):
            self.warned = True
            events.append("!! усі цілі поруч не зі списку «Бити тільки цих» — "
                          "перевір список або вимкни фільтр")
        return PipelineResult(actions=actions, status="скинув ціль", events=events)
