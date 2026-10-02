"""
Координати персонажа.

Гра сама пише їх угорі праворуч («Поселок у моста / 241, 563  22»), тому нічого
вигадувати не треба: читаємо число з екрана і кладемо на спільну дошку. Звідти їх
бере повернення на місце — без координат воно не знає ані де воно, ані куди йти.

Читаємо не щокадру: OCR коштує близько 80 мс, а персонаж за секунду пробігає
кілька одиниць — цього досить.

Тут же рахується, наскільки персонаж відійшов від місця, де його лишили: сам блок
нікуди не біжить (рух гра через фонові повідомлення не приймає), але видно і в
статусі, і в лозі, що бот заїхав не туди.
"""
from __future__ import annotations

from pydantic import Field

from app.core.background import Probe
from app.pipelines.actions import PipelineResult
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext
from app.pipelines.registry import register
from app.pipelines.shared import SHARED_HOME, SHARED_POS, Position, busy_reasons
from app.vision.digits import DigitsConfig, read_numbers
from app.vision.text import OcrConfig


class PositionConfig(PipelineConfig):
    read_every: float = Field(default=1.0, ge=0, title="Перечитувати раз на, с",
                              description="швидкий режим — коли точність потрібна (їдемо додому, "
                                          "після смерті, місце ще невідоме, відійшли від дому)")
    calm_every: float = Field(default=15.0, ge=0, title="У спокої перечитувати раз на, с",
                              description="0 = завжди раз на «Перечитувати». Спокій — місце відоме, "
                                          "персонаж біля дому, ніхто не їде. Кожне читання — це "
                                          "запуск tesseract, а при кількох вікнах їх десятки на "
                                          "секунду; стоячому на фармі персонажу 15 с цілком досить")
    calm_radius: float = Field(default=6.0, gt=0, title="Спокій, якщо ближче до дому, ніж",
                               json_schema_extra={"tech": True},
                               description="далі від дому читаємо швидко, щоб повернення почалось вчасно")
    forget_after: int = Field(default=5, ge=1, title="Забути після N невдач",
                              json_schema_extra={"tech": True},
                              description="панель могли закрити або перекрити вікном")
    max_jump: int = Field(default=30, ge=0, title="Найбільший стрибок за читання",
                          json_schema_extra={"tech": True},
                          description="0 = приймати будь-який; інакше різкий стрибок "
                                      "вважається помилкою читання")
    min_value: int = Field(default=10, ge=0, title="Найменша можлива координата",
                           json_schema_extra={"tech": True},
                           description="«456, 2» замість «456, 673» — у числі загубились цифри: "
                                       "координати на цій карті тризначні")
    max_value: int = Field(default=1500, gt=0, title="Найбільша можлива координата",
                           json_schema_extra={"tech": True},
                           description="карта менша: 6693 замість 669 — це зайва цифра від сусіднього "
                                       "числа, а не подорож на край світу")
    teleport_reads: int = Field(default=5, ge=2, title="Телепорт, якщо стрибок повторився N разів",
                                json_schema_extra={"tech": True},
                                description="воскресіння в місті — це справжній стрибок, і він лишається "
                                            "назавжди. Помилки читання («453, 70» замість 674) "
                                            "тримались до трьох читань, тому віримо лише довшій серії")
    home_x: int = Field(default=0, ge=0, title="Дім: X",
                        description="0 = взяти перше прочитане місце за домашнє")
    home_y: int = Field(default=0, ge=0, title="Дім: Y")
    warn_away: int = Field(default=40, ge=0, title="Попередити, якщо відійшов на",
                           description="0 = не стежити. Пишеться в лог один раз, поки не повернувся")
    digits: DigitsConfig = Field(default_factory=DigitsConfig, title="Де шукати число",
                                 json_schema_extra={"tech": True})
    ocr: OcrConfig = Field(default_factory=OcrConfig, title="Читання тексту",
                           json_schema_extra={"tech": True})


def _is_subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


def _digit_slip(new: int, old: int) -> bool:
    """Нове число — це старе з однією цифрою менше (або більше): типова помилка OCR."""
    a, b = str(new), str(old)
    if a == b:
        return False
    if len(a) == len(b) - 1:
        return _is_subsequence(a, b)
    if len(a) == len(b) + 1:
        return _is_subsequence(b, a)
    return False


