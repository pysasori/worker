"""
Повернення на місце: кнопка під мінімапою -> «Список» -> подвійний клік по «фарм».

Кадри справжні: на одному «Список» відкритий (точки «фарм» і «test»), на іншому закритий.
"""
from __future__ import annotations

import pytest
from PIL import Image

from app.pipelines.actions import ClickAt
from app.pipelines.base import Frame, PipelineContext
from app.pipelines.return_home import ReturnHomeConfig, ReturnHomePipeline, ReturnState
from app.pipelines.shared import (
    SHARED_HOME, SHARED_POS, Position, TargetInfo, busy_reasons, combat_ready, set_busy,
)
from app.vision.schemas import BarReading
from tests.conftest import FIXTURES

HOME = Position(x=241, y=563, known=True)
FAR = Position(x=226, y=549, known=True)          # 21 одиниця від дому


@pytest.fixture(scope="module")
def list_open() -> Image.Image:
    return Image.open(FIXTURES / "frame_1440_nav_list_open.png").convert("RGB")


@pytest.fixture(scope="module")
def list_closed() -> Image.Image:
    return Image.open(FIXTURES / "frame_1440_nav_closed.png").convert("RGB")


def cfg(**kw) -> ReturnHomeConfig:
    return ReturnHomeConfig(**{"stuck_after": 4.0, "cooldown": 20.0, **kw})


def step(pipe, image, ts, shared, pos=None):
    if pos is not None:
        shared[SHARED_POS] = pos.model_copy(update={"at": ts})   # щойно прочитано
    shared.setdefault(SHARED_HOME, HOME)
    return pipe.process(PipelineContext(window="t", frame=Frame(image=image, ts=ts), shared=shared))


def clicks(res) -> list[ClickAt]:
    return [a for a in res.actions if isinstance(a, ClickAt)]


# ---- коли йти -----------------------------------------------------------------
def test_stays_when_close_to_home(list_closed):
    pipe, shared = ReturnHomePipeline(cfg()), {}
    res = step(pipe, list_closed, 0, shared, Position(x=236, y=560, known=True))
    assert res.actions == [] and pipe.state is ReturnState.IDLE
    assert combat_ready(shared)


def test_does_nothing_without_coordinates(list_closed):
    pipe, shared = ReturnHomePipeline(cfg()), {}
    res = step(pipe, list_closed, 0, shared, Position())
    assert res.actions == []
    assert not combat_ready(shared), "до підтвердження координат бій не починаємо"


def test_startup_returns_before_touching_an_accidental_target(list_closed):
    pipe, shared = ReturnHomePipeline(cfg()), {}
    shared["target"] = TargetInfo(present=True, bar=BarReading(present=True, filled=208, total=208))
    res = step(pipe, list_closed, 0, shared, FAR)
    assert clicks(res), "на старті не б'ємо випадкового моба, а одразу повертаємось"
    assert not combat_ready(shared)


def test_finishes_an_existing_fight_after_start_check(list_closed):
    pipe, shared = ReturnHomePipeline(cfg()), {}
    step(pipe, list_closed, 0, shared, HOME)  # стартову позицію перевірено, бій дозволено
    shared["target"] = TargetInfo(present=True, bar=BarReading(present=True, filled=208, total=208))
    res = step(pipe, list_closed, 1, shared, FAR)
    assert res.actions == []
    assert "добиваю ціль" in res.status
    assert combat_ready(shared)


def test_waits_for_loot(list_closed):
    pipe, shared = ReturnHomePipeline(cfg()), {}
    set_busy(shared, "loot", True)
    res = step(pipe, list_closed, 0, shared, FAR)
    assert res.actions == [] and "чекаю: loot" in res.status


# ---- як іти -------------------------------------------------------------------
def test_opens_the_list_with_the_minimap_button(list_closed):
    pipe, shared = ReturnHomePipeline(cfg()), {}
    res = step(pipe, list_closed, 0, shared, FAR)
    (click,) = clicks(res)
    assert (click.x, click.y) == (1376, 181), "кнопка з хрестиком під мінімапою"
    assert click.hover_delay > 0, "кнопки PW не реагують на клік без наведення"
    assert pipe.state is ReturnState.OPENING
    assert busy_reasons(shared) == {"return_home"}, "поки йдемо — не б'ємось"


