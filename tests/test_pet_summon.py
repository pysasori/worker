"""Приклик пета: тиснемо F7, коли рамки пета нема, і не даємо перебити каст."""
from __future__ import annotations

import pytest
from PIL import Image

from app.pipelines.actions import PressKey
from app.pipelines.attack import AttackPipeline
from app.pipelines.base import Frame, PipelineContext
from app.pipelines.pet_summon import PetSummonPipeline
from app.pipelines.shared import SHARED_PET, SHARED_TARGET, TargetInfo, busy_reasons
from app.vision.schemas import BarReading

BLANK = Image.new("RGB", (100, 100))


@pytest.fixture(scope="module")
def summon_cfg(bot_config_raw):
    from app.pipelines.pet_summon import PetSummonConfig
    from tests.conftest import _spec

    # паузу спокою і витримку «скільки секунд без пета» перевіряють окремі тести,
    # у решті вони лише заважали б рахувати кадри
    return PetSummonConfig(**{**_spec(bot_config_raw, "pet_summon"), "settle": 0, "missing_for": 0})


def ctx_for(ts: float, shared: dict, pet_present: bool) -> PipelineContext:
    shared[SHARED_PET] = BarReading(present=pet_present, filled=82 if pet_present else 0, total=82)
    return PipelineContext(window="test", frame=Frame(image=BLANK, ts=ts), shared=shared)


def keys(res) -> list[str]:
    return [a.key for a in res.actions if isinstance(a, PressKey)]


def test_quiet_while_pet_is_alive(summon_cfg):
    pipe, shared = PetSummonPipeline(summon_cfg), {}
    for i in range(6):
        assert keys(pipe.process(ctx_for(i * 0.2, shared, True))) == []


def test_summons_after_a_few_frames_without_pet(summon_cfg):
    pipe, shared = PetSummonPipeline(summon_cfg), {}
    pressed = []
    for i in range(summon_cfg.confirm_frames + 1):
        pressed += keys(pipe.process(ctx_for(i * 0.2, shared, False)))
    assert pressed == [summon_cfg.summon_key]


def test_one_blink_of_the_frame_is_not_enough(summon_cfg):
    pipe, shared = PetSummonPipeline(summon_cfg), {}
    assert keys(pipe.process(ctx_for(0.0, shared, False))) == []
    assert keys(pipe.process(ctx_for(0.2, shared, True))) == []
    assert keys(pipe.process(ctx_for(0.4, shared, False))) == []


def summon_now(pipe, shared, start=0.0):
    for i in range(pipe.config.confirm_frames + 1):
        res = pipe.process(ctx_for(start + i * 0.2, shared, False))
    return res


def test_bot_does_not_fight_during_the_cast(summon_cfg, attack_cfg):
    """Удар під час касту перериває приклик — саме тому ставимо busy."""
    pipe, shared = PetSummonPipeline(summon_cfg), {}
    summon_now(pipe, shared)
    assert busy_reasons(shared) == {"pet_summon"}

    shared[SHARED_TARGET] = TargetInfo(present=True, bar=BarReading(present=True, filled=208, total=208),
                                       acquired_at=0.0, updated_at=1.0)
    attack = AttackPipeline(attack_cfg)
    res = attack.process(PipelineContext(window="test", frame=Frame(image=BLANK, ts=1.0), shared=shared))
    assert keys(res) == [], "поки йде приклик — не б'ємо"


def test_cast_ends_and_bot_returns_to_work(summon_cfg):
    pipe, shared = PetSummonPipeline(summon_cfg), {}
    summon_now(pipe, shared)
    mid = pipe.process(ctx_for(summon_cfg.cast_time / 2, shared, False))
    assert "приклик" in mid.status
    res = pipe.process(ctx_for(summon_cfg.cast_time + 1, shared, True))
    assert busy_reasons(shared) == set()
    assert "піт на місці" in res.events


def test_pet_appearing_early_frees_the_bot(summon_cfg):
    pipe, shared = PetSummonPipeline(summon_cfg), {}
    summon_now(pipe, shared)
    res = pipe.process(ctx_for(1.0, shared, True))       # з'явився раніше за таймер
    assert busy_reasons(shared) == set()
    assert "піт на місці" in res.events


