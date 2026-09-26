"""Тести ремонту: кроки йдуть лише коли відповідне вікно справді на екрані."""
from __future__ import annotations

import pytest
from PIL import Image

from pathlib import Path

from app.core.geometry import Point, Region
from app.pipelines.actions import ClickAt, PressKey
from app.pipelines.base import Frame, PipelineContext
from app.pipelines.repair import RepairConfig, RepairPipeline, RepairState
from app.pipelines.shared import TargetInfo, busy_reasons, write_target
from app.vision.schemas import BarReading
from app.vision.ui import UiMarker
from tests.conftest import FIXTURES

WHITE = (255, 255, 255)
GOLD = (200, 160, 40)
TEMPLATES = Path(__file__).resolve().parents[1] / "assets" / "templates"

# Кнопки «Лавки» шукаються за виглядом, тому синтетичний кадр складаємо з тих самих
# зразків, які використовує бот, і кладемо їх у довільні місця: саме так вікно й
# поводиться в грі — відкривається там, де його лишили мишею.
SHOP_AT = (400, 300)
REPAIR_AT = (505, 700)
CONFIRM_AT = (612, 556)
BAG_AT = (900, 200)          # рюкзак теж відкривається там, де його лишили

BAG = "bag"
SHOP = "shop"
CONFIRM = "confirm"


def _tpl(name: str) -> Image.Image:
    return Image.open(TEMPLATES / name).convert("RGB")


def center_of(name: str, at: tuple[int, int]) -> tuple[int, int]:
    w, h = _tpl(name).size
    return at[0] + w // 2, at[1] + h // 2


@pytest.fixture
def cfg() -> RepairConfig:
    return RepairConfig(
        every=10.0, idle_before=0.0, step_timeout=5.0, hover_delay=0.1, step_delay=0.0,
        # на синтетичних кадрах справжньої іконки нема, тому уточнення вимкнене:
        # його поведінку перевіряють окремі тести на реальних кадрах
        shop_icon_search=None,
    )


def bag_icon_at(at: tuple[int, int], cfg: RepairConfig) -> tuple[int, int]:
    """Куди бот клікне, якщо вікно «Рюкзак» лежить у точці at."""
    x, y = center_of("bag_title.png", at)
    return x + cfg.shop_offset.x, y + cfg.shop_offset.y


def frame_with(*parts) -> Image.Image:
    """Кадр, де «відкриті» лише названі елементи інтерфейсу."""
    img = Image.new("RGB", (1440, 1080), (5, 5, 5))
    for part in parts:
        if part == BAG:
            img.paste(_tpl("bag_title.png"), BAG_AT)
        elif part == SHOP:
            img.paste(_tpl("shop_title.png"), SHOP_AT)
            img.paste(_tpl("repair_all.png"), REPAIR_AT)
        elif part == CONFIRM:
            img.paste(_tpl("repair_confirm.png"), CONFIRM_AT)
    return img


def ctx(image: Image.Image, ts: float, shared: dict, target: bool = False) -> PipelineContext:
    write_target(shared, TargetInfo(present=target, bar=BarReading(), updated_at=ts))
    return PipelineContext(window="test", frame=Frame(image=image, ts=ts), shared=shared)


def keys(res) -> list[str]:
    return [a.key for a in res.actions if isinstance(a, PressKey)]


def clicks(res) -> list[tuple[int, int]]:
    return [(a.x, a.y) for a in res.actions if isinstance(a, ClickAt)]


def test_waits_for_its_hour(cfg):
    pipe, shared = RepairPipeline(cfg), {}
    empty = frame_with()
    assert pipe.process(ctx(empty, 0.0, shared)).actions == []
    assert pipe.process(ctx(empty, 5.0, shared)).actions == []
    assert keys(pipe.process(ctx(empty, 11.0, shared))) == [cfg.bag_key]


def test_never_repairs_in_combat(cfg):
    pipe, shared = RepairPipeline(cfg), {}
    empty = frame_with()
    pipe.process(ctx(empty, 0.0, shared))
    assert pipe.process(ctx(empty, 100.0, shared, target=True)).actions == []
    assert pipe.state is RepairState.IDLE