def test_double_clicks_the_farm_point(list_open):
    pipe, shared = ReturnHomePipeline(cfg()), {}
    step(pipe, list_open, 0, shared, FAR)                     # «Список» уже відкритий
    res = step(pipe, list_open, 0.2, shared)
    (click,) = clicks(res)
    assert click.double, "у «Списку» біг запускає саме подвійний клік"
    assert abs(click.x - 263) <= 25 and abs(click.y - 529) <= 4, "рядок «фарм», а не «test»"
    assert pipe.state is ReturnState.RUNNING


def test_point_name_is_configurable(list_open):
    pipe, shared = ReturnHomePipeline(cfg(point_name="тест")), {}
    step(pipe, list_open, 0, shared, FAR)
    (click,) = clicks(step(pipe, list_open, 0.2, shared))
    assert abs(click.y - 544) <= 4, "другий рядок — «test»"


def test_missing_point_is_reported_and_list_closed(list_open):
    pipe, shared = ReturnHomePipeline(cfg(point_name="нема такої точки")), {}
    step(pipe, list_open, 0, shared, FAR)
    res = step(pipe, list_open, 0.2, shared)
    assert any("нема точки" in e for e in res.events)
    assert pipe.state is ReturnState.CLOSING, "вікно відкритим не лишаємо"


def test_list_that_never_opens_is_a_failure(list_closed):
    pipe, shared = ReturnHomePipeline(cfg()), {}
    step(pipe, list_closed, 0, shared, FAR)
    res = step(pipe, list_closed, 10, shared)
    assert any("не відкрився" in e for e in res.events)
    assert busy_reasons(shared) == set(), "бот знову вільний"
    assert step(pipe, list_closed, 15, shared).actions == [], "пауза після невдачі"


# ---- дорога ---------------------------------------------------------------------
def start_running(pipe, list_open, shared):
    step(pipe, list_open, 0, shared, FAR)
    step(pipe, list_open, 0.2, shared)
    assert pipe.state is ReturnState.RUNNING


def test_arrives_closes_the_list_and_goes_back_to_work(list_open, list_closed):
    pipe, shared = ReturnHomePipeline(cfg()), {}
    start_running(pipe, list_open, shared)
    step(pipe, list_open, 5, shared, Position(x=233, y=556, known=True))
    res = step(pipe, list_open, 10, shared, Position(x=240, y=562, known=True))
    assert any("повернувся на місце" in e for e in res.events)
    res = step(pipe, list_open, 10.2, shared)
    (click,) = clicks(res)
    assert (click.x, click.y) == (373, 475), "хрестик «Списку»"
    step(pipe, list_closed, 11, shared, Position(x=240, y=562, known=True))   # «Список» зник
    step(pipe, list_closed, 11.5, shared, Position(x=240, y=562, known=True))
    res = step(pipe, list_closed, 12, shared, Position(x=241, y=563, known=True))
    assert pipe.state is ReturnState.IDLE, "координати звірені — можна в бій"
    assert any("в бій" in e for e in res.events)
    assert busy_reasons(shared) == set()


def test_clicks_again_when_standing(list_open):
    pipe, shared = ReturnHomePipeline(cfg()), {}
    start_running(pipe, list_open, shared)
    step(pipe, list_open, 2, shared, FAR)
    res = step(pipe, list_open, 5, shared, FAR)               # 5 с на тому ж місці
    assert pipe.state is ReturnState.PICKING
    assert any("пробую ще раз" in e for e in res.events)


