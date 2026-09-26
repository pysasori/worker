"""
Приклик пета: якщо рамки пета нема — тиснемо клавішу приклику і чекаємо касту.

Це основа всієї групи «Пет»: без пета нема ні його HP, ні ситості, тому лікування
й годування просто мовчать. Поки триває каст, виставляється busy — інакше атака
своїм ударом перерве приклик («Использование умения было прервано»).

Стан пета беремо готовий, від блока лікування: кадр читається один раз на всіх.

Кличемо лише поза боєм: коли пет гине посеред бійки, моб б'є персонажа і каст
переривається («Использование умения было прервано»). Тому спершу добиваємо ціль,
чекаємо, поки лут доберуть, дивимось, що HP персонажа не падає (значить, нас ніхто
не б'є) і що картинка не їде (значить, персонаж стоїть, а не дочовгує до предметів —
рух рве каст так само надійно, як удар), і тільки тоді тиснемо приклик.

Здаємось не назавжди: невдачі майже завжди тимчасові (бій, каст), тож після
паузи пробуємо знову, інакше бот до ранку фармив би без пета.
"""
from __future__ import annotations

from pydantic import Field

from app.core.geometry import Region

from app.pipelines.actions import PipelineResult, PressKey
from app.pipelines.base import Pipeline, PipelineConfig, PipelineContext
from app.pipelines.registry import register
from app.pipelines.shared import (
    SHARED_PET, busy_reasons, is_mounted, read_player, read_target, set_busy, set_mounted,
)
from PIL import Image, ImageChops, ImageStat

from app.vision.colors import is_red


class PetSummonConfig(PipelineConfig):
    summon_key: str = Field(default="f7", title="Клавіша приклику")
    cast_time: float = Field(default=5.0, ge=0, title="Час касту, с",
                             description="поки триває — бот не б'є, щоб не перервати")
    cooldown: float = Field(default=8.0, ge=0, title="Пауза між спробами, с")
    confirm_frames: int = Field(default=4, ge=1, title="Кадрів без пета",
                                description="щоб не смикатись, коли рамка блимнула")
    missing_for: float = Field(default=5.0, ge=0, title="Секунд без пета",
                               description="клавіша приклику в грі — ПЕРЕМИКАЧ: якщо пет живий, "
                                           "вона його відкликає. Тому тиснемо лише коли рамки "
                                           "нема довго, а не пів секунди")
    give_up_after: int = Field(default=3, ge=0, title="Спроб без результату",
                               description="0 = не здаватись. Інакше бот робить паузу і йде "
                                           "фармити далі, щоб не стояти в касті всю ніч")
    retry_after: float = Field(default=60.0, ge=0, title="Після паузи пробувати знову, с",
                               description="невдачі бувають через бій — за хвилину вже можна")
    only_out_of_combat: bool = Field(default=True, title="Кликати лише поза боєм",
                                     description="у бою моб перериває каст приклику")
    settle: float = Field(default=3.0, ge=0, title="Спокою перед приликом, с",
                          description="після луту персонаж ще добігає до предметів, "
                                      "і рух перериває каст; сюди ж — секунди без ударів по нас")
    hp_region: Region = Field(default=Region.of(105, 28, 145, 12), title="Смужка HP персонажа",
                              json_schema_extra={"tech": True},
                              description="по ній видно, що нас б'ють: поки HP падає, каст "
                                          "приклику однаково переб'ють")
    still_area: Region = Field(default=Region.of(300, 300, 850, 500), title="Де дивитись рух",
                               json_schema_extra={"tech": True},
                               description="шматок світу: коли персонаж іде, камера їде за ним "
                                           "і картинка сильно змінюється")
    hold_for: float = Field(default=8.0, ge=0, title="Тримати паузу заради приклику, с",
                            description="між убитими мобами тиші не буває, тому бот сам "
                                        "не бере нову ціль і чекає, поки стане тихо")
    dismount_key: str = Field(default="9", title="Клавіша польоту (злізти зі звіра)",
                              description="порожньо = не злазити. Верхи гра не дає прикликати пета "
                                          "(«Здесь невозможно призвать питомца»), а на екрані нема "
                                          "жодної позначки, що персонаж верхи — тому мовчазні "
                                          "невдачі приклику це єдина ознака, яку видно")
    move_level: float = Field(default=15.0, gt=0, title="Поріг руху", json_schema_extra={"tech": True},
                              description="зміна картинки за секунду: стоїмо — до 8, ідемо — від 30. "
                                          "Рахується на секунду, щоб не залежати від частоти кадрів")


