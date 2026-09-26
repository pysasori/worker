"""
Атака: б'є, поки на дошці жива ціль. Сам на екран не дивиться — стан бере
від пайплайна пошуку цілі (target_search), тому кадр читається один раз.

Нова ціль скидає таймер, щоб перший удар пішов одразу після Tab, а не через інтервал.
"""
from __future__ import annotations

from pydantic import Field

from app.pipelines.actions import PipelineResult, PressKey
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext
from app.pipelines.registry import register
from app.pipelines.shared import SHARED_TARGET, busy_reasons, read_target


class AttackConfig(PipelineConfig):
    key: str = Field(default="f1", title="Клавіша атаки")
    interval: float = Field(default=3.0, gt=0, title="Інтервал, с", description="секунд між ударами")
    stop_below: float = Field(default=0.0, ge=0, le=1, title="Не бити нижче HP",
                              description="0 = бити завжди")


@register
class AttackPipeline(Pipeline):
    type_name = "attack"
    label = "Атака"
    category = "combat"
    config_model = AttackConfig
    requires = frozenset({SHARED_TARGET})

    def __init__(self, config: AttackConfig, window: str = "") -> None:
        super().__init__(config, window)
        self.reset()

    def reset(self) -> None:
        self.last_attack = float("-inf")
        self.seen_target: float | None = None  # acquired_at цілі, яку вже бачили

    def process(self, ctx: PipelineContext) -> PipelineResult:
        cfg: AttackConfig = self.config
        target = read_target(ctx.shared)
        if target is None:
            return PipelineResult.idle("нема даних про ціль")
        if not target.present:
            self.seen_target = None
            return PipelineResult.idle()
        busy = busy_reasons(ctx.shared)
        if busy:
            # хтось зайнятий довгою дією (ремонт, збір лута) — не б'ємо крізь відкриті вікна
            return PipelineResult.idle(f"чекаю: {', '.join(sorted(busy))}")

        if target.acquired_at != self.seen_target:  # ціль змінилась — бити негайно
            self.seen_target = target.acquired_at
            self.last_attack = float("-inf")

        if cfg.stop_below and 0 < target.hp < cfg.stop_below:
            return PipelineResult.idle(f"не б'ю: HP цілі {target.bar.percent}%")
        if ctx.now - self.last_attack < cfg.interval:
            return PipelineResult.idle()
        self.last_attack = ctx.now
        return PipelineResult(actions=[PressKey(cfg.key, reason="атака")], status="б'ю")
