"""
Збір лута: після смерті цілі тисне клавішу підбору до N разів із заданим інтервалом,
перевіряючи між натисканнями, чи ще щось лежить у центральному квадраті.

Натискання розкидані по кадрах, а не виконуються пачкою: завдяки цьому перед кожним
наступним натисканням є свіжий кадр, і як тільки земля порожня — збір припиняється,
не витрачаючи решту натискань.

Поки збір триває, лут виставляє прапорець "busy": пошук цілі його бачить і не тисне
Tab, щоб нова ціль не перебила підбір. Зв'язок навмисно м'який (без requires), інакше
вийшло б кільце: лут потребує ціль, а пошук потребував би лут.
"""
from __future__ import annotations

from pydantic import Field

from app.pipelines.actions import PipelineResult, PressKey
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext
from app.pipelines.registry import register
from app.pipelines.shared import SHARED_TARGET, read_target, set_busy
from app.vision.ground import GroundCheckConfig, center_square, scan_ground

_EPS = 1e-6


class LootConfig(PipelineConfig):
    key: str = Field(default="f2", title="Клавіша підбору")
    max_presses: int = Field(default=5, ge=1, title="Натискань максимум")
    interval: float = Field(default=0.4, ge=0, title="Інтервал, с")
    corpse_delay: float = Field(default=0.3, ge=0, title="Пауза після смерті, с",
                                description="поки випадає лут")
    check: GroundCheckConfig = Field(default_factory=GroundCheckConfig, title="Перевірка землі",
                                     description="квадрат у центрі екрана")


@register
class LootPipeline(Pipeline):
    type_name = "loot"
    label = "Збір лута"
    category = "loot"
    config_model = LootConfig
    requires = frozenset({SHARED_TARGET})

    def __init__(self, config: LootConfig, window: str = "") -> None:
        super().__init__(config, window)
        self.reset()

    def reset(self) -> None:
        self.pending = 0
        self.ready_at = 0.0
        self.last_press = float("-inf")
        self.last_ground = None

    def process(self, ctx: PipelineContext) -> PipelineResult:
        cfg: LootConfig = self.config
        target = read_target(ctx.shared)
        if target is None:
            return PipelineResult.idle("нема даних про ціль")

        res = PipelineResult()
        if target.died_now:  # ціль щойно померла — заряджаємо серію натискань
            self.pending = cfg.max_presses
            self.ready_at = ctx.now + cfg.corpse_delay
            self.last_press = float("-inf")

        if self.pending <= 0:
            set_busy(ctx.shared, "loot", False)
            return res
        set_busy(ctx.shared, "loot", True)

        # EPS: без нього натискання інколи з'їжджає на цілий кадр через похибку float
        if ctx.now < self.ready_at - _EPS or ctx.now - self.last_press < cfg.interval - _EPS:
            return PipelineResult(status=f"збираю лут ({cfg.max_presses - self.pending + 1}/{cfg.max_presses})")

        if cfg.check.enabled:
            region = center_square(ctx.frame.size, cfg.check)
            ground = scan_ground(ctx.frame.crop(region), region, cfg.check)
            self.last_ground = ground
            ctx.shared["ground"] = ground
            if not ground.has_loot:
                done = cfg.max_presses - self.pending
                self.pending = 0
                set_busy(ctx.shared, "loot", False)
                if done:
                    res.events.append(f"на землі порожньо, зупинився після {done} натискань")
                return PipelineResult(events=res.events, status="землі порожньо")

        self.pending -= 1
        self.last_press = ctx.now
        pressed = cfg.max_presses - self.pending
        if pressed == 1:
            res.events.append(f"збір лута: {cfg.key} до {cfg.max_presses} разів")
        res.actions.append(PressKey(cfg.key, reason="лут"))
        res.status = f"збираю лут ({pressed}/{cfg.max_presses})"
        return res
