"""Координати й висота: числа читаються з панелі гри і лягають на спільну дошку."""
from __future__ import annotations

import pytest
from PIL import Image

from app.pipelines.altitude import AltitudeConfig, AltitudePipeline
from app.pipelines.base import Frame, PipelineContext
from app.pipelines.position import PositionConfig, PositionPipeline
from app.pipelines.shared import SHARED_ALT, SHARED_POS, Position
from app.vision.digits import DigitsConfig, read_numbers
from app.vision.text import OcrConfig
from tests.conftest import FIXTURES

PANEL = FIXTURES / "frame_1440_coords.png"


@pytest.fixture(scope="module")
def panel_frame() -> Image.Image:
    """Справжній кадр гри: «Поселок у моста / 241, 563  21»."""
    return Image.open(PANEL).convert("RGB")


def ctx_for(image: Image.Image, ts: float, shared: dict) -> PipelineContext:
    return PipelineContext(window="test", frame=Frame(image=image, ts=ts), shared=shared)


# ---- читання чисел ------------------------------------------------------------
def test_reads_coordinates_from_the_panel(panel_frame):
    assert read_numbers(panel_frame, DigitsConfig(), OcrConfig())[:2] == [241, 563]


def test_reads_altitude_by_its_green_color(panel_frame):
    """Висота зелена, координати білі — тому в одне число інше не потрапляє."""
    assert read_numbers(panel_frame, DigitsConfig(color="green"), OcrConfig()) == [21]


def test_empty_place_gives_nothing():
    blank = Image.new("RGB", (1440, 1080), (18, 30, 24))
    assert read_numbers(blank, DigitsConfig(), OcrConfig()) == []


# ---- пайплайн координат -------------------------------------------------------
def test_position_lands_on_the_shared_board(panel_frame):
    pipe, shared = PositionPipeline(PositionConfig()), {}
    pipe.process(ctx_for(panel_frame, 0.0, shared))
    assert not shared[SHARED_POS].known, "першому читанню не віримо на слово"
    pipe.process(ctx_for(panel_frame, 1.0, shared))
    pos = shared[SHARED_POS]
    assert (pos.x, pos.y, pos.known) == (241, 563, True)


def test_position_is_not_re_read_every_frame(panel_frame, monkeypatch):
    reads = []
    monkeypatch.setattr("app.pipelines.position.read_numbers",
                        lambda *a, **k: (reads.append(1), [10, 20])[1])
    pipe, shared = PositionPipeline(PositionConfig(read_every=1.0)), {}
    for i in range(10):
        pipe.process(ctx_for(panel_frame, i * 0.1, shared))
    assert len(reads) == 1, "читаємо раз на секунду, а не щокадру"


def test_position_survives_one_bad_read(panel_frame, monkeypatch):
    """Панель могло перекрити вікном — стару точку тримаємо, поки не втратимо надовго."""
    answers = [[241, 563], [241, 563], [], [], []]
    monkeypatch.setattr("app.pipelines.position.read_numbers", lambda *a, **k: answers.pop(0))
    pipe, shared = PositionPipeline(PositionConfig(read_every=0, forget_after=3)), {}
    for i in range(4):
        pipe.process(ctx_for(panel_frame, i, shared))
    assert shared[SHARED_POS].known, "два промахи — ще тримаємось"
    pipe.process(ctx_for(panel_frame, 4, shared))
    assert not shared[SHARED_POS].known, "довго не бачимо панель — чесно кажемо, що не знаємо"


def test_absurd_jump_is_treated_as_a_misread(panel_frame, monkeypatch):
    answers = [[241, 563], [241, 563], [999, 111], [242, 564]]
    monkeypatch.setattr("app.pipelines.position.read_numbers", lambda *a, **k: answers.pop(0))
    pipe, shared = PositionPipeline(PositionConfig(read_every=0, max_jump=60)), {}
    for i in range(3):
        pipe.process(ctx_for(panel_frame, i, shared))
    assert (shared[SHARED_POS].x, shared[SHARED_POS].y) == (241, 563), "стрибок через пів карти — брак"
    pipe.process(ctx_for(panel_frame, 3, shared))
    assert (shared[SHARED_POS].x, shared[SHARED_POS].y) == (242, 564)


def test_distance_between_points():
    assert Position(x=100, y=100, known=True).distance_to(Position(x=103, y=104, known=True)) == 5


# ---- пайплайн висоти ----------------------------------------------------------
def test_altitude_lands_on_the_shared_board(panel_frame):
    pipe, shared = AltitudePipeline(AltitudeConfig()), {}
    pipe.process(ctx_for(panel_frame, 0.0, shared))
    assert (shared[SHARED_ALT].z, shared[SHARED_ALT].known) == (21, True)


def test_altitude_is_forgotten_when_the_panel_is_gone(panel_frame, monkeypatch):
    monkeypatch.setattr("app.pipelines.altitude.read_numbers", lambda *a, **k: [])
    pipe, shared = AltitudePipeline(AltitudeConfig(read_every=0, forget_after=2)), {}
    for i in range(3):
        pipe.process(ctx_for(panel_frame, i, shared))
    assert not shared[SHARED_ALT].known


# ---- аналіз: чи далеко відійшли -----------------------------------------------
def test_first_place_becomes_home(panel_frame, monkeypatch):
    monkeypatch.setattr("app.pipelines.position.read_numbers", lambda *a, **k: [241, 563])
    pipe, shared = PositionPipeline(PositionConfig(read_every=0)), {}
    pipe.process(ctx_for(panel_frame, 0, shared))
    res = pipe.process(ctx_for(panel_frame, 1, shared))
    assert "від місця 0" in res.status