@register
class PetSummonPipeline(Pipeline):
    type_name = "pet_summon"
    label = "Приклик пета"
    category = "pet"
    config_model = PetSummonConfig
    requires = frozenset({SHARED_PET})

    def __init__(self, config: PetSummonConfig, window: str = "") -> None:
        super().__init__(config, window)
        self.reset()

    def reset(self) -> None:
        self.missing_since: float | None = None    # відколи не видно рамки пета
        self.missing_streak = 0
        self.casting_until = 0.0
        self.last_try = float("-inf")     # перший приклик — одразу
        self.tries = 0
        self.gave_up = False
        self.gave_up_at = 0.0
        self.calm_since: float | None = None
        self.hp = -1                          # червоних пікселів у смужці персонажа
        self.hit_at = float("-inf")           # коли востаннє по нас влучили
        self.scene: Image.Image | None = None  # зменшений шматок світу з минулого кадру
        self.scene_at = 0.0
        self.move_rate = 0.0                  # наскільки швидко міняється картинка
        self.moved_at = float("-inf")         # коли востаннє картинка їхала
        self.hold_since: float | None = None  # відколи тримаємо паузу заради приклику

    def _hit_recently(self, ctx: PipelineContext) -> bool:
        """HP персонажа впало щойно — отже, нас б'ють і каст переб'ють."""
        cfg: PetSummonConfig = self.config
        known = read_player(ctx.shared)          # блок відхілу вже прочитав смужку
        if known is not None and known.present:
            hp = known.filled
        else:
            crop = ctx.frame.image.crop(cfg.hp_region.box)
            px = crop.load()
            hp = max(sum(1 for x in range(crop.width) if is_red(px[x, y])) for y in range(crop.height))
        if self.hp >= 0 and hp < self.hp - 1:
            self.hit_at = ctx.now
        self.hp = hp
        return ctx.now - self.hit_at < cfg.settle

    def _moving(self, ctx: PipelineContext) -> bool:
        """Картинка їде — персонаж іде (наприклад, дочовгує до лута), каст переб'ється."""
        cfg: PetSummonConfig = self.config
        scene = ctx.frame.image.crop(cfg.still_area.box).resize((85, 50))
        was, was_at = self.scene, self.scene_at
        self.scene, self.scene_at = scene, ctx.now
        if was is not None:
            dt = max(ctx.now - was_at, 0.05)
            self.move_rate = ImageStat.Stat(ImageChops.difference(was, scene)).mean[0] / dt
            if self.move_rate > cfg.move_level:
                self.moved_at = ctx.now
        return ctx.now - self.moved_at < cfg.settle

    def process(self, ctx: PipelineContext) -> PipelineResult:
        cfg: PetSummonConfig = self.config
        pet = ctx.shared.get(SHARED_PET)
        if pet is None:
            return PipelineResult.idle("нема даних про пета")

        if ctx.now < self.casting_until:
            set_busy(ctx.shared, "pet_summon", True)
            if pet.present:                                   # з'явився раніше за таймер
                return self._done(ctx, "піт на місці")
            left = self.casting_until - ctx.now
            return PipelineResult.idle(f"приклик пета ({left:.0f}с)")

        if self.casting_until:                                # каст щойно закінчився
            if pet.present:
                self.tries = 0
                self.gave_up = False
                return self._done(ctx, "піт на місці")
            res = self._done(ctx, "пета так і нема")
            # здаємось лише тут: рішення на момент НАТИСКАННЯ писало в лог «здався»
            # навіть тоді, коли той самий приклик за мить спрацьовував
            if cfg.give_up_after and self.tries >= cfg.give_up_after:
                self.gave_up = True
                self.gave_up_at = ctx.now
                if cfg.dismount_key and is_mounted(ctx.shared) is not False:
                    # верхи пет не кличеться ніколи. Стан «верхи» бот веде сам, бо в грі
                    # його не видно, і після перезапуску він невідомий — тоді ця спроба
                    # і є перевіркою: злізли, а далі приклик або спрацює, або ні
                    set_mounted(ctx.shared, False)
                    res.actions.append(PressKey(cfg.dismount_key, delay_after=0.4,
                                                reason="злізти зі звіра"))
                    res.events.append(f"!! пет не кличеться — схоже, персонаж верхи, "
                                      f"тисну {cfg.dismount_key} і пробую ще раз")
                    self.gave_up_at = ctx.now - cfg.retry_after + min(cfg.retry_after, 10.0)
                    return res
                res.events.append(f"!! {cfg.summon_key} не викликає пета за {cfg.give_up_after} "
                                  f"спроб — фармлю без нього, знову спробую через "
                                  f"{cfg.retry_after:.0f}с")
            return res

        if pet.present:
            # пет міг з'явитись, поки ми тримали паузу заради приклику: якщо не зняти
            # busy саме тут, бій і пошук цілі мовчать назавжди — «атака тупить»
            self._release(ctx)
            self.missing_streak = 0
            self.missing_since = None
            self.tries = 0
            self.gave_up = False
            return PipelineResult.idle()

        if self.gave_up:
            self._release(ctx)                       # здались — бій не чекає на нас
            if ctx.now - self.gave_up_at < cfg.retry_after:
                left = cfg.retry_after - (ctx.now - self.gave_up_at)
                return PipelineResult.idle(f"пета нема · нова спроба через {left:.0f}с")
            self.gave_up, self.tries = False, 0      # пауза минула — пробуємо знову

        self.missing_streak += 1
        if self.missing_since is None:
            self.missing_since = ctx.now
        missing_for = ctx.now - self.missing_since
        if (self.missing_streak < cfg.confirm_frames or missing_for < cfg.missing_for
                or ctx.now - self.last_try < cfg.cooldown):
            return PipelineResult.idle(f"пета нема ({missing_for:.0f}с)")

        if cfg.only_out_of_combat:
            target = read_target(ctx.shared)
            if target is not None and target.present:
                self.calm_since = None
                return PipelineResult.idle("пета нема · добиваю ціль, потім приклик")
            others = busy_reasons(ctx.shared) - {"pet_summon"}
            if others:
                self.calm_since = None
                return PipelineResult.idle(f"пета нема · чекаю: {', '.join(sorted(others))}")
            if self._hit_recently(ctx):
                # поки б'ють — тримати паузу марно: доб'ємо моба і спробуємо потім
                self.calm_since, self.hold_since = None, None
                set_busy(ctx.shared, "pet_summon", False)
                return PipelineResult.idle("пета нема · нас б'ють, каст переб'ють")
            # тримаємо паузу: інакше бот одразу бере нову ціль і тиші не буває ніколи
            if self.hold_since is None:
                self.hold_since = ctx.now
            set_busy(ctx.shared, "pet_summon", True)
            if ctx.now - self.hold_since > cfg.hold_for:
                self.hold_since, self.calm_since = None, None
                self.last_try = ctx.now                   # спробуємо після кулдауну
                set_busy(ctx.shared, "pet_summon", False)
                return PipelineResult.idle("пета нема · тиші не дочекався, фармлю далі")
            if self._moving(ctx):
                self.calm_since = None
                return PipelineResult.idle(f"пета нема · персонаж іде ({self.move_rate:.0f}), "
                                           f"чекаю зупинки")
            if self.calm_since is None:
                self.calm_since = ctx.now
            if ctx.now - self.calm_since < cfg.settle:
                return PipelineResult.idle("пета нема · даю персонажу зупинитись")
            self.hold_since = None

        self.last_try = ctx.now
        self.casting_until = ctx.now + cfg.cast_time
        self.tries += 1
        set_busy(ctx.shared, "pet_summon", True)
        events = [f"пета нема -> {cfg.summon_key}"]
        return PipelineResult(
            actions=[PressKey(cfg.summon_key, reason="приклик пета")],
            status=f"приклик пета ({cfg.cast_time:.0f}с)",
            events=events)

    def _release(self, ctx: PipelineContext) -> None:
        """Зняти паузу, яку тримали заради тиші перед прикликом."""
        self.hold_since, self.calm_since = None, None
        set_busy(ctx.shared, "pet_summon", False)

    def _done(self, ctx: PipelineContext, why: str) -> PipelineResult:
        self.casting_until = 0.0
        self.missing_streak = 0
        self.missing_since = None
        set_busy(ctx.shared, "pet_summon", False)
        return PipelineResult(status="", events=[why])