def test_full_flow_step_by_step(cfg):
    pipe, shared = RepairPipeline(cfg), {}
    pipe.process(ctx(frame_with(), 0.0, shared))
    pipe.process(ctx(frame_with(), 11.0, shared))                    # тисне B
    assert busy_reasons(shared) == {"repair"}

    res = pipe.process(ctx(frame_with(BAG), 11.5, shared))           # рюкзак відкрився
    assert clicks(res) == [bag_icon_at(BAG_AT, cfg)]

    res = pipe.process(ctx(frame_with(BAG, SHOP), 12.0, shared))     # Лавка відкрилась
    assert clicks(res) == [center_of("repair_all.png", REPAIR_AT)]

    res = pipe.process(ctx(frame_with(BAG, SHOP, CONFIRM), 12.5, shared))
    assert clicks(res) == [center_of("repair_confirm.png", CONFIRM_AT)]
    assert "спорядження відремонтовано" in res.events

    res = pipe.process(ctx(frame_with(BAG), 13.0, shared))           # закриваємось
    assert keys(res) == [cfg.close_key, cfg.bag_key]
    assert pipe.state is RepairState.VERIFY_CLOSED
    assert busy_reasons(shared) == {"repair"}, "поки вікна відкриті — бот не воює"

    res = pipe.process(ctx(frame_with(), 13.5, shared))               # вікон більше нема
    assert pipe.state is RepairState.IDLE
    assert busy_reasons(shared) == set()
    assert "вікна закрито, працюю далі" in res.events


def test_keeps_closing_until_windows_are_gone(cfg):
    """Одного Esc могло не вистачити — перевіряємо, а не віримо на слово."""
    pipe, shared = RepairPipeline(cfg), {}
    pipe.process(ctx(frame_with(), 0.0, shared))
    pipe.process(ctx(frame_with(), 11.0, shared))
    pipe.process(ctx(frame_with(BAG), 11.5, shared))
    pipe.process(ctx(frame_with(BAG, SHOP), 12.0, shared))
    pipe.process(ctx(frame_with(BAG, SHOP, CONFIRM), 12.5, shared))
    pipe.process(ctx(frame_with(BAG, SHOP), 13.0, shared))            # перша спроба закрити

    again = pipe.process(ctx(frame_with(BAG, SHOP), 13.5, shared))    # Лавка ще висить
    assert keys(again) == [cfg.close_key]
    bag_only = pipe.process(ctx(frame_with(BAG), 14.0, shared))       # лишився рюкзак
    assert keys(bag_only) == [cfg.bag_key]
    done = pipe.process(ctx(frame_with(), 14.5, shared))
    assert pipe.state is RepairState.IDLE
    assert done.actions == []


def test_gives_up_closing_but_frees_the_bot(cfg):
    """Якщо закрити не вдалось — не зависаємо назавжди з busy."""
    pipe, shared = RepairPipeline(cfg), {}
    pipe.process(ctx(frame_with(), 0.0, shared))
    pipe.process(ctx(frame_with(), 11.0, shared))
    pipe.process(ctx(frame_with(BAG), 11.5, shared))
    pipe.process(ctx(frame_with(BAG, SHOP), 12.0, shared))
    pipe.process(ctx(frame_with(BAG, SHOP, CONFIRM), 12.5, shared))
    pipe.process(ctx(frame_with(BAG, SHOP), 13.0, shared))
    res = pipe.process(ctx(frame_with(BAG, SHOP), 30.0, shared))      # вийшов час
    assert pipe.state is RepairState.IDLE
    assert busy_reasons(shared) == set()
    assert any("не вдалось закрити" in e for e in res.events)


def test_hover_before_click(cfg):
    """Кнопки PW не реагують на клік без наведення."""
    pipe, shared = RepairPipeline(cfg), {}
    pipe.process(ctx(frame_with(), 0.0, shared))
    pipe.process(ctx(frame_with(), 11.0, shared))
    res = pipe.process(ctx(frame_with(BAG), 11.5, shared))
    assert all(a.hover_delay > 0 for a in res.actions if isinstance(a, ClickAt))


