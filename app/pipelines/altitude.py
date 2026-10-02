"""
Висота персонажа — третє число в тій самій панелі, але зелене.

Окремий блок, бо потрібна не для того, для чого координати: по висоті видно, що
персонаж злетів, впав з обриву або пливе. Повернення на місце дивиться і на неї:
бігти по землі до точки, яка на 30 одиниць вище, марно.

Колір розділяє числа надійніше за координати пікселів: координати білі, висота зелена.
"""
from __future__ import annotations

from pydantic import Field

from app.pipelines.actions import PipelineResult
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext
from app.pipelines.registry import register
from app.pipelines.shared import SHARED_ALT, Altitude, busy_reasons, is_mounted
from app.vision.digits import DigitsConfig, read_numbers
from app.vision.text import OcrConfig


class AltitudeConfig(PipelineConfig):
    read_every: float = Field(default=1.0, ge=0, title="Перечитувати раз на, с",
                              description="швидкий режим: літаємо/їдемо додому/висота невідома")
    calm_every: float = Field(default=15.0, ge=0, title="У спокої перечитувати раз на, с",
                              description="0 = завжди раз на «Перечитувати». Спокій — персонаж "
                                          "на землі й ніхто не їде; висота на місці фарму майже "
                                          "не міняється, а кожне читання — запуск tesseract")
    forget_after: int = Field(default=5, ge=1, title="Забути після N невдач",
                              json_schema_extra={"tech": True})
    max_jump: int = Field(default=10, ge=0, title="Найбільша зміна за читання",
                          json_schema_extra={"tech": True},
                          description="0 = приймати будь-яке число. Інакше різкий стрибок треба "
                                      "підтвердити ще одним читанням: у «22» інколи губиться "
                                      "цифра й виходить «6», а падіння з висоти дає плавний ряд, "
                                      "тому двом однаковим читанням поспіль віримо")
    confirm_reads: int = Field(default=2, ge=1, title="Читань для підтвердження стрибка",
                               json_schema_extra={"tech": True})
    digits: DigitsConfig = Field(
        default_factory=lambda: DigitsConfig(color="green"), title="Де шукати число",
        json_schema_extra={"tech": True}, description="зелене число в панелі локації")
    ocr: OcrConfig = Field(default_factory=OcrConfig, title="Читання тексту",
                           json_schema_extra={"tech": True})


@register
class AltitudePipeline(Pipeline):
    type_name = "altitude"
    label = "Висота"
    category = "nav"
    config_model = AltitudeConfig
    provides = frozenset({SHARED_ALT})

    def __init__(self, config: AltitudeConfig, window: str = "") -> None:
        super().__init__(config, window)
        self.reset()

    def reset(self) -> None:
        self.alt = Altitude()
        self.last_read = float("-inf")
        self.misses = 0
        self.jump: int | None = None       # підозріле число, яке чекає підтвердження
        self.jump_seen = 0

    def process(self, ctx: PipelineContext) -> PipelineResult:
        cfg: AltitudeConfig = self.config
        if ctx.now - self.last_read >= self._interval(ctx):
            self.last_read = ctx.now
            numbers = read_numbers(ctx.frame.image, cfg.digits, cfg.ocr)
            if numbers and self._believable(numbers[-1], cfg):
                self.misses = 0
                self.alt = Altitude(z=numbers[-1], known=True, at=ctx.now)
            else:
                self.misses += 1
                if self.misses >= cfg.forget_after:
                    self.alt = Altitude(at=ctx.now)
        ctx.shared[SHARED_ALT] = self.alt
        return PipelineResult.idle(str(self.alt))

    def _interval(self, ctx: PipelineContext) -> float:
        """Швидко, поки висота важлива: невідома, читання хибить, їдемо/летимо, сидимо верхи."""
        cfg: AltitudeConfig = self.config
        if (not cfg.calm_every or not cfg.read_every          # read_every=0 — явне «щокадру»
                or not self.alt.known or self.misses or self.jump is not None
                or is_mounted(ctx.shared)
                or busy_reasons(ctx.shared) & {"return_home", "death_return"}):
            return cfg.read_every
        return max(cfg.read_every, cfg.calm_every)

    def _believable(self, z: int, cfg: AltitudeConfig) -> bool:
        """
        Зіпсуте читання висоти коштувало дорого: «6» замість «22» осідало в пам'яті
        як висота землі, і бот цілу ніч «сідав» на рівному місці — тобто раз у раз
        сідав на літаючого звіра. Тому різку зміну приймаємо лише після повтору.
        """
        if not cfg.max_jump or not self.alt.known or abs(z - self.alt.z) <= cfg.max_jump:
            self.jump, self.jump_seen = None, 0
            return True
        same = self.jump is not None and abs(z - self.jump) <= cfg.max_jump
        self.jump_seen = self.jump_seen + 1 if same else 1
        self.jump = z
        if self.jump_seen < cfg.confirm_reads:
            return False
        self.jump, self.jump_seen = None, 0
        return True
