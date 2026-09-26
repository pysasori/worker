"""
Годування пета: смужка ситості під смужкою HP, при падінні нижче порогу — клавіша корму.

Смужка золота (≈230,172,70) на темному тлі, тому шукається так само за ознаками,
як і HP: відрізок потрібної ширини з пікселів «заповнене або порожнє». Порожня
смужка (0%) — це голодний пет, а не відсутня рамка, тому годуємо і в цьому випадку.
"""
from __future__ import annotations

from pydantic import Field

from app.pipelines.actions import PipelineResult, PressKey
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext
from app.pipelines.registry import register
from app.pipelines.shared import SHARED_PET_FOOD, SHARED_PET_FRAME
from app.vision.pet_frame import read_bar
from app.vision.schemas import BarReading


class PetFeedConfig(PipelineConfig):
    feed_key: str = Field(default="f8", title="Клавіша корму")
    feed_below: float = Field(default=0.5, gt=0, le=1, title="Годувати нижче",
                              description="0.5 = половина смужки")
    cooldown: float = Field(default=40.0, ge=0, title="Кулдаун корму, с")
    give_up_after: int = Field(default=8, ge=0, title="Спроб без ефекту",
                               description="0 = не здаватись. Одна порція додає менше відсотка, "
                                           "тому рахуємо ріст від початку серії, а не між сусідніми")


@register
class PetFeedPipeline(Pipeline):
    type_name = "pet_feed"
    label = "Годування пета"
    category = "pet"
    config_model = PetFeedConfig
    provides = frozenset({SHARED_PET_FOOD})
    requires = frozenset({SHARED_PET_FRAME})

    def __init__(self, config: PetFeedConfig, window: str = "") -> None:
        super().__init__(config, window)
        self.reset()

    def reset(self) -> None:
        self.last_feed = float("-inf")   # перше годування — одразу
        self.tries = 0                   # годувань у поточній серії
        self.level_at_start = -1         # рівень смужки на початку серії
        self.gave_up = False

    def process(self, ctx: PipelineContext) -> PipelineResult:
        cfg: PetFeedConfig = self.config
        frame = ctx.shared.get(SHARED_PET_FRAME)
        if frame is None:
            ctx.shared[SHARED_PET_FOOD] = BarReading(present=False)
            return PipelineResult.idle("рамки пета нема")
        filled, total = read_bar(ctx.frame.image, frame, frame.food_row, gold=True)
        reading = BarReading(present=True, filled=filled, total=total,
                             x0=frame.x0, row=frame.food_row)
        ctx.shared[SHARED_PET_FOOD] = reading

        if not reading.present:
            return PipelineResult.idle("ситість не видно")
        status = f"корм {reading.percent}% ({reading.filled}/{reading.total}px)"
        # ріст міряємо від ПОЧАТКУ серії: одна порція додає менше пікселя,
        # тому порівняння сусідніх годувань хибно вважало б корм марним
        if self.level_at_start >= 0 and reading.filled > self.level_at_start:
            self.tries = 0
            self.level_at_start = reading.filled
            self.gave_up = False

        if reading.ratio >= cfg.feed_below:
            # мета досягнута — серія спроб закінчилась вдало. Без цього бот на порозі
            # (ситість коливається 49-50%) рахував вдалі годування як марні й «здавався»
            self.tries, self.level_at_start, self.gave_up = 0, -1, False
            return PipelineResult.idle(status)
        if self.gave_up:
            return PipelineResult.idle(f"{status} · корм не діє")
        if ctx.now - self.last_feed < cfg.cooldown:
            return PipelineResult.idle(f"{status} · кулдаун")

        self.last_feed = ctx.now
        if self.level_at_start < 0:
            self.level_at_start = reading.filled
        self.tries += 1
        events = [f"ситість {reading.percent}% < {cfg.feed_below:.0%} -> {cfg.feed_key}"]
        if cfg.give_up_after and self.tries >= cfg.give_up_after:
            # інакше бот усю ніч тиснув би клавішу, на яку гра не реагує
            self.gave_up = True
            events.append(f"!! {cfg.feed_key} не піднімає ситість за {cfg.give_up_after} спроб "
                          f"— перевір, що на цій клавіші корм")
        return PipelineResult(actions=[PressKey(cfg.feed_key, reason="корм")],
                              status=status, events=events)