def test_does_not_click_blindly_when_window_never_opens(cfg):
    pipe, shared = RepairPipeline(cfg), {}
    pipe.process(ctx(frame_with(), 0.0, shared))
    pipe.process(ctx(frame_with(), 11.0, shared))
    quiet = pipe.process(ctx(frame_with(), 12.0, shared))            # рюкзак не відкрився
    assert clicks(quiet) == []
    res = pipe.process(ctx(frame_with(), 20.0, shared))              # вийшов час
    assert clicks(res) == []
    assert keys(res) == [], "нічого не відкрилось — нема чого й закривати"
    assert pipe.state is RepairState.IDLE
    assert busy_reasons(shared) == set()


def test_closes_bag_if_it_stayed_open_after_failure(cfg):
    pipe, shared = RepairPipeline(cfg), {}
    pipe.process(ctx(frame_with(), 0.0, shared))
    pipe.process(ctx(frame_with(), 11.0, shared))
    res = pipe.process(ctx(frame_with(BAG), 20.0, shared))           # таймаут з відкритим рюкзаком
    assert cfg.bag_key in keys(res), "інакше рюкзак лишиться відкритим і бот не візьме ціль"


def test_nothing_to_repair_is_not_an_error(cfg):
    pipe, shared = RepairPipeline(cfg), {}
    pipe.process(ctx(frame_with(), 0.0, shared))
    pipe.process(ctx(frame_with(), 11.0, shared))
    pipe.process(ctx(frame_with(BAG), 11.5, shared))
    pipe.process(ctx(frame_with(BAG, SHOP), 12.0, shared))           # чекаємо підтвердження
    res = pipe.process(ctx(frame_with(BAG, SHOP), 30.0, shared))     # діалог не з'явився
    assert "ремонтувати нічого" in res.events
    assert keys(res) == [cfg.close_key], "Лавку закриваємо"
    pipe.process(ctx(frame_with(BAG), 30.5, shared))
    pipe.process(ctx(frame_with(), 31.0, shared))
    assert pipe.state is RepairState.IDLE
    assert busy_reasons(shared) == set()


# ---- ознака зносу (жовта іконка зброї справа вгорі) ---------------------------
@pytest.fixture(scope="module")
def real_repair_cfg(bot_config_raw):
    from tests.conftest import _spec

    return RepairConfig(**_spec(bot_config_raw, "repair"))


def damaged_frame():
    from tests.conftest import FIXTURES

    return Image.open(FIXTURES / "frame_1440_gear_damaged.png").convert("RGB")


def repaired_frame():
    from tests.conftest import FIXTURES

    return Image.open(FIXTURES / "frame_1440_gear_repaired.png").convert("RGB")


def broken_frame():
    from tests.conftest import FIXTURES

    return Image.open(FIXTURES / "frame_1440_gear_broken.png").convert("RGB")


def any_damage(cfg, frame) -> bool:
    from app.vision.template import find_template

    return find_template(frame, cfg.worn_icon) is not None


def test_damage_markers_cover_worn_and_broken(real_repair_cfg):
    """Іконка зносу є і на зношеному, і на зламаному; після ремонту її нема."""
    assert any_damage(real_repair_cfg, damaged_frame())
    assert any_damage(real_repair_cfg, broken_frame())
    assert not any_damage(real_repair_cfg, repaired_frame())


def test_grass_is_not_worn_gear(real_repair_cfg):
    """
    Живий випадок: іконку шукали за жовтим кольором, а під нею — жовто-зелена трава.
    Бот «ремонтував» щодесять хвилин і писав, ніби не вистачило монет.
    """
    from tests.conftest import FIXTURES

    for name in ("frame_1440_target_and_pet.png", "frame_1440_nav_closed.png"):
        field = Image.open(FIXTURES / name).convert("RGB")
        assert not any_damage(real_repair_cfg, field), f"{name}: тут нема зношеного"


def test_broken_gear_also_starts_repair(real_repair_cfg):
    pipe, shared = RepairPipeline(real_repair_cfg), {}
    broken = broken_frame()          # на цьому кадрі рюкзак уже відкритий
    pipe.process(ctx(broken, 0.0, shared))
    t = real_repair_cfg.min_interval + 1
    for i in range(6):               # іконка має протриматись кілька секунд
        pipe.process(ctx(broken, t + i * 2, shared))
    assert pipe.state is not RepairState.IDLE, "червона іконка теж запускає ремонт"
    assert busy_reasons(shared) == {"repair"}