def test_gives_up_after_retries(list_open):
    pipe, shared = ReturnHomePipeline(cfg(retries=2)), {}
    start_running(pipe, list_open, shared)
    t, events, doubles = 0.2, [], 1
    for _ in range(4):
        t += 5
        events += step(pipe, list_open, t, shared, FAR).events      # стоїть
        t += 0.2
        res = step(pipe, list_open, t, shared)
        events += res.events
        doubles += sum(1 for c in clicks(res) if c.double)
    assert doubles == 2, "клікаємо рівно стільки разів, скільки дозволено"
    assert any("застряг" in e for e in events)
    assert pipe.state is not ReturnState.RUNNING


def test_learns_where_the_point_really_is(list_open):
    """Гра зупинила персонажа за 6 одиниць від «дому» — отже, точка «фарм» саме там."""
    pipe, shared = ReturnHomePipeline(cfg()), {}
    start_running(pipe, list_open, shared)
    step(pipe, list_open, 2, shared, Position(x=230, y=553, known=True))
    step(pipe, list_open, 3, shared, Position(x=236, y=559, known=True))
    res = step(pipe, list_open, 8, shared, Position(x=236, y=559, known=True))
    assert any("запам'ятав" in e for e in res.events)
    assert (pipe.learned_home.x, pipe.learned_home.y) == (236, 559)


# ---- приземлення ---------------------------------------------------------------
def test_lands_when_autopath_left_the_character_in_the_air(list_closed):
    """
    Живий випадок: автопуть привіз персонажа на фарм верхи на скаті, на висоті 54.
    У повітрі гра відповідає «Здесь невозможно призвать питомца», і бот три спроби
    поспіль кликав пета в порожнечу.
    """
    from app.pipelines.actions import PressKey
    from app.pipelines.shared import SHARED_ALT, Altitude, set_mounted

    pipe, shared = ReturnHomePipeline(cfg(land_wait=5.0)), {SHARED_HOME: HOME}
    shared[SHARED_ALT] = Altitude(z=22, known=True)
    assert step(pipe, list_closed, 0.0, shared, HOME).actions == [], "на землі нічого не тиснемо"
    set_mounted(shared, True)                       # автопуть привіз нас верхи
    shared[SHARED_ALT] = Altitude(z=54, known=True)
    res = step(pipe, list_closed, 1.0, shared, HOME)
    assert [a.key for a in res.actions if isinstance(a, PressKey)] == ["9"]
    assert any("сідаю" in e for e in res.events)
    assert step(pipe, list_closed, 3.0, shared, HOME).actions == [], "чекаємо, поки впаде"
    shared[SHARED_ALT] = Altitude(z=23, known=True)
    assert step(pipe, list_closed, 9.0, shared, HOME).actions == [], "сів — більше не тиснемо"


def test_gives_up_landing_and_says_so(list_closed):
    from app.pipelines.shared import SHARED_ALT, Altitude

    from app.pipelines.shared import set_mounted

    pipe, shared = ReturnHomePipeline(cfg(land_wait=1.0)), {SHARED_HOME: HOME}
    shared[SHARED_ALT] = Altitude(z=20, known=True)
    step(pipe, list_closed, 0.0, shared, HOME)
    set_mounted(shared, True)
    shared[SHARED_ALT] = Altitude(z=60, known=True)
    events = []
    for i in range(1, 12):
        events += step(pipe, list_closed, float(i) * 2, shared, HOME).events
    assert any("не саджає персонажа" in e for e in events)


# ---- висота фарму --------------------------------------------------------------
def alt_frames():
    """Кадри з вікном «Автопуть»: повзунок на 22 і на 75."""
    return (Image.open(FIXTURES / "frame_1440_autopath_22.png").convert("RGB"),
            Image.open(FIXTURES / "frame_1440_autopath_75.png").convert("RGB"))


