"""
Втримання висоти в польоті: пробіл — вгору, Z — вниз.

Висоту дає блок «Висота» (третє число біля координат). Тут лише рішення: якщо
піднялись вище за потрібне — тиснемо вниз, якщо просіли — вгору, і не частіше ніж
раз на `interval`, щоб не смикати персонажа.

Бот стежить, чи його натискання взагалі щось міняють: на землі висота задана
рельєфом і від клавіш не залежить, тому після кількох марних спроб блок замовкає
і пише про це в лог, а не молотить пробіл усю ніч.
"""
from __future__ import annotations

from pydantic import Field

from app.pipelines.actions import PipelineResult, PressKey
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext
from app.pipelines.registry import register
from app.pipelines.shared import SHARED_ALT, busy_reasons, read_altitude

BUSY_SKIP = frozenset({"repair", "pet_summon", "return_home"})


class AltitudeHoldConfig(PipelineConfig):
    target: int = Field(default=0, ge=0, title="Яку висоту тримати",
                        description="0 = запам'ятати ту, на якій бот почав")
    tolerance: int = Field(default=2, ge=0, title="Допуск, одиниць",
                           description="менші відхилення не чіпаємо")
    up_key: str = Field(default="space", title="Клавіша вгору")
    down_key: str = Field(default="z", title="Клавіша вниз")
    tap: float = Field(default=0.3, gt=0, title="Тримати клавішу, с")
    interval: float = Field(default=1.0, ge=0, title="Пауза між поправками, с")
    give_up_after: int = Field(default=6, ge=0, title="Спроб без зміни",
                               description="0 = не здаватись. Інакше бот замовкає: на землі "
                                           "висота від клавіш не залежить")
    retry_after: float = Field(default=60.0, ge=0, title="Після паузи пробувати знову, с")


@register
class AltitudeHoldPipeline(Pipeline):
    type_name = "altitude_hold"
    label = "Втримання висоти"
    category = "nav"
    config_model = AltitudeHoldConfig
    requires = frozenset({SHARED_ALT})

    def __init__(self, config: AltitudeHoldConfig, window: str = "") -> None:
        super().__init__(config, window)
        self.reset()

    def reset(self) -> None:
        self.target: int | None = None
        self.last_press = float("-inf")
        self.last_z: int | None = None      # висота на момент попереднього натискання
        self.tries = 0                      # натискань поспіль без жодної зміни
        self.gave_up_at: float | None = None

    def process(self, ctx: PipelineContext) -> PipelineResult:
        cfg: AltitudeHoldConfig = self.config
        alt = read_altitude(ctx.shared)
        if alt is None or not alt.known:
            return PipelineResult.idle("висоти не видно")

        if self.target is None:
            self.target = cfg.target or alt.z
        diff = alt.z - self.target
        status = f"висота {alt.z}, тримаю {self.target}"
        if abs(diff) <= cfg.tolerance:
            self.tries, self.last_z = 0, None
            return PipelineResult.idle(f"висота {alt.z}")

        if self.gave_up_at is not None:
            if ctx.now - self.gave_up_at < cfg.retry_after:
                return PipelineResult.idle(f"{status} · клавіші не діють, чекаю")
            self.gave_up_at, self.tries = None, 0

        busy = busy_reasons(ctx.shared) & BUSY_SKIP
        if busy:
            return PipelineResult.idle(f"{status} · чекаю: {', '.join(sorted(busy))}")
        if ctx.now - self.last_press < cfg.interval:
            return PipelineResult.idle(status)

        # чи подіяло попереднє натискання
        if self.last_z is not None and alt.z == self.last_z:
            self.tries += 1
        else:
            self.tries = 0
        self.last_z = alt.z
        self.last_press = ctx.now

        events = []
        if cfg.give_up_after and self.tries >= cfg.give_up_after:
            self.gave_up_at = ctx.now
            events.append(f"!! висота не міняється за {cfg.give_up_after} натискань — "
                          f"персонаж не в польоті або гра не приймає клавішу")
            return PipelineResult(status=status, events=events)

        key = cfg.down_key if diff > 0 else cfg.up_key
        where = "вниз" if diff > 0 else "вгору"
        return PipelineResult(actions=[PressKey(key, hold=cfg.tap, reason=f"висота {where}")],
                              status=f"{status} · {where}")