def test_broken_gear_does_not_wait_an_hour(real_repair_cfg):
    """Зламане (червоне) лагодимо швидко — така річ не працює взагалі."""
    pipe, shared = RepairPipeline(real_repair_cfg), {}
    broken = broken_frame()
    pipe.process(ctx(broken, 0.0, shared))                      # відлік пішов
    quiet = pipe.process(ctx(broken, real_repair_cfg.min_interval - 5, shared))
    assert quiet.actions == [], "занадто рано навіть за поломкою"
    pipe.process(ctx(broken, real_repair_cfg.min_interval + 1, shared))
    assert pipe.state is not RepairState.IDLE
    assert busy_reasons(shared) == {"repair"}


def test_worn_gear_waits_for_its_hour(real_repair_cfg):
    """
    Жовтий знос з'являється в бою за лічені хвилини. Поки він прискорював ремонт,
    бот бігав у Лавку щотри хвилини — тепер зношене чекає свою годину.
    """
    pipe, shared = RepairPipeline(real_repair_cfg), {}
    worn = damaged_frame()
    pipe.process(ctx(worn, 0.0, shared))
    assert pipe.process(ctx(worn, real_repair_cfg.min_interval + 1, shared)).actions == []
    assert pipe.state is RepairState.IDLE
    res = pipe.process(ctx(worn, real_repair_cfg.every + 1, shared))
    assert keys(res) == [real_repair_cfg.bag_key]


def test_no_damage_means_wait_for_the_timer(real_repair_cfg):
    pipe, shared = RepairPipeline(real_repair_cfg), {}
    clean = repaired_frame()
    pipe.process(ctx(clean, 0.0, shared))
    assert pipe.process(ctx(clean, real_repair_cfg.min_interval + 1, shared)).actions == []
    res = pipe.process(ctx(clean, real_repair_cfg.every + 1, shared))
    # на цьому кадрі рюкзак уже відкритий, тому клавішу тиснути не треба —
    # важливо, що ремонт таки почався
    assert pipe.state is not RepairState.IDLE, "за таймером ремонт усе одно буває"
    assert busy_reasons(shared) == {"repair"}
    assert any("час ремонту" in e for e in res.events)


# ---- пошук іконки «Лавка» -----------------------------------------------------
def test_repair_waits_instead_of_clicking_blindly(real_repair_cfg, monkeypatch):
    """Іконки не видно — чекаємо рюкзак і нікуди не клікаємо."""
    monkeypatch.setattr("app.pipelines.repair.find_template", lambda *a, **k: None)

    pipe, shared = RepairPipeline(real_repair_cfg), {}
    worn = damaged_frame()
    pipe.process(ctx(worn, 0.0, shared))
    pipe.process(ctx(worn, real_repair_cfg.every + 1, shared))            # тисне B
    open_bag = Image.open(FIXTURES / "frame_1440_bag_moved.png").convert("RGB")
    res = pipe.process(ctx(open_bag, real_repair_cfg.every + 2, shared))
    assert clicks(res) == [], "жодного кліку навмання"
    assert "чекаю" in res.status


def test_guard_sees_both_kinds_of_dialogs(bot_config_raw):
    """
    Поки модальне вікно висить, гра ігнорує кліки — саме через це ремонт раз за разом
    не спрацьовував. Сторож має бачити і підтвердження, і «Бросить монеты».
    """
    from app.pipelines.dialog_guard import DialogGuardConfig
    from app.vision.ui import marker_present
    from tests.conftest import _spec

    from app.vision.template import find_template

    cfg = DialogGuardConfig(**_spec(bot_config_raw, "dialog_guard"))

    def seen(frame):
        return (any(marker_present(frame, m) for m in cfg.markers)
                or any(find_template(frame, t) for t in cfg.templates))

    assert seen(live("frame_1440_repair_confirm.png")), "діалог підтвердження видно"
    assert not seen(repaired_frame()), "без діалогів сторож мовчить"
    assert not seen(live("frame_1440_ground_loot.png")), "підписи луту — не діалог"