def test_flies_up_to_the_farm_altitude(list_open, list_closed):
    """Висота фарму задана — бот злітає і летить автопуттю на неї, а не сідає."""
    from app.pipelines.actions import DragTo, PressKey
    from app.pipelines.shared import SHARED_ALT, Altitude

    low, high = alt_frames()
    pipe, shared = ReturnHomePipeline(cfg(farm_altitude=75, takeoff_delay=2.0)), {SHARED_HOME: HOME}
    shared[SHARED_ALT] = Altitude(z=22, known=True)
    res = step(pipe, list_closed, 0.0, shared, HOME)
    assert any("висота" in e for e in res.events), "сама висота — теж привід рушити"
    step(pipe, list_open, 1.0, shared, HOME)                  # «Список» відкрився
    res = step(pipe, list_open, 1.5, shared, HOME)
    assert [a.key for a in res.actions if isinstance(a, PressKey)] == ["9"], "спершу злітаємо"
    step(pipe, list_open, 2.5, shared, HOME)
    shared[SHARED_ALT] = Altitude(z=30, known=True)
    step(pipe, list_open, 4.5, shared, HOME)                  # злетіли
    res = step(pipe, list_open, 5.0, shared, HOME)
    assert any(isinstance(a, ClickAt) and a.double for a in res.actions), "клік по точці"
    res = step(pipe, low, 6.5, shared, HOME)
    (drag,) = [a for a in res.actions if isinstance(a, DragTo)]
    assert abs(drag.x2 - 776) <= 2, "повзунок на 75"
    step(pipe, high, 8.0, shared, HOME)  # повзунок на місці
    shared[SHARED_ALT] = Altitude(z=74, known=True)
    res = step(pipe, high, 10.0, shared, HOME)
    assert pipe.state is ReturnState.CLOSING
    assert any("став на висоту" in e for e in res.events)


def test_with_farm_altitude_the_bot_does_not_land(list_closed):
    from app.pipelines.actions import PressKey
    from app.pipelines.shared import SHARED_ALT, Altitude

    pipe, shared = ReturnHomePipeline(cfg(farm_altitude=70)), {SHARED_HOME: HOME}
    shared[SHARED_ALT] = Altitude(z=68, known=True)
    res = step(pipe, list_closed, 0.0, shared, HOME)
    assert [a for a in res.actions if isinstance(a, PressKey)] == [], "на своїй висоті не чіпаємо"


# ---- політ за відстанню ---------------------------------------------------------
def test_long_way_home_is_flown_not_walked(list_open, list_closed):
    """Далеко — сідаємо на звіра і летимо: по землі дорога довга і є за що зачепитись."""
    from app.pipelines.actions import PressKey
    from app.pipelines.shared import SHARED_ALT, Altitude

    far = Position(x=241, y=463, known=True)                  # 100 одиниць від дому
    pipe, shared = ReturnHomePipeline(cfg(fly=True, fly_beyond=50, fly_height=60)), {SHARED_HOME: HOME}
    shared[SHARED_ALT] = Altitude(z=22, known=True)
    step(pipe, list_closed, 0, shared, far)
    step(pipe, list_open, 1, shared, far)
    res = step(pipe, list_open, 1.5, shared, far)
    assert [a.key for a in res.actions if isinstance(a, PressKey)] == ["9"], "злітаємо"


def test_short_way_home_is_walked(list_open, list_closed):
    from app.pipelines.actions import PressKey
    from app.pipelines.shared import SHARED_ALT, Altitude

    pipe, shared = ReturnHomePipeline(cfg(fly=True, fly_beyond=50)), {SHARED_HOME: HOME}
    shared[SHARED_ALT] = Altitude(z=22, known=True)
    step(pipe, list_closed, 0, shared, FAR)                   # 21 одиниця — близько
    step(pipe, list_open, 1, shared, FAR)
    res = step(pipe, list_open, 1.5, shared, FAR)
    assert [a for a in res.actions if isinstance(a, PressKey)] == [], "поруч — просто біжимо"
    assert any(isinstance(a, ClickAt) and a.double for a in res.actions)


