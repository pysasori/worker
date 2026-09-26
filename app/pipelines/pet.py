"""Пайплайн піта: HP нижче порогу -> клавіша лікування."""
from __future__ import annotations

from pydantic import Field

from app.core.geometry import Region
from app.pipelines.actions import PipelineResult, PressKey
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext
from app.pipelines.registry import register
from app.pipelines.shared import SHARED_PET, SHARED_PET_FRAME
from app.vision.pet_frame import PetFrameConfig, find_pet_frame, read_bar
from app.vision.schemas import BarReading


class PetHealConfig(PipelineConfig):
    search: Region = Field(default=Region.of(0, 120, 150, 320), title="Де шукати рамку пета",
                           json_schema_extra={"tech": True},
                           description="вузька смуга зліва: рамку можна тягати вгору-вниз, а праворуч "
                                       "починаються смужки івенту, схожі на рамку пета")
    frame: PetFrameConfig = Field(default_factory=PetFrameConfig, title="Пошук рамки пета",
                                  json_schema_extra={"tech": True})
    forget_after: int = Field(default=2, ge=1, title="Забути рамку після N невдач",
                              json_schema_extra={"tech": True},
                              description="щоб бот не тримався за старі координати")
    relocate_every: float = Field(default=2.0, ge=0, title="Перешукувати рамку раз на, с",
                                  json_schema_extra={"tech": True},
                                  description="рамку можна пересунути мишею, тому іноді шукаємо наново")

    heal_key: str = Field(default="f3", title="Клавіша лікування")
    heal_below: float = Field(default=0.5, gt=0, le=1, title="Лікувати нижче HP",
                              description="0.5 = половина смужки")
    cooldown: float = Field(default=3.0, ge=0, title="Пауза між лікуваннями, с")


@register
class PetHealPipeline(Pipeline):
    type_name = "pet_heal"
    label = "Лікування пета"
    category = "pet"
    config_model = PetHealConfig
    provides = frozenset({SHARED_PET, SHARED_PET_FRAME})

    def __init__(self, config: PetHealConfig, window: str = "") -> None:
        super().__init__(config, window)
        self.reset()

    def reset(self) -> None:
        self.last_heal = float("-inf")  # перший лік — одразу, без очікування кулдауна
        self.frame = None               # знайдена геометрія рамки пета
        self.located_at = float("-inf")
        self.hp_total = 0               # найдовша бачена смужка = 100% HP
        self.misses = 0                 # скільки разів поспіль рамку не знайшли

    def process(self, ctx: PipelineContext) -> PipelineResult:
        cfg: PetHealConfig = self.config
        image = ctx.frame.image

        # рамку пета шукаємо за виглядом: її можна пересунути куди завгодно
        if self.frame is None or ctx.now - self.located_at >= cfg.relocate_every:
            found = find_pet_frame(image, cfg.frame, cfg.search.box)
            self.located_at = ctx.now
            if found is not None:
                self.frame, self.misses = found, 0
            else:
                # одне мигання рамки переживаємо, але не більше: інакше бот тримав би
                # застарілі координати й «лікував» неіснуючого пета
                self.misses += 1
                if self.misses >= cfg.forget_after:
                    self.frame, self.hp_total = None, 0
        if self.frame is not None and read_bar(image, self.frame, self.frame.hp_row)[0] == 0:
            self.frame = find_pet_frame(image, cfg.frame, cfg.search.box)  # зникла або переїхала

        ctx.shared[SHARED_PET_FRAME] = self.frame
        if self.frame is None:
            reading = BarReading(present=False)
        else:
            filled, span = read_bar(image, self.frame, self.frame.hp_row)
            if self.frame.exact:
                total = max(span, 1)                 # край рамки знайдено — ширина точна
            else:
                # край не знайшли: розмах трохи довший за смужку, тож за 100% беремо
                # найдовшу бачену смужку, а поки її не бачили — оцінку від розмаху
                self.hp_total = max(self.hp_total, filled)
                total = max(self.hp_total, round(span * 0.9), 1)
            reading = BarReading(present=True, filled=min(filled, total), total=total,
                                 x0=self.frame.x0, row=self.frame.hp_row)
        ctx.shared[SHARED_PET] = reading

        if not reading.present:
            return PipelineResult.idle("піта нема")
        status = f"піт {reading.percent}%"
        # ratio == 0 при живій рамці means піт при смерті або смужка порожня — лікувати нічим
        if 0 < reading.ratio < cfg.heal_below and ctx.now - self.last_heal >= cfg.cooldown:
            self.last_heal = ctx.now
            return PipelineResult(
                actions=[PressKey(cfg.heal_key, reason="лік піта")],
                status=status,
                events=[f"HP піта {reading.percent}% < {cfg.heal_below:.0%} -> {cfg.heal_key}"],
            )
        return PipelineResult.idle(status)