# ---- кнопки на справжніх кадрах -----------------------------------------------
def live(name: str) -> Image.Image:
    from tests.conftest import FIXTURES

    return Image.open(FIXTURES / name).convert("RGB")


def test_buttons_are_found_on_real_frames(real_repair_cfg):
    """
    Кадри зняті з гри при пересунутому вікні «Лавка»: раніше бот клікав у стару
    координату (352, 668) і ремонт мовчки не робився.
    """
    from app.vision.template import find_template

    shop = live("frame_1440_shop_open.png")
    button = find_template(shop, real_repair_cfg.repair_all)
    assert button is not None and (button.x, button.y) == (718, 702)
    assert find_template(shop, real_repair_cfg.shop_open) is not None
    assert find_template(shop, real_repair_cfg.confirm) is None, "діалогу ще нема"

    dialog = live("frame_1440_repair_confirm.png")
    yes = find_template(dialog, real_repair_cfg.confirm)
    assert yes is not None and (yes.x, yes.y) == (661, 568)


def test_no_false_buttons_without_the_shop(real_repair_cfg):
    from app.vision.template import find_template

    for name in ("frame_1440_bag_only.png", "frame_1440_target_and_pet.png"):
        frame = live(name)
        assert find_template(frame, real_repair_cfg.repair_all) is None, name
        assert find_template(frame, real_repair_cfg.confirm) is None, name
        assert find_template(frame, real_repair_cfg.shop_open) is None, name


def test_full_repair_on_real_frames(real_repair_cfg):
    """Той самий шлях, що й у грі: рюкзак -> Лавка -> Починить все -> Да."""
    pipe, shared = RepairPipeline(real_repair_cfg), {}
    bag, shop, dialog = (live("frame_1440_bag_only.png"), live("frame_1440_shop_open.png"),
                         live("frame_1440_repair_confirm.png"))
    t = real_repair_cfg.every + 1
    pipe.process(ctx(bag, 0.0, shared))
    pipe.process(ctx(bag, t, shared))                         # рюкзак уже відкритий
    res = pipe.process(ctx(bag, t + 0.5, shared))
    assert clicks(res) == [(1067, 695)], "клік по іконці «Лавка»"
    res = pipe.process(ctx(shop, t + 1.0, shared))
    assert clicks(res) == [(718, 702)], "клік по «Починить все»"
    res = pipe.process(ctx(dialog, t + 1.5, shared))
    assert clicks(res) == [(661, 568)], "клік по «Да»"
    assert "спорядження відремонтовано" in res.events


def test_guard_ignores_loot_labels_near_the_character():
    """Кадр, на якому старий сторож тиснув Esc: діалогу нема, лише підписи луту й «Список»."""
    from app.pipelines.dialog_guard import DialogGuardConfig
    from app.vision.template import find_template
    from app.vision.ui import marker_present

    cfg = DialogGuardConfig()
    frame = live("frame_1440_guard_false_alarm.png")
    assert not any(marker_present(frame, m) for m in cfg.markers)
    assert not any(find_template(frame, t) for t in cfg.templates)



def test_guard_sees_the_drop_coins_dialog():
    from app.pipelines.dialog_guard import DialogGuardConfig
    from app.vision.template import find_template

    cfg = DialogGuardConfig()
    assert any(find_template(live("frame_1440_drop_coins.png"), t) for t in cfg.templates)


def test_guard_ignores_coins_lying_on_the_ground():
    """Монета на землі давала жовто-зелені пікселі там, де колись шукали діалог."""
    from app.pipelines.dialog_guard import DialogGuardConfig
    from app.vision.template import find_template

    cfg = DialogGuardConfig()
    for name in ("frame_1440_ground_loot.png", "frame_1440_no_loot.png", "frame_1440_guard_false_alarm.png",
                 "frame_1440_coin_on_ground.png"):
        assert not any(find_template(live(name), t) for t in cfg.templates), name