def test_wrong_place_after_the_trip_starts_another_try(list_open, list_closed):
    """Гра могла зупинити персонажа не там: звіряємо координати й ідемо ще раз."""
    pipe, shared = ReturnHomePipeline(cfg()), {}
    start_running(pipe, list_open, shared)
    step(pipe, list_open, 5, shared, Position(x=240, y=562, known=True))   # «дійшов»
    step(pipe, list_open, 5.2, shared)                                     # закриваємо «Список»
    step(pipe, list_closed, 6, shared, Position(x=300, y=600, known=True))
    res = step(pipe, list_closed, 7, shared, Position(x=300, y=600, known=True))
    assert any("пробую ще раз" in e for e in res.events)


def test_lands_right_after_a_flight_home(list_open, list_closed):
    """
    Живий випадок: бот прилетів на спот і завис на висоті 33 при землі 22 — різниці
    не вистачило до порога, і він простояв у повітрі чотири години.
    """
    from app.pipelines.actions import PressKey
    from app.pipelines.shared import SHARED_ALT, Altitude

    far = Position(x=241, y=463, known=True)                  # 100 одиниць від дому
    pipe, shared = ReturnHomePipeline(cfg(fly=True, fly_beyond=50)), {SHARED_HOME: HOME}
    shared[SHARED_ALT] = Altitude(z=22, known=True)
    for ts, img, pos in ((0, list_closed, far), (1, list_open, far), (1.5, list_open, far),
                         (4, list_open, far), (4.5, list_open, far)):
        step(pipe, img, ts, shared, pos)
    shared[SHARED_ALT] = Altitude(z=55, known=True)           # летимо
    step(pipe, list_open, 9, shared, far)                     # клік по точці
    step(pipe, list_open, 15, shared, far)                    # вікна «Автопуть» нема — летимо як є
    step(pipe, list_open, 16, shared, HOME)                   # прилетіли
    step(pipe, list_open, 16.5, shared, HOME)                 # закриваємо «Список»
    step(pipe, list_closed, 17, shared, HOME)                 # «Список» зник — звіряємо
    step(pipe, list_closed, 17.5, shared, HOME)
    res = step(pipe, list_closed, 18, shared, HOME)
    assert [a.key for a in res.actions if isinstance(a, PressKey)] == ["9"], "сідаємо одразу"
    assert any("сідаю і в бій" in e for e in res.events)


def test_stuck_on_a_tree_is_walked_down_through_the_list(list_closed, list_open):
    """
    Живий випадок: автопуть посадив персонажа на дерево — координати домашні, а висота
    на 11 вища. Клавіша польоту не допомагає (ми не верхи), тому веде гра через «Список».
    """
    from app.pipelines.shared import SHARED_ALT, Altitude

    from app.pipelines.shared import set_mounted

    pipe, shared = ReturnHomePipeline(cfg(land_wait=0.5)), {SHARED_HOME: HOME}
    shared[SHARED_ALT] = Altitude(z=22, known=True)
    step(pipe, list_closed, 0, shared, HOME)                  # земля = 22
    set_mounted(shared, True)
    shared[SHARED_ALT] = Altitude(z=33, known=True)
    events = []
    for i in range(1, 8):
        events += step(pipe, list_closed, float(i), shared, HOME).events
    assert any("веду через «Список»" in e for e in events)
    assert pipe.state in (ReturnState.OPENING, ReturnState.PICKING)


def test_no_targets_for_long_walks_the_character_anew(list_closed):
    """Цілей нема кілька хвилин — найчастіше персонаж стоїть там, звідки їх не дістати."""
    from app.pipelines.shared import TargetInfo, write_target
    from app.vision.schemas import BarReading

    pipe, shared = ReturnHomePipeline(cfg(idle_walk_after=60.0)), {SHARED_HOME: HOME}
    events = []
    for ts in (0.0, 30.0, 59.0):
        write_target(shared, TargetInfo(present=False, bar=BarReading(), updated_at=ts))
        events += step(pipe, list_closed, ts, shared, HOME).events
    assert not events, "хвилина без цілей — ще не привід"
    write_target(shared, TargetInfo(present=False, bar=BarReading(), updated_at=61.0))
    res = step(pipe, list_closed, 61.0, shared, HOME)
    assert any("без цілей" in e for e in res.events)
    assert pipe.state in (ReturnState.OPENING, ReturnState.PICKING)


