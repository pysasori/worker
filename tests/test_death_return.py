"""Повернення після смерті: воскреснути в місті, злетіти, долетіти до фарму, сісти."""
from __future__ import annotations

from PIL import Image

from app.pipelines.actions import ClickAt, PressKey
from app.pipelines.base import Frame, PipelineContext
from app.pipelines.death_return import DeathReturnConfig, DeathReturnPipeline, DeathState
from app.pipelines.shared import SHARED_HOME, SHARED_POS, Position, busy_reasons
from tests.conftest import FIXTURES

HOME = Position(x=460, y=680, known=True)
CITY = Position(x=520, y=900, known=True)

DEAD = "frame_1440_death.png"
LIST_OPEN = "frame_1440_nav_list_open.png"
LIST_CLOSED = "frame_1440_nav_closed.png"
ALIVE = "frame_1440_target_and_pet.png"
_CACHE: dict[str, Image.Image] = {}


def live(name: str) -> Image.Image:
    if name not in _CACHE:
        _CACHE[name] = Image.open(FIXTURES / name).convert("RGB")
    return _CACHE[name]


def cfg(**kw) -> DeathReturnConfig:
    return DeathReturnConfig(**{"after_respawn": 5.0, "takeoff_delay": 2.0, "check_every": 0,
                                "stuck_after": 10.0, "flight_height": 0, "land_wait": 3.0, **kw})


def step(pipe, name, ts, shared, pos=CITY):
    shared[SHARED_POS] = pos.model_copy(update={"at": ts}) if pos.known else pos   # щойно прочитано
    shared.setdefault(SHARED_HOME, HOME)
    return pipe.process(PipelineContext(window="t", frame=Frame(image=live(name), ts=ts), shared=shared))


def keys(res):
    return [a.key for a in res.actions if isinstance(a, PressKey)]


def clicks(res):
    return [a for a in res.actions if isinstance(a, ClickAt)]


def die(pipe, shared):
    res = None
    for i in range(pipe.config.confirm_frames):
        res = step(pipe, DEAD, i * 0.2, shared)
    return res


def test_alive_character_is_left_alone():
    pipe, shared = DeathReturnPipeline(cfg()), {}
    for i in range(5):
        assert step(pipe, ALIVE, i * 0.2, shared).actions == []
    assert busy_reasons(shared) == set()


def test_respawns_in_the_nearest_city():
    pipe, shared = DeathReturnPipeline(cfg()), {}
    res = die(pipe, shared)
    (click,) = clicks(res)
    assert (click.x, click.y) == (717, 536), "кнопка «Ближний город»"
    assert busy_reasons(shared) == {"death_return"}, "мертвим бій і лут мовчать"
    assert any("загинув" in e for e in res.events)


def test_blinking_death_text_does_not_freeze_the_bot():
    pipe, shared = DeathReturnPipeline(cfg()), {}
    step(pipe, DEAD, 0.0, shared)
    assert busy_reasons(shared) == {"death_return"}
    step(pipe, ALIVE, 0.2, shared)
    assert busy_reasons(shared) == set(), "вікно зникло — бот знову вільний"


def test_alive_character_clears_stale_death_busy_after_pipeline_reset():
    """Reconnect скидає pipeline, але старий busy не має заморожувати живого персонажа."""
    from app.pipelines.shared import set_busy

    pipe, shared = DeathReturnPipeline(cfg()), {}
    set_busy(shared, "death_return", True)
    pipe.reset()
    step(pipe, ALIVE, 0.0, shared)
    assert busy_reasons(shared) == set()


def reach_travel(pipe, shared, flight=True, expect=DeathState.TRAVEL):
    die(pipe, shared)
    step(pipe, LIST_CLOSED, 1.0, shared)            # вікно смерті зникло
    res = step(pipe, LIST_CLOSED, 7.0, shared)      # місто завантажилось
    if flight:
        assert keys(res) == ["9"], "злітаємо"
        step(pipe, LIST_CLOSED, 10.0, shared)       # пауза після зльоту
    res = step(pipe, LIST_CLOSED, 10.5, shared)     # клік по кнопці «Список»
    assert clicks(res), "відкриваємо «Список»"
    res = step(pipe, LIST_OPEN, 11.5, shared)
    (click,) = clicks(res)
    assert click.double, "подвійний клік по точці фарму"
    assert pipe.state is expect


