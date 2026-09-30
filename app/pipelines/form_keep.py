"""Підтримка бойової форми: без підтвердженого значка бій не починаємо."""
from __future__ import annotations

from pydantic import Field

from app.core.geometry import Region
from app.pipelines.actions import PipelineResult, PressKey
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext
from app.pipelines.registry import register
from app.pipelines.shared import busy_reasons, combat_ready, set_busy
from app.vision.template import TemplateSpec, find_template


BUSY = "form_keep"


class FormKeepConfig(PipelineConfig):
    key: str = Field(default="1", title="Клавіша форми")
    retry_after: float = Field(default=5.0, ge=0.5, title="Повторити через, с",
                               description="пауза після натискання, якщо значок не з'явився")
    confirm_missing: int = Field(default=2, ge=1, title="Підтвердити відсутність, кадрів",
                                 json_schema_extra={"tech": True})
    icon: TemplateSpec = Field(
        default_factory=lambda: TemplateSpec(
            name="animal_form.pgm", threshold=0.70, area=Region.of(70, 55, 210, 90)),
        title="Значок форми", json_schema_extra={"tech": True})


@register
class FormKeepPipeline(Pipeline):
    type_name = "form_keep"
    label = "Підтримка форми"
    category = "combat"
    run_order = 50
    config_model = FormKeepConfig

    def __init__(self, config: FormKeepConfig, window: str = "") -> None:
        super().__init__(config, window)
        self.reset()

    def reset(self) -> None:
        self.missing = 0
        self.last_press = float("-inf")

    def process(self, ctx: PipelineContext) -> PipelineResult:
        cfg: FormKeepConfig = self.config
        present = find_template(ctx.frame.image, cfg.icon) is not None
        if present:
            self.missing = 0
            set_busy(ctx.shared, BUSY, False)
            return PipelineResult.idle("форма є")

        self.missing += 1

        # Навігація й сервіс важливіші. Не ставимо свій busy, щоб не створити
        # взаємне очікування з поверненням, ремонтом, продажем чи воскресінням.
        others = busy_reasons(ctx.shared) - {BUSY}
        if not combat_ready(ctx.shared) or others:
            set_busy(ctx.shared, BUSY, False)
            why = ", ".join(sorted(others)) if others else "перевірку місця"
            return PipelineResult.idle(f"форми нема · чекаю: {why}")

        # Після підтвердження блокуємо пошук/атаку, доки значок не з'явиться.
        if self.missing < cfg.confirm_missing:
            return PipelineResult.idle("перевіряю форму")
        set_busy(ctx.shared, BUSY, True)
        if ctx.now - self.last_press < cfg.retry_after:
            return PipelineResult.idle("вмикаю форму")

        self.last_press = ctx.now
        return PipelineResult(
            actions=[PressKey(cfg.key, reason="увімкнути форму")],
            status=f"форми нема → {cfg.key}",
            events=[f"форми нема — тисну {cfg.key}"],
        )