def test_retry_waits_for_the_cooldown(summon_cfg):
    pipe, shared = PetSummonPipeline(summon_cfg), {}
    summon_now(pipe, shared)
    pipe.process(ctx_for(summon_cfg.cast_time + 0.1, shared, False))    # каст минув, пета нема
    soon = pipe.process(ctx_for(summon_cfg.cast_time + 0.3, shared, False))
    assert keys(soon) == [], "одразу знову не тиснемо"
    later = summon_now(pipe, shared, start=summon_cfg.cooldown + summon_cfg.cast_time + 1)
    assert keys(later) == [summon_cfg.summon_key]


def test_without_pet_data_it_stays_silent(summon_cfg):
    pipe = PetSummonPipeline(summon_cfg)
    res = pipe.process(PipelineContext(window="test", frame=Frame(image=BLANK, ts=0.0), shared={}))
    assert res.actions == []


def test_gives_up_when_summon_does_not_work(summon_cfg):
    """
    Якщо пет не з'являється (клавіша не та, пета нема взагалі) — бот не має
    всю ніч стояти в касті замість фарму.
    """
    from app.pipelines.shared import set_mounted

    cfg = summon_cfg.model_copy(update={"retry_after": 10_000})    # паузу тут не чекаємо
    pipe, shared = PetSummonPipeline(cfg), {}
    set_mounted(shared, False)            # точно не верхи: злазити нема з чого
    pressed = []
    t = 0.0
    for _ in range(10):
        for i in range(summon_cfg.confirm_frames + 1):
            res = pipe.process(ctx_for(t + i * 0.2, shared, False))
            pressed += keys(res)
        t += cfg.cooldown + cfg.cast_time + 1
    assert len(pressed) == cfg.give_up_after
    assert pipe.gave_up
    assert busy_reasons(shared) == set(), "здались — бот вільний воювати"


def test_stale_pet_frame_is_forgotten(real_frame):
    """
    Рамка зникла з екрана — бот не має лікувати неіснуючого пета за старими
    координатами. Саме через це він колись тиснув F3 у порожнечу.
    """
    from PIL import Image

    from app.pipelines.pet import PetHealConfig, PetHealPipeline
    from app.pipelines.shared import SHARED_PET

    pipe = PetHealPipeline(PetHealConfig())
    shared: dict = {}

    def tick(image, ts):
        pipe.process(PipelineContext(window="t", frame=Frame(image=image, ts=ts), shared=shared))
        return shared[SHARED_PET]

    assert tick(real_frame, 0.0).present, "спершу пет є"
    empty = Image.new("RGB", real_frame.size, (30, 60, 40))
    for i in range(1, 6):
        reading = tick(empty, i * 3.0)          # relocate_every = 2 с
    assert not reading.present, "рамки нема — і бот це визнає"


def test_timer_keys_do_not_break_the_cast(summon_cfg):
    """
    Клавіша по таймеру («1» раз на 1.5 с) перериває каст приклику — саме через це
    піт не з'являвся всю ніч, а бот здавався після трьох спроб.
    """
    from app.pipelines.periodic import PeriodicKeysConfig, PeriodicKeysPipeline

    pipe, shared = PetSummonPipeline(summon_cfg), {}
    timer = PeriodicKeysPipeline(PeriodicKeysConfig(keys={"1": 1.5}, press_on_start=True))
    summon_now(pipe, shared)                      # пішов каст

    ctx = PipelineContext(window="test", frame=Frame(image=BLANK, ts=1.0), shared=shared)
    assert keys(timer.process(ctx)) == [], "поки йде приклик — жодних клавіш"

    pipe.process(ctx_for(summon_cfg.cast_time + 1, shared, True))     # каст скінчився
    free = PipelineContext(window="test", frame=Frame(image=BLANK, ts=summon_cfg.cast_time + 1),
                           shared=shared)
    assert keys(timer.process(free)) == ["1"], "звільнились — тиснемо одразу"


def pressed_while(pipe, shared, start=0.0) -> list[str]:
    """Усі клавіші за серію кадрів без пета (а не лише з останнього кадру)."""
    out: list[str] = []
    for i in range(pipe.config.confirm_frames + 1):
        out += keys(pipe.process(ctx_for(start + i * 0.2, shared, False)))
    return out


def fail_until_gave_up(pipe, shared, cfg) -> float:
    """Прокрутити спроби без результату, поки бот не здасться. Повертає поточний час."""
    t = 0.0
    while not pipe.gave_up:
        for i in range(cfg.confirm_frames + 1):
            pipe.process(ctx_for(t + i * 0.2, shared, False))
        t += cfg.cooldown + cfg.cast_time + 1
    return t