def test_the_flight_home_does_not_count_as_idle_time(list_closed):
    """Дорога після смерті триває хвилини — цей час не має рахуватись як «нема цілей»."""
    from app.pipelines.shared import TargetInfo, set_busy, write_target
    from app.vision.schemas import BarReading

    pipe, shared = ReturnHomePipeline(cfg(idle_walk_after=60.0)), {SHARED_HOME: HOME}
    set_busy(shared, "death_return", True)
    for ts in (0.0, 30.0, 90.0, 150.0):
        write_target(shared, TargetInfo(present=False, bar=BarReading(), updated_at=ts))
        assert step(pipe, list_closed, ts, shared, HOME).events == [], "у дорозі мовчимо"
    set_busy(shared, "death_return", False)
    write_target(shared, TargetInfo(present=False, bar=BarReading(), updated_at=160.0))
    assert step(pipe, list_closed, 160.0, shared, HOME).events == [], "відлік починається заново"


def test_a_zero_altitude_never_becomes_the_ground(list_closed):
    """Живий випадок: висота прочиталась як 0, і бот вирішив, що земля — нуль."""
    from app.pipelines.shared import SHARED_ALT, Altitude

    pipe, shared = ReturnHomePipeline(cfg()), {SHARED_HOME: HOME}
    shared[SHARED_ALT] = Altitude(z=22, known=True)
    step(pipe, list_closed, 0, shared, HOME)
    shared[SHARED_ALT] = Altitude(z=0, known=True)
    step(pipe, list_closed, 1, shared, HOME)
    assert pipe.ground == 22, "нуль за землю не беремо"
    shared[SHARED_ALT] = Altitude(z=23, known=True)
    assert step(pipe, list_closed, 2, shared, HOME).actions == [], "23 при землі 22 — не політ"


# ---- звідки бот знає, де земля ----------------------------------------------------
def test_bad_low_reading_far_from_home_is_not_taken_for_ground(list_closed):
    """
    Живий випадок: після смерті в місті бот запам'ятав «землю 2», а на споті земля 22 —
    і далі щохвилини «сідав» на рівному місці, тиснув клавішу польоту (тобто злітав)
    і їхав автопуттю. За ніч таких польотів без причини було 1240.
    """
    from app.pipelines.shared import SHARED_ALT, Altitude

    city = Position(x=341, y=663, known=True)      # 141 одиниця від дому
    pipe, shared = ReturnHomePipeline(cfg(land_key="9")), {SHARED_HOME: HOME}
    shared[SHARED_ALT] = Altitude(z=8, known=True)
    step(pipe, list_closed, 0.0, shared, city)
    assert pipe.ground is None, "чужа земля не рахується"

    shared[SHARED_ALT] = Altitude(z=22, known=True)
    pipe.state = ReturnState.IDLE                  # дорога позаду, ми вдома
    step(pipe, list_closed, 1.0, shared, HOME)
    assert pipe.ground == 22


def test_one_bad_reading_does_not_become_the_ground(list_closed):
    """
    Через це бот і літав: у висоті «22» інколи губиться цифра й читається «6».
    Мінімум за сесію брав те «6» за землю назавжди — і далі бот раз у раз «сідав»
    на рівному місці, тобто сідав на звіра. Медіана читань такого не помічає.
    """
    from app.pipelines.shared import SHARED_ALT, Altitude

    pipe, shared = ReturnHomePipeline(cfg(land_key="9")), {SHARED_HOME: HOME}
    ts = 0.0
    for z in [22, 23, 22, 22, 6, 23, 22, 22, 23]:      # одне число зіпсуте
        shared[SHARED_ALT] = Altitude(z=z, known=True)
        step(pipe, list_closed, ts, shared, HOME)
        ts += 1.0
    assert pipe.ground == 22, f"земля має лишитись 22, а не {pipe.ground}"

    quiet = step(pipe, list_closed, ts, shared, HOME)
    assert quiet.actions == [], "нікуди не летимо й нічого не тиснемо"