def test_flies_home_and_lands():
    pipe, shared = DeathReturnPipeline(cfg()), {}
    reach_travel(pipe, shared)
    step(pipe, LIST_OPEN, 15.0, shared, Position(x=490, y=800, known=True))
    res = step(pipe, LIST_OPEN, 20.0, shared, Position(x=462, y=682, known=True))
    assert keys(res) == ["9"], "прилетіли — сідаємо тією ж клавішею"
    step(pipe, LIST_OPEN, 23.0, shared, HOME)
    res = step(pipe, LIST_OPEN, 23.5, shared, HOME)
    assert clicks(res), "закриваємо «Список»"
    step(pipe, LIST_CLOSED, 25.0, shared, HOME)           # «Список» зник, звіряємо координати
    step(pipe, LIST_CLOSED, 26.0, shared, HOME)
    res = step(pipe, LIST_CLOSED, 27.0, shared, HOME)
    assert pipe.state is DeathState.IDLE and busy_reasons(shared) == set()
    assert any("повернувся після смерті" in e for e in res.events)


def test_without_flight_just_runs():
    """Галочка «політ» не стоїть — тупо тикаємо на точку фарму."""
    pipe, shared = DeathReturnPipeline(cfg(use_flight=False)), {}
    reach_travel(pipe, shared, flight=False)
    res = step(pipe, LIST_OPEN, 15.0, shared, HOME)
    assert keys(res) == [], "без польоту сідати не треба"


def test_clicks_again_when_stuck():
    pipe, shared = DeathReturnPipeline(cfg()), {}
    reach_travel(pipe, shared)
    step(pipe, LIST_OPEN, 12.0, shared, CITY)
    res = step(pipe, LIST_OPEN, 25.0, shared, CITY)
    assert pipe.state is DeathState.OPEN_LIST
    assert any("пробую ще раз" in e for e in res.events)


def test_unknown_home_gives_control_back():
    pipe, shared = DeathReturnPipeline(cfg()), {SHARED_HOME: None}
    die(pipe, shared)
    step(pipe, LIST_CLOSED, 1.0, shared)
    res = step(pipe, LIST_CLOSED, 7.0, shared)
    assert any("невідоме" in e for e in res.events)
    assert busy_reasons(shared) == set()


def test_name_label_over_the_text_does_not_hide_death():
    """
    Над вікном смерті висять «Княжество» та ім'я персонажа. Коли вони лягли на текст,
    збіг упав до 0.67, і бот дві години не помічав, що персонаж мертвий.
    """
    from PIL import ImageDraw

    covered = live(DEAD).copy()
    ImageDraw.Draw(covered).rectangle((640, 482, 710, 498), fill=(20, 20, 30))   # підпис на тексті
    _CACHE["covered"] = covered
    pipe, shared = DeathReturnPipeline(cfg()), {}
    res = None
    for i in range(pipe.config.confirm_frames):
        res = step(pipe, "covered", i * 0.2, shared)
    assert clicks(res), "кнопки видно — отже, мертвий, воскресаємо"


def test_saves_a_snapshot_when_hp_is_zero_without_death_window(tmp_path):
    from app.pipelines.shared import SHARED_PLAYER
    from app.vision.schemas import BarReading

    pipe, shared = DeathReturnPipeline(cfg(snapshot_dir=str(tmp_path))), {}
    shared[SHARED_PLAYER] = BarReading(present=False)
    events = []
    for i in range(12):
        events += step(pipe, ALIVE, i * 1.0, shared).events
    assert any("HP персонажа на нулі" in e for e in events)
    assert list(tmp_path.glob("*.png")), "знімок збережено"