def test_tries_again_after_a_pause(summon_cfg):
    """
    Здатися назавжди — погано: пет загинув у бою о 22:15 і бот до ранку бився б сам.
    Після паузи приклик повторюється.
    """
    from app.pipelines.shared import set_mounted

    pipe, shared = PetSummonPipeline(summon_cfg), {}
    set_mounted(shared, False)
    t = fail_until_gave_up(pipe, shared, summon_cfg)
    assert pressed_while(pipe, shared, start=t) == [], "пауза ще не минула"
    later = pressed_while(pipe, shared, start=t + summon_cfg.retry_after + summon_cfg.cast_time)
    assert later == [summon_cfg.summon_key], "після паузи — знову F7"
    assert not pipe.gave_up


def test_waits_for_the_fight_to_end(summon_cfg):
    """Посеред бою моб перериває каст приклику — спершу добиваємо ціль."""
    pipe, shared = PetSummonPipeline(summon_cfg), {}
    shared[SHARED_TARGET] = TargetInfo(present=True, bar=BarReading(present=True, filled=208, total=208))
    res = summon_now(pipe, shared)
    assert keys(res) == [], "ціль жива — не кличемо"
    assert "добиваю ціль" in res.status
    assert busy_reasons(shared) == set(), "і атаці не заважаємо"

    shared[SHARED_TARGET] = TargetInfo(present=False)          # ціль убита
    res = pipe.process(ctx_for(10.0, shared, False))
    assert keys(res) == [summon_cfg.summon_key]


def test_waits_while_loot_is_collected(summon_cfg):
    from app.pipelines.shared import set_busy

    pipe, shared = PetSummonPipeline(summon_cfg), {}
    set_busy(shared, "loot", True)
    res = summon_now(pipe, shared)
    assert keys(res) == []
    assert "чекаю: loot" in res.status
    set_busy(shared, "loot", False)
    assert keys(pipe.process(ctx_for(5.0, shared, False))) == [summon_cfg.summon_key]


def test_can_summon_in_combat_if_asked(summon_cfg):
    cfg = summon_cfg.model_copy(update={"only_out_of_combat": False})
    pipe, shared = PetSummonPipeline(cfg), {}
    shared[SHARED_TARGET] = TargetInfo(present=True, bar=BarReading(present=True, filled=208, total=208))
    assert pressed_while(pipe, shared) == [cfg.summon_key]



def test_waits_for_the_character_to_settle_after_loot(summon_cfg):
    """
    Після луту персонаж ще добігає до предметів, і рух перериває каст — так пет
    тричі не прийшов уночі. Тому після бою й луту чекаємо кілька секунд спокою.
    """
    from app.pipelines.shared import set_busy

    cfg = summon_cfg.model_copy(update={"settle": 3.0})
    pipe, shared = PetSummonPipeline(cfg), {}
    set_busy(shared, "loot", True)
    for i in range(6):
        pipe.process(ctx_for(i * 0.2, shared, False))
    set_busy(shared, "loot", False)                            # лут зібрали о 1.2 с
    early = []
    for i in range(7, 20):                                     # до 3.8 с
        early += keys(pipe.process(ctx_for(i * 0.2, shared, False)))
    assert early == [], "персонаж ще рухається — не кличемо"
    late = []
    for i in range(22, 30):                                    # після 4.4 с
        late += keys(pipe.process(ctx_for(i * 0.2, shared, False)))
    assert late == [cfg.summon_key]


def test_returning_pet_resets_the_give_up(summon_cfg):
    pipe, shared = PetSummonPipeline(summon_cfg), {}
    t = fail_until_gave_up(pipe, shared, summon_cfg)
    assert pipe.gave_up
    pipe.process(ctx_for(t + 10, shared, True))       # пет знову є
    assert not pipe.gave_up


def hp_frame(fraction: float, cfg) -> "Image.Image":
    """Кадр із заданим HP персонажа: смужка в лівому верхньому куті."""
    from PIL import Image as PILImage

    img = PILImage.new("RGB", (400, 200), (20, 30, 40))
    px = img.load()
    box = cfg.hp_region
    for y in range(box.y + 1, box.y + box.h - 1):
        for x in range(box.x, box.x + int(box.w * fraction)):
            px[x, y] = (207, 41, 20)
    return img