@register
class PositionPipeline(Pipeline):
    type_name = "position"
    label = "Координати"
    category = "nav"
    config_model = PositionConfig
    provides = frozenset({SHARED_POS, SHARED_HOME})

    def __init__(self, config: PositionConfig, window: str = "") -> None:
        super().__init__(config, window)
        self.reset()

    def reset(self) -> None:
        cfg: PositionConfig = self.config
        self.probe = Probe()
        self.pos = Position()
        self.last_read = float("-inf")
        self.misses = 0
        self.candidate: Position | None = None   # перше читання, ще не підтверджене
        self.jump: Position | None = None        # далеке читання, яке може бути телепортом
        self.jump_seen = 0
        self.home = (Position(x=cfg.home_x, y=cfg.home_y, known=True)
                     if cfg.home_x and cfg.home_y else None)
        self.away = False              # щоб не писати в лог те саме щосекунди
        self.probe = Probe()           # OCR цифр іде у фоні

    def process(self, ctx: PipelineContext) -> PipelineResult:
        cfg: PositionConfig = self.config
        due = ctx.now - self.last_read >= self._interval(ctx)
        if due:
            self.last_read = ctx.now
        ready, numbers = self.probe.step(read_numbers, ctx.frame.image, cfg.digits, cfg.ocr, due=due)
        if ready:
            self._apply(numbers, ctx)
        ctx.shared[SHARED_POS] = self.pos
        ctx.shared[SHARED_HOME] = self.home
        return self._report()

    def _interval(self, ctx: PipelineContext) -> float:
        """
        Як часто читати зараз. Повільно (calm_every) лише коли все спокійно; щойно щось
        потребує свіжих координат — швидко: місце ще невідоме або читання хибить, їде
        повернення чи воскресіння (вони звіряють координати за секунди й мають busy),
        персонаж відійшов від дому.
        """
        cfg: PositionConfig = self.config
        if (not cfg.calm_every or not cfg.read_every          # read_every=0 — явне «щокадру»
                or not self.pos.known or self.home is None or self.misses
                or self.jump is not None):
            return cfg.read_every
        if busy_reasons(ctx.shared) & {"return_home", "death_return"}:
            return cfg.read_every
        if self.pos.distance_to(self.home) > cfg.calm_radius:
            return cfg.read_every
        return max(cfg.read_every, cfg.calm_every)

    def _report(self) -> PipelineResult:
        cfg: PositionConfig = self.config
        if not (self.pos.known and self.home):
            return PipelineResult.idle(str(self.pos))
        away = self.pos.distance_to(self.home)
        status = f"{self.pos} · від місця {away:.0f}"
        if not cfg.warn_away:
            return PipelineResult.idle(status)
        if away >= cfg.warn_away and not self.away:
            self.away = True
            return PipelineResult(status=status,
                                  events=[f"!! відійшов від місця на {away:.0f} "
                                          f"({self.home} -> {self.pos})"])
        if away < cfg.warn_away and self.away:
            self.away = False
            return PipelineResult(status=status, events=["повернувся на місце"])
        return PipelineResult.idle(status)

    def _apply(self, numbers: list[int], ctx: PipelineContext) -> None:
        cfg: PositionConfig = self.config
        if len(numbers) < 2:
            self.misses += 1
            if self.misses >= cfg.forget_after:
                self.pos = Position(at=ctx.now)     # краще «не знаю», ніж стара точка
            return
        x, y = numbers[0], numbers[1]
        if max(x, y) > cfg.max_value or min(x, y) < cfg.min_value:   # цифри злиплись або зникли
            self.misses += 1
            return
        if self.pos.known and (_digit_slip(x, self.pos.x) or _digit_slip(y, self.pos.y)):
            # OCR загубив або додав цифру («581» -> «58», «669» -> «6693»): персонаж так не
            # телепортується, це збій читання. Такий стрибок не рахуємо навіть як кандидата
            # в телепорт — інакше стабільна хибна цифра за кілька читань «ставала» правдою
            # і запускала політ додому з ніякого місця.
            self.misses += 1
            return
        if cfg.max_jump and not self.pos.known:
            # першому читанню не віримо на слово: одне хибне число — і бот «далеко від
            # дому» або, гірше, запам'ятав би неправильний дім. Потрібні два схожі підряд.
            seen = Position(x=x, y=y, known=True, at=ctx.now)
            confirmed = self.candidate is not None and seen.distance_to(self.candidate) <= cfg.max_jump
            self.candidate = seen
            if not confirmed:
                return
        if (cfg.max_jump and self.pos.known
                and Position(x=x, y=y).distance_to(self.pos) > cfg.max_jump):
            # схоже на помилку читання — або на телепорт (смерть, воскресіння в місті).
            # Раніше тут стрибок відкидався назавжди: після смерті координати застигли
            # на місці фарму, і повернення «долетіло» за секунду, не злетівши з міста.
            seen = Position(x=x, y=y, known=True, at=ctx.now)
            if self.jump is not None and seen.distance_to(self.jump) <= cfg.max_jump:
                self.jump_seen += 1
            else:
                self.jump_seen = 1
            self.jump = seen
            if self.jump_seen < cfg.teleport_reads:
                self.misses += 1
                return
        self.jump, self.jump_seen = None, 0
        self.misses = 0
        self.pos = Position(x=x, y=y, known=True, at=ctx.now)
        if self.home is None:
            self.home = self.pos      # перше відоме місце і вважаємо домом
