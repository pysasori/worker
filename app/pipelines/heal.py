"""
Відхіл персонажа: HP нижче порогу — тиснемо банку.

Банка спрацьовує миттєво, її не перериває ні удар, ні рух, тому цей блок працює
завжди: і в бою, і під час ремонту чи приклику пета. Єдине обмеження — кулдаун,
щоб не витратити всю сумку за секунду.

Стан HP кладеться на спільну дошку: його читає не лише лікування (приклик пета
дивиться, чи нас б'ють), і кадр так розбирається один раз на всіх.
"""
from __future__ import annotations

from pydantic import Field

from app.pipelines.actions import PipelineResult, PressKey
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext
from app.pipelines.registry import register
from app.pipelines.shared import SHARED_PLAYER, busy_reasons
from app.vision.player import PlayerBarConfig, read_player_hp


class HealConfig(PipelineConfig):
    heal_key: str = Field(default="f6", title="Клавіша банки")
    heal_below: float = Field(default=0.5, gt=0, le=1, title="Пити нижче HP",
                              description="0.5 = половина смужки")
    cooldown: float = Field(default=10.0, ge=0, title="Пауза між банками, с",
                            description="щоб не випити всю сумку за раз")
    panic_below: float = Field(default=0.25, ge=0, le=1, title="Терміново нижче HP",
                               description="на цьому рівні паузу скорочуємо вдвічі; 0 = не поспішати")
    potion_check: float = Field(default=2.5, ge=0, title="Перевірити банку через, с",
                                json_schema_extra={"tech": True},
                                description="банка додає життя одразу; якщо не додала — її нема")
    potion_misses: int = Field(default=3, ge=1, title="Порожніх банок підряд до тривоги",
                               json_schema_extra={"tech": True})
    warn_every: float = Field(default=300.0, ge=0, title="Повторювати тривогу раз на, с",
                              json_schema_extra={"tech": True})
    bar: PlayerBarConfig = Field(default_factory=PlayerBarConfig, title="Смужка HP персонажа",
                                 json_schema_extra={"tech": True})


@register
class HealPipeline(Pipeline):
    type_name = "heal"
    label = "Відхіл персонажа"
    category = "combat"
    config_model = HealConfig
    provides = frozenset({SHARED_PLAYER})

    def __init__(self, config: HealConfig, window: str = "") -> None:
        super().__init__(config, window)
        self.reset()

    def reset(self) -> None:
        self.last_heal = float("-inf")      # перша банка — одразу, без очікування
        self.pending: tuple[float, float] | None = None   # коли випили і скільки було життя
        self.misses = 0
        self.last_warn = float("-inf")

    def process(self, ctx: PipelineContext) -> PipelineResult:
        cfg: HealConfig = self.config
        reading = read_player_hp(ctx.frame.image, cfg.bar)
        ctx.shared[SHARED_PLAYER] = reading
        if not reading.present:
            return PipelineResult.idle("HP не видно")

        events = self._check_potion(ctx, reading)
        status = f"HP {reading.percent}%"
        if self.misses >= cfg.potion_misses:
            status += " · банок нема"
        if "death_return" in busy_reasons(ctx.shared):
            return PipelineResult(status=f"{status} · персонаж мертвий або в дорозі", events=events)
        if reading.ratio >= cfg.heal_below:
            return PipelineResult(status=status, events=events)

        pause = cfg.cooldown
        if cfg.panic_below and reading.ratio < cfg.panic_below:
            pause = cfg.cooldown / 2                  # мало життя — чекати нема коли
        if ctx.now - self.last_heal < pause:
            return PipelineResult(status=f"{status} · кулдаун", events=events)

        self.last_heal = ctx.now
        self.pending = (ctx.now, reading.ratio)
        return PipelineResult(
            actions=[PressKey(cfg.heal_key, reason="банка")],
            status=status,
            events=events + [f"HP {reading.percent}% < {cfg.heal_below:.0%} -> {cfg.heal_key}"])

    def _check_potion(self, ctx: PipelineContext, reading) -> list[str]:
        """
        Банка додає життя миттєво. Якщо після кількох натискань життя не додалось —
        комірка порожня: банки скінчились. Раніше бот тиснув у порожнечу до самої
        смерті, і в лозі було видно лише «HP 30% -> f6» раз за разом.
        """
        cfg: HealConfig = self.config
        if self.pending is None:
            return []
        at, before = self.pending
        if ctx.now - at < cfg.potion_check:
            if reading.ratio > before + 0.05:            # подіяла раніше за перевірку
                self.pending, self.misses = None, 0
            return []
        self.pending = None
        if reading.ratio > before + 0.05:
            self.misses = 0
            return []
        self.misses += 1
        if self.misses < cfg.potion_misses or ctx.now - self.last_warn < cfg.warn_every:
            return []
        self.last_warn = ctx.now
        return [f"!! банка {cfg.heal_key} не діє {self.misses} разів поспіль — схоже, "
                f"банки скінчились, персонаж помре"]
