"""
Пайплайн клавіш по таймеру: тисне задані клавіші з інтервалом.

Бою він не заважає, але чекає, поки бот зайнятий довгою дією (приклик пета, ремонт):
натиснута скіл-клавіша перериває каст, і піт через це так і не приходив.
"""
from __future__ import annotations

from pydantic import Field, field_validator

from app.pipelines.actions import PipelineResult, PressKey
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext
from app.pipelines.registry import register
from app.pipelines.shared import busy_reasons


class PeriodicKeysConfig(PipelineConfig):
    keys: dict[str, float] = Field(default_factory=dict, title="Клавіші та інтервали",
                                   description="клавіша -> секунди")
    press_on_start: bool = Field(default=True, title="Натиснути на старті")

    @field_validator("keys")
    @classmethod
    def _positive(cls, v: dict[str, float]) -> dict[str, float]:
        bad = [k for k, sec in v.items() if sec <= 0]
        if bad:
            raise ValueError(f"інтервал має бути > 0 (клавіші: {', '.join(bad)})")
        return v


@register
class PeriodicKeysPipeline(Pipeline):
    type_name = "periodic_keys"
    label = "Клавіші по таймеру"
    category = "service"
    config_model = PeriodicKeysConfig

    def __init__(self, config: PeriodicKeysConfig, window: str = "") -> None:
        super().__init__(config, window)
        self.reset()

    def reset(self) -> None:
        cfg: PeriodicKeysConfig = self.config
        start = float("-inf") if cfg.press_on_start else None
        self.last: dict[str, float | None] = {k: start for k in cfg.keys}

    def process(self, ctx: PipelineContext) -> PipelineResult:
        cfg: PeriodicKeysConfig = self.config
        busy = busy_reasons(ctx.shared)
        if busy:
            # таймер не зсуваємо: клавіша натиснеться одразу, щойно бот звільниться
            return PipelineResult.idle(f"чекаю: {', '.join(sorted(busy))}")
        res = PipelineResult()
        for key, every in cfg.keys.items():
            last = self.last.get(key)
            if last is None:                     # press_on_start=False: перший раз через інтервал
                self.last[key] = ctx.now
                continue
            if ctx.now - last >= every:
                res.actions.append(PressKey(key, delay_after=0.05, reason=f"таймер {every:g}с"))
                self.last[key] = ctx.now
        return res