def test_ground_is_learned_only_on_the_farm_spot(list_closed):
    """Біля води земля справді інша — але то не наша земля, і в пам'ять вона не йде."""
    from app.pipelines.shared import SHARED_ALT, Altitude

    beach = Position(x=241, y=583, known=True)        # 20 одиниць від дому
    pipe, shared = ReturnHomePipeline(cfg(land_key="9")), {SHARED_HOME: HOME}
    for i, z in enumerate([22, 22, 23]):
        shared[SHARED_ALT] = Altitude(z=z, known=True)
        step(pipe, list_closed, float(i), shared, HOME)
    was = pipe.ground
    pipe.state = ReturnState.IDLE
    shared[SHARED_ALT] = Altitude(z=6, known=True)
    step(pipe, list_closed, 10.0, shared, beach)
    assert pipe.ground == was == 22


def test_never_mounts_while_trying_to_land(list_closed):
    """
    Клавіша польоту — перемикач: на землі вона не саджає, а САДЖАЄ НА ЗВІРА.
    Коли бот точно знає, що персонаж не верхи, тиснути її не можна — інакше він
    піде фармити верхи, без пета й з перерваними вміннями (20.09 так і сталось).
    """
    from app.pipelines.actions import PressKey
    from app.pipelines.shared import SHARED_ALT, Altitude, set_mounted

    pipe, shared = ReturnHomePipeline(cfg(land_key="9", land_wait=0.0)), {SHARED_HOME: HOME}
    set_mounted(shared, False)
    shared[SHARED_ALT] = Altitude(z=6, known=True)
    step(pipe, list_closed, 0.0, shared, HOME)          # зіпсуте читання стало «землею»
    shared[SHARED_ALT] = Altitude(z=22, known=True)

    res = step(pipe, list_closed, 1.0, shared, HOME)
    assert [a for a in res.actions if isinstance(a, PressKey)] == [], "на своїх двох 9 не тиснемо"


def test_flight_trip_remembers_that_we_are_mounted(list_open, list_closed):
    from app.pipelines.shared import SHARED_ALT, Altitude, is_mounted

    far = Position(x=241, y=463, known=True)
    pipe, shared = ReturnHomePipeline(cfg(fly=True, fly_beyond=50, fly_height=60)), {SHARED_HOME: HOME}
    shared[SHARED_ALT] = Altitude(z=22, known=True)
    step(pipe, list_closed, 0, shared, far)
    step(pipe, list_open, 1, shared, far)
    step(pipe, list_open, 1.5, shared, far)
    assert is_mounted(shared) is True, "злетіли — отже верхи"


# ---- дім не знайшовся: не стоїмо вічно ---------------------------------------------
def test_fights_anyway_after_repeated_failures_and_keeps_home():
    blank = Image.new("RGB", (1440, 1080))               # ні кнопки, ні «Списку»: повернення не вдається
    pipe, shared = ReturnHomePipeline(cfg(cooldown=50.0, fight_after_fails=2)), {}
    near = Position(x=241 + 25, y=563, known=True)       # 25 від «дому»
    step(pipe, blank, 0, shared, near)                    # невдача №1
    assert not combat_ready(shared)
    pipe.retry_at = 0
    step(pipe, blank, 1, shared, near)                    # невдача №2
    res = step(pipe, blank, 2, shared, near)              # пауза до нової спроби
    assert combat_ready(shared), "після двох невдач бот фармить, а не стоїть"
    assert "фармлю де є" in res.status
    assert pipe.learned_home is None, "дім НЕ переносимо"


def test_keeps_standing_before_enough_failures():
    blank = Image.new("RGB", (1440, 1080))
    pipe, shared = ReturnHomePipeline(cfg(cooldown=50.0, fight_after_fails=3)), {}
    near = Position(x=241 + 25, y=563, known=True)
    step(pipe, blank, 0, shared, near)
    step(pipe, blank, 1, shared, near)
    assert not combat_ready(shared)