def test_does_not_summon_while_being_hit(summon_cfg):
    """
    Уночі пет не приходив: моби били персонажа, і каст приклику щоразу переривався
    («Использование умения было прервано»). Поки HP падає — не кличемо.
    """
    cfg = summon_cfg.model_copy(update={"settle": 3.0})
    pipe, shared = PetSummonPipeline(cfg), {}

    def tick(hp: float, ts: float):
        shared[SHARED_PET] = BarReading(present=False, filled=0, total=82)
        ctx = PipelineContext(window="t", frame=Frame(image=hp_frame(hp, cfg), ts=ts), shared=shared)
        return keys(pipe.process(ctx))

    pressed = []
    hp = 1.0
    for i in range(12):                       # нас безперервно б'ють
        hp -= 0.05
        pressed += tick(hp, i * 0.5)
    assert pressed == [], "поки б'ють — приклик марний"

    for i in range(12, 30):                   # удари припинились
        pressed += tick(hp, i * 0.5)
    assert pressed == [cfg.summon_key], "стало тихо — кличемо"


def test_does_not_summon_while_the_character_walks(summon_cfg):
    """
    Після збору лута персонаж ще дочовгує до предметів. Рух рве каст так само, як удар,
    тому чекаємо, поки картинка перестане їхати.
    """
    from PIL import Image as PILImage

    cfg = summon_cfg.model_copy(update={"settle": 2.0})
    pipe, shared = PetSummonPipeline(cfg), {}
    still = hp_frame(1.0, cfg)
    walking = [hp_frame(1.0, cfg) for _ in range(2)]
    for n, frame in enumerate(walking):                 # дві геть різні картинки світу
        px = frame.load()
        for y in range(cfg.still_area.y, min(frame.height, cfg.still_area.y + 40)):
            for x in range(cfg.still_area.x, min(frame.width, cfg.still_area.x + 60)):
                px[x, y] = (250, 250, 250) if n else (0, 0, 0)

    def tick(image, ts):
        shared[SHARED_PET] = BarReading(present=False, filled=0, total=82)
        ctx = PipelineContext(window="t", frame=Frame(image=image, ts=ts), shared=shared)
        return keys(pipe.process(ctx))

    pressed = []
    for i in range(10):                                  # картинка стрибає — персонаж іде
        pressed += tick(walking[i % 2], i * 0.3)
    assert pressed == [], "поки йде — не кличемо"
    for i in range(10, 30):                              # зупинився
        pressed += tick(still, i * 0.3)
    assert pressed == [cfg.summon_key]


def test_holds_the_fight_to_catch_a_quiet_moment(summon_cfg):
    """
    Між убитими мобами тиші не буває: убив — зібрав — узяв наступного. Тому заради
    приклику бот сам тримає паузу і не дає взяти нову ціль.
    """
    cfg = summon_cfg.model_copy(update={"settle": 2.0, "hold_for": 8.0})
    pipe, shared = PetSummonPipeline(cfg), {}
    still = hp_frame(1.0, cfg)

    def tick(ts):
        shared[SHARED_PET] = BarReading(present=False, filled=0, total=82)
        ctx = PipelineContext(window="t", frame=Frame(image=still, ts=ts), shared=shared)
        return keys(pipe.process(ctx))

    pressed = []
    for i in range(16):
        pressed += tick(i * 0.3)
        if cfg.confirm_frames <= i <= 10:
            assert busy_reasons(shared) == {"pet_summon"}, "поки ловимо тишу — бій не починаємо"
    assert pressed == [cfg.summon_key]


def test_gives_the_fight_back_if_quiet_never_comes(summon_cfg):
    cfg = summon_cfg.model_copy(update={"settle": 3.0, "hold_for": 4.0})
    pipe, shared = PetSummonPipeline(cfg), {}
    frames = [hp_frame(1.0, cfg), hp_frame(0.9, cfg)]     # HP падає — нас б'ють
    pressed = []
    for i in range(40):
        shared[SHARED_PET] = BarReading(present=False, filled=0, total=82)
        ctx = PipelineContext(window="t", frame=Frame(image=frames[i % 2], ts=i * 0.3), shared=shared)
        pressed += keys(pipe.process(ctx))
    assert pressed == [], "тиші не було — приклик не тиснемо"
    assert busy_reasons(shared) == set(), "і бій не блокуємо назавжди"