def test_give_up_does_not_reopen_the_bag(cfg):
    """
    Було так: Esc закривав рюкзак, а наступний B одразу відкривав його знову, і той
    лишався відкритим до ранку. Тепер закриваємось із перевіркою по кадру.
    """
    pipe, shared = RepairPipeline(cfg), {}
    pipe.process(ctx(frame_with(), 0.0, shared))
    pipe.process(ctx(frame_with(), 11.0, shared))                    # тисне B
    res = pipe.process(ctx(frame_with(BAG), 20.0, shared))           # Лавка так і не відкрилась
    assert keys(res) == [cfg.bag_key], "закриваємо саме рюкзак, без сліпих Esc"
    done = pipe.process(ctx(frame_with(), 21.0, shared))             # рюкзака вже нема
    assert pipe.state is RepairState.IDLE
    assert keys(done) == []
    assert busy_reasons(shared) == set()



def test_damage_numbers_do_not_trigger_repair(real_repair_cfg):
    """
    Над іконкою спорядження пролітають цифри урону («405» червоним) — рівно через них
    бот уночі бігав у Лавку щотри хвилини. Справжня поломка з екрана не зникає.
    """
    pipe, shared = RepairPipeline(real_repair_cfg), {}
    broken, clean = broken_frame(), repaired_frame()
    t = 0.0
    pipe.process(ctx(clean, t, shared))
    for _ in range(20):                       # спалах раз на кілька секунд
        t += 3
        pipe.process(ctx(broken, t, shared))  # цифра з'явилась
        t += 1
        res = pipe.process(ctx(clean, t, shared))   # і зникла
        assert res.actions == [], "спалах — не привід іти в Лавку"
    assert pipe.state is RepairState.IDLE


def test_lasting_red_icon_still_starts_repair(real_repair_cfg):
    pipe, shared = RepairPipeline(real_repair_cfg), {}
    broken = broken_frame()
    t = real_repair_cfg.min_interval + 1
    pipe.process(ctx(broken, 0.0, shared))
    for i in range(6):                        # іконка висить і не зникає
        res = pipe.process(ctx(broken, t + i * 2, shared))
    assert pipe.state is not RepairState.IDLE



def test_clicks_the_shop_icon_again_when_nothing_opened(cfg):
    """
    Гра іноді не помічає клік по іконці «Лавка», і ремонт зривався ні на чому.
    Тепер клік повторюється, і лише потім бот здається.
    """
    pipe, shared = RepairPipeline(cfg.model_copy(update={"shop_icon_search": None})), {}
    pipe.process(ctx(frame_with(), 0.0, shared))
    pipe.process(ctx(frame_with(), 11.0, shared))                    # тисне B
    first = pipe.process(ctx(frame_with(BAG), 11.5, shared))         # клік по іконці
    assert len(clicks(first)) == 1
    assert clicks(pipe.process(ctx(frame_with(BAG), 12.0, shared))) == [], "не молотимо щокадру"
    again = pipe.process(ctx(frame_with(BAG), 11.5 + cfg.click_again_after + 0.1, shared))
    assert clicks(again) == clicks(first), "той самий клік ще раз"
    ok = pipe.process(ctx(frame_with(BAG, SHOP), 16.0, shared))
    assert clicks(ok) == [center_of("repair_all.png", REPAIR_AT)], "Лавка відкрилась — працюємо далі"


def test_finds_the_shop_icon_wherever_the_bag_window_is(real_repair_cfg):
    """
    Рюкзак відкривається там, де його лишили мишею. Одного разу вікно переїхало на
    60 px, іконку шукали в радіусі 45 — і ремонт мовчки зривався, а спорядження
    лишалось поламаним. Тепер точка рахується від заголовка вікна.
    """
    pipe = RepairPipeline(real_repair_cfg)
    old = pipe._shop_icon(live("frame_1440_bag_only.png"))
    assert old and abs(old.x - 1067) <= 6 and abs(old.y - 695) <= 6

    moved = pipe._shop_icon(live("frame_1440_bag_moved.png"))
    assert moved and abs(moved.x - 1067) <= 6 and abs(moved.y - 695) <= 6

    other = pipe._shop_icon(live("frame_1440_bag_new_layout.png"))
    assert other and abs(other.x - 1019) <= 6 and abs(other.y - 719) <= 6, "вікно в іншому місці"