def test_warns_once_when_the_character_wandered_off(panel_frame, monkeypatch):
    """Персонажа відтягують моби — це видно по координатах, і про це варто сказати."""
    steps = [[241, 563], [241, 563], [251, 563], [271, 563], [291, 563], [271, 563], [241, 563]]
    monkeypatch.setattr("app.pipelines.position.read_numbers", lambda *a, **k: steps.pop(0))
    pipe, shared = PositionPipeline(PositionConfig(read_every=0, warn_away=40)), {}
    events = []
    for i in range(7):
        events += pipe.process(ctx_for(panel_frame, i, shared)).events
    assert len([e for e in events if "відійшов від місця" in e]) == 1, "попереджаємо один раз"
    assert any("повернувся на місце" in e for e in events)


def test_home_can_be_set_by_hand(panel_frame, monkeypatch):
    monkeypatch.setattr("app.pipelines.position.read_numbers", lambda *a, **k: [241, 563])
    pipe, shared = PositionPipeline(PositionConfig(read_every=0, home_x=200, home_y=563)), {}
    pipe.process(ctx_for(panel_frame, 0, shared))
    res = pipe.process(ctx_for(panel_frame, 1, shared))
    assert "від місця 41" in res.status


def test_one_garbage_first_read_is_not_believed(panel_frame, monkeypatch):
    """Так уже було: рамка мінімапи дописала цифру, і вийшло «226, 5493»."""
    steps = [[226, 5493], [226, 549], [226, 549]]
    monkeypatch.setattr("app.pipelines.position.read_numbers", lambda *a, **k: steps.pop(0))
    pipe, shared = PositionPipeline(PositionConfig(read_every=0)), {}
    for i in range(3):
        pipe.process(ctx_for(panel_frame, i, shared))
    pos = shared[SHARED_POS]
    assert (pos.x, pos.y) == (226, 549)
    assert shared["home"].y == 549, "і домом не став сміттєвий «5493»"


def test_repeated_jump_is_a_teleport_not_a_misread(panel_frame, monkeypatch):
    """Воскресіння в місті: координати стрибнули далеко і там лишились — приймаємо."""
    answers = [[460, 680], [460, 680], [900, 300], [901, 300], [902, 301], [903, 301],
               [904, 302], [905, 302]]
    monkeypatch.setattr("app.pipelines.position.read_numbers", lambda *a, **k: answers.pop(0))
    pipe, shared = PositionPipeline(PositionConfig(read_every=0)), {}
    for i in range(6):
        pipe.process(ctx_for(panel_frame, i, shared))
    assert (shared[SHARED_POS].x, shared[SHARED_POS].y) == (460, 680), "коротка серія — ще сумніваємось"
    pipe.process(ctx_for(panel_frame, 6, shared))
    assert (shared[SHARED_POS].x, shared[SHARED_POS].y) == (904, 302), "довга серія — це телепорт"
    assert shared[SHARED_POS].at == 6


def test_four_digit_coordinate_is_a_misread(panel_frame, monkeypatch):
    """Живий випадок: «458, 6693» — до числа приліпилась чужа цифра, а бот поїхав «додому»."""
    answers = [[456, 674], [456, 674], [458, 6693], [458, 6693], [458, 6693], [456, 675]]
    monkeypatch.setattr("app.pipelines.position.read_numbers", lambda *a, **k: answers.pop(0))
    pipe, shared = PositionPipeline(PositionConfig(read_every=0)), {}
    for i in range(5):
        pipe.process(ctx_for(panel_frame, i, shared))
    assert (shared[SHARED_POS].x, shared[SHARED_POS].y) == (456, 674), "4-значну координату не беремо"
    pipe.process(ctx_for(panel_frame, 5, shared))
    assert (shared[SHARED_POS].x, shared[SHARED_POS].y) == (456, 675)


def test_a_few_seconds_of_wrong_digits_do_not_move_the_bot(panel_frame, monkeypatch):
    """Живий випадок: «453, 70» замість 674 трималось три читання — і бот їхав «додому»."""
    answers = [[456, 674], [456, 674], [453, 70], [453, 70], [453, 70], [456, 675]]
    monkeypatch.setattr("app.pipelines.position.read_numbers", lambda *a, **k: answers.pop(0))
    pipe, shared = PositionPipeline(PositionConfig(read_every=0)), {}
    for i in range(5):
        pipe.process(ctx_for(panel_frame, i, shared))
    assert (shared[SHARED_POS].x, shared[SHARED_POS].y) == (456, 674)


def test_a_coordinate_that_lost_its_digits_is_a_misread(panel_frame, monkeypatch):
    """Живий випадок: «456, 2» замість «456, 673» — у числі загубились дві цифри."""
    answers = [[456, 673], [456, 673], [456, 2], [456, 2], [456, 2], [456, 674]]
    monkeypatch.setattr("app.pipelines.position.read_numbers", lambda *a, **k: answers.pop(0))
    pipe, shared = PositionPipeline(PositionConfig(read_every=0)), {}
    for i in range(5):
        pipe.process(ctx_for(panel_frame, i, shared))
    assert (shared[SHARED_POS].x, shared[SHARED_POS].y) == (456, 673)