def test_blinking_frame_does_not_dismiss_a_living_pet(summon_cfg):
    """
    Клавіша приклику — перемикач: якщо пет живий, вона його ВІДКЛИКАЄ. Рамка інколи
    блимає на частку секунди, і бот через це відкликав щойно взятого пета.
    """
    cfg = summon_cfg.model_copy(update={"missing_for": 5.0, "settle": 0})
    pipe, shared = PetSummonPipeline(cfg), {}
    still = hp_frame(1.0, cfg)
    pressed = []
    for i in range(40):                      # рамка зникає на 2 с і повертається
        ts = i * 0.2
        present = not (2.0 <= ts < 4.0)
        shared[SHARED_PET] = BarReading(present=present, filled=82 if present else 0, total=82)
        ctx = PipelineContext(window="t", frame=Frame(image=still, ts=ts), shared=shared)
        pressed += keys(pipe.process(ctx))
    assert pressed == [], "дві секунди без рамки — це ще не «пета нема»"


def test_long_absence_still_summons(summon_cfg):
    cfg = summon_cfg.model_copy(update={"missing_for": 5.0, "settle": 0})
    pipe, shared = PetSummonPipeline(cfg), {}
    still = hp_frame(1.0, cfg)
    pressed = []
    for i in range(60):
        ts = i * 0.2
        shared[SHARED_PET] = BarReading(present=False, filled=0, total=82)
        ctx = PipelineContext(window="t", frame=Frame(image=still, ts=ts), shared=shared)
        pressed += keys(pipe.process(ctx))
    assert pressed == [cfg.summon_key], "а ось шість секунд — уже так"


def test_does_not_hold_the_fight_after_the_pet_came_back():
    """
    Живий випадок: пет з'явився, поки бот тримав тишу заради приклику, і пауза
    лишилась висіти. Пошук цілі й атака мовчали — «атака тупить».
    """
    from app.pipelines.pet_summon import PetSummonConfig

    pipe, shared = PetSummonPipeline(PetSummonConfig(missing_for=0, confirm_frames=1)), {}
    for i in range(4):
        pipe.process(ctx_for(i * 0.2, shared, pet_present=False))
    assert busy_reasons(shared) == {"pet_summon"}, "тримаємо тишу заради приклику"
    pipe.process(ctx_for(2.0, shared, pet_present=True))
    assert busy_reasons(shared) == set(), "пет прийшов — бій вільний"


def test_gets_off_the_mount_when_the_pet_will_not_come(summon_cfg):
    """
    Живий випадок 20.09: бот фармив верхи на літаючому звірі й без пета. Верхи гра
    відповідає «Здесь невозможно призвать питомца», а на екрані нічого не видно —
    кадр верхи й пішки різняться лише самим персонажем. Тому мовчазні невдачі
    приклику і є ознакою: злазимо й пробуємо ще раз.
    """
    from app.pipelines.shared import SHARED_ALT, SHARED_GROUND, Altitude, is_mounted

    pipe, shared = PetSummonPipeline(summon_cfg), {}     # стан «верхи» невідомий...
    shared[SHARED_GROUND] = 22
    shared[SHARED_ALT] = Altitude(z=75, known=True)       # ...але висота каже: ми в повітрі
    t = fail_until_gave_up(pipe, shared, summon_cfg)
    assert is_mounted(shared) is False, "вважаємо, що злізли"

    soon = pressed_while(pipe, shared, start=t + 11.0)
    assert soon == [summon_cfg.summon_key], "після злізання пробуємо скоро, а не через хвилину"


def test_does_not_press_the_flight_key_when_it_cannot_tell_it_is_mounted(summon_cfg):
    """
    Клавіша польоту — перемикач: натиснута на землі, вона САДИТЬ на літаючого звіра, і druid
    літав та бився в повітрі. Невідомо, верхи чи ні, і висоти нема — клавішу не чіпаємо.
    """
    pipe, shared = PetSummonPipeline(summon_cfg), {}
    pressed, t = [], 0.0
    while not pipe.gave_up and t < 600:
        for i in range(summon_cfg.confirm_frames + 1):
            pressed += keys(pipe.process(ctx_for(t + i * 0.2, shared, False)))
        t += summon_cfg.cooldown + summon_cfg.cast_time + 1
    assert pipe.gave_up
    pressed += pressed_while(pipe, shared, start=t + 11.0)
    assert summon_cfg.dismount_key not in pressed, "на землі 9 посадила б на звіра"
    assert shared.get("mount") is None, "стан «верхи» лишився невідомим"