def test_closed_bag_gives_no_icon(real_repair_cfg):
    pipe = RepairPipeline(real_repair_cfg)
    for name in ("frame_1440_target_and_pet.png", "frame_1440_no_loot.png", "frame_1440_gear_damaged.png"):
        assert pipe._shop_icon(live(name)) is None, name


def test_repair_that_did_not_help_pauses_the_fast_path(real_repair_cfg):
    """
    Знос лишився після ремонту — найчастіше просто не вистачило монет (перевірено
    наживо: ремонт коштував 9358 при 10 615 у гаманці). Тоді бігати в Лавку кожні
    десять хвилин марно, робимо паузу.
    """
    pipe, shared = RepairPipeline(real_repair_cfg), {}
    broken = broken_frame()
    pipe.process(ctx(broken, 0.0, shared))              # відлік пішов
    t = real_repair_cfg.min_interval + 1
    for i in range(6):
        pipe.process(ctx(broken, t + i * 2, shared))
    assert pipe.state is not RepairState.IDLE, "знос видно — ідемо ремонтувати"

    pipe.reset()                                        # ремонт відпрацював
    pipe.last_repair = t + 20
    pipe.check_broken_at = t + 25
    pipe.process(ctx(broken, t + 30, shared))
    assert pipe.useless_until > t + 30, "ремонт не допоміг — робимо паузу"

    t2 = t + real_repair_cfg.min_interval + 60
    for i in range(8):
        pipe.process(ctx(broken, t2 + i * 3, shared))
    assert pipe.state is RepairState.IDLE, "у паузі за ознакою не ходимо"


def test_wear_alone_starts_repair_without_waiting_an_hour(real_repair_cfg):
    """Влад просив не раз на годину: жовтий знос теж веде в Лавку, але не частіше ніж дозволено."""
    pipe, shared = RepairPipeline(real_repair_cfg), {}
    worn = damaged_frame()
    pipe.process(ctx(worn, 0.0, shared))
    for i in range(4):
        pipe.process(ctx(worn, 10 + i * 2, shared))
    assert not pipe._due(ctx(worn, 20.0, shared)), "занадто рано"
    assert pipe._due(ctx(worn, real_repair_cfg.min_interval + 1, shared)), "знос — привід іти в Лавку"


# ---- забуте вікно гри ------------------------------------------------------------
def guard_ctx(frame, ts: float, shared: dict):
    from app.pipelines.base import Frame, PipelineContext

    return PipelineContext(window="t", frame=Frame(image=frame, ts=ts), shared=shared)


def test_guard_closes_a_forgotten_bag():
    """
    Живий випадок за ніч 20.09: продаж вийшов з «продавати не було чого» й лишив
    рюкзак із «Лавкою» на екрані. Гра після цього не приймає клавіші — бот не пив
    банку й не кликав пета, персонаж загинув 40 разів поспіль.
    """
    from app.pipelines.actions import PressKey
    from app.pipelines.dialog_guard import DialogGuardConfig, DialogGuardPipeline
    from app.pipelines.shared import set_busy

    pipe = DialogGuardPipeline(DialogGuardConfig(confirm_frames=2, window_check_every=1.0))
    frame, shared = live("frame_1440_shop_open.png"), {}

    assert pipe.process(guard_ctx(frame, 0.0, shared)).actions == [], "перший раз лише помічаємо"
    res = pipe.process(guard_ctx(frame, 2.0, shared))
    assert [a.key for a in res.actions if isinstance(a, PressKey)] == ["esc"]
    assert any("не приймає клавіші" in e for e in res.events)

    set_busy(shared, "sell", True)                 # поки продаємо — вікна доречні
    pipe.window_seen = 5
    assert pipe.process(guard_ctx(frame, 10.0, shared)).actions == []


def test_guard_ignores_a_clean_frame():
    from app.pipelines.dialog_guard import DialogGuardConfig, DialogGuardPipeline

    pipe = DialogGuardPipeline(DialogGuardConfig(confirm_frames=1, window_check_every=0.0))
    clean = live("frame_1440_guard_false_alarm.png")       # гра без жодного вікна
    for ts in (0.0, 1.0, 2.0):
        res = pipe.process(guard_ctx(clean, ts, {}))
        assert res.actions == [], "на чистому кадрі нічого не тиснемо"