def test_stale_farm_coordinates_after_death_do_not_count_as_arrival():
    """
    Живий випадок: після смерті координати застигли на місці фарму (стрибок у місто
    відкидався як помилка читання), і бот «долетів» за секунду, не злетівши з міста.
    """
    pipe, shared = DeathReturnPipeline(cfg()), {}
    die(pipe, shared)
    stale = HOME.model_copy(update={"at": 0.0})       # прочитано ще до смерті
    for ts in (1.0, 7.0, 30.0):
        shared[SHARED_POS] = stale
        shared.setdefault(SHARED_HOME, HOME)
        pipe.process(PipelineContext(window="t", frame=Frame(image=live(LIST_CLOSED), ts=ts), shared=shared))
    assert pipe.state is DeathState.LOADING, "поки координати не прочитані в місті — не злітаємо"


def test_position_read_before_the_click_is_not_arrival():
    pipe, shared = DeathReturnPipeline(cfg()), {}
    reach_travel(pipe, shared)
    shared[SHARED_POS] = HOME.model_copy(update={"at": 1.0})
    res = pipe.process(PipelineContext(window="t", frame=Frame(image=live(LIST_OPEN), ts=12.0), shared=shared))
    assert keys(res) == [] and pipe.state is DeathState.TRAVEL


# ---- висота автопуті ------------------------------------------------------------
AUTOPATH_LOW = "frame_1440_autopath_22.png"     # щойно після кліку: висота землі
AUTOPATH_HIGH = "frame_1440_autopath_75.png"


def test_reads_the_autopath_height_slider():
    from app.vision.autopath import AutopathConfig, read_autopath

    low = read_autopath(live(AUTOPATH_LOW), AutopathConfig())
    high = read_autopath(live(AUTOPATH_HIGH), AutopathConfig())
    assert (low.handle_x, low.height) == (675, 22)
    assert (high.handle_x, high.height) == (776, 75)
    assert read_autopath(live(LIST_CLOSED), AutopathConfig()) is None


def test_raises_the_autopath_height_before_flying():
    """
    Живий випадок: після воскресіння автопуть летів на висоті землі (22) і впирався
    в стіну міста. Повзунок «Высота» тягнеться з фону — на 75 персонаж перелетів місто.
    """
    from app.pipelines.actions import DragTo

    pipe, shared = DeathReturnPipeline(cfg(flight_height=75)), {}
    reach_travel(pipe, shared, expect=DeathState.SET_HEIGHT)
    res = step(pipe, AUTOPATH_LOW, 13.0, shared)
    (drag,) = [a for a in res.actions if isinstance(a, DragTo)]
    assert (drag.x1, drag.y1) == (675, 732)
    assert abs(drag.x2 - 776) <= 2, "повзунок туди, де висота 75"
    res = step(pipe, AUTOPATH_HIGH, 14.5, shared)
    assert pipe.state is DeathState.TRAVEL
    assert any("висота автопуті 75" in e for e in res.events)


def test_flies_as_is_when_autopath_window_never_shows():
    pipe, shared = DeathReturnPipeline(cfg(flight_height=75)), {}
    reach_travel(pipe, shared, expect=DeathState.SET_HEIGHT)
    step(pipe, LIST_OPEN, 13.0, shared)
    res = step(pipe, LIST_OPEN, 17.5, shared)
    assert pipe.state is DeathState.TRAVEL
    assert any("Автопуть" in e for e in res.events)


def test_not_at_the_farm_after_the_flight_means_another_try():
    """Долетів не туди — координати це показують, і бот клікає точку ще раз."""
    pipe, shared = DeathReturnPipeline(cfg()), {}
    reach_travel(pipe, shared)
    step(pipe, LIST_OPEN, 20.0, shared, HOME)             # «прилетів»
    step(pipe, LIST_OPEN, 23.0, shared, HOME)             # сів
    step(pipe, LIST_OPEN, 23.5, shared, HOME)             # закриваємо «Список»
    step(pipe, LIST_CLOSED, 25.0, shared, Position(x=520, y=900, known=True))
    res = step(pipe, LIST_CLOSED, 26.0, shared, Position(x=520, y=900, known=True))
    assert pipe.state is DeathState.OPEN_LIST
    assert any("пробую ще раз" in e for e in res.events)
