"""Продаж луту: перетягнути з рюкзака в лот, підтвердити кількість, натиснути «Продать»."""
from __future__ import annotations

import pytest
from PIL import Image

from app.pipelines.actions import ClickAt, DragTo, PressKey
from app.pipelines.base import Frame, PipelineContext
from app.pipelines.sell import SellConfig, SellPipeline, SellState
from app.pipelines.shared import TargetInfo, busy_reasons, set_busy, write_target
from app.vision.schemas import BarReading
from tests.conftest import FIXTURES


def cfg(**kw) -> SellConfig:
    """
    Конфіг для тестів: місця вікон беремо такі, які вони на кадрі-фікстурі, щоб крок
    «розставити вікна» не заважав. Його перевіряє окремий тест.
    """
    base = SellConfig()
    shop = where(SHOP_FRAME, base.shop_title)
    bag = where(SHOP_FRAME, base.bag_title)
    return SellConfig(**{"every": 10.0, "hover_delay": 0.1, "step_delay": 0.0, "park_offset": 0,
                         "shop_park": {"x": shop[0], "y": shop[1]},
                         "bag_park": {"x": bag[0], "y": bag[1]}, **kw})

def live(name: str) -> Image.Image:
    return Image.open(FIXTURES / name).convert("RGB")


def ctx(image: Image.Image, ts: float, shared: dict, target: bool = False) -> PipelineContext:
    write_target(shared, TargetInfo(present=target, bar=BarReading(), updated_at=ts))
    return PipelineContext(window="t", frame=Frame(image=image, ts=ts), shared=shared)


def keys(res) -> list[str]:
    return [a.key for a in res.actions if isinstance(a, PressKey)]


def clicks(res) -> list[tuple[int, int]]:
    return [(a.x, a.y) for a in res.actions if isinstance(a, ClickAt)]


def drags(res) -> list[tuple[int, int, int, int]]:
    return [(a.x1, a.y1, a.x2, a.y2) for a in res.actions if isinstance(a, DragTo)]


BLANK = Image.new("RGB", (1440, 1080), (12, 20, 26))


# ---- коли продавати -----------------------------------------------------------
def test_waits_for_its_time(monkeypatch):
    pipe, shared = SellPipeline(cfg()), {}
    pipe.process(ctx(BLANK, 0.0, shared))
    assert pipe.process(ctx(BLANK, 5.0, shared)).actions == [], "ще рано"


def test_finishes_the_fight_first():
    pipe, shared = SellPipeline(cfg()), {}
    pipe.process(ctx(BLANK, 0.0, shared))
    res = pipe.process(ctx(BLANK, 20.0, shared, target=True))
    assert res.actions == [] and "добиваю ціль" in res.status


def test_waits_for_repair():
    pipe, shared = SellPipeline(cfg()), {}
    pipe.process(ctx(BLANK, 0.0, shared))
    set_busy(shared, "repair", True)
    res = pipe.process(ctx(BLANK, 20.0, shared))
    assert res.actions == [] and "чекаю: repair" in res.status


def test_opens_the_bag_first():
    pipe, shared = SellPipeline(cfg()), {}
    pipe.process(ctx(BLANK, 0.0, shared))
    res = pipe.process(ctx(BLANK, 20.0, shared))
    assert keys(res) == ["b"]
    assert busy_reasons(shared) == {"sell"}, "поки продаємо — не воюємо"


# ---- сам продаж ---------------------------------------------------------------
SHOP_FRAME = "frame_1440_shop_sell2.png"


def where(name: str, spec) -> tuple[int, int]:
    """Де на кадрі потрібний напис — щоб тести не тримались за магічні числа."""
    from app.vision.template import find_template

    point = find_template(live(name), spec)
    assert point is not None, "напис має знаходитись на кадрі"
    return point.x, point.y


def open_shop(pipe, shared, t=20.0, arrange=True):
    """
    Довести до стану, коли рюкзак і Лавка відкриті й розсунуті. На кадрі-фікстурі
    вікна накладаються, тому бот спершу відсовує «Лавку» — цей крок теж проходимо.
    """
    pipe.process(ctx(BLANK, 0.0, shared))
    pipe.process(ctx(BLANK, t, shared))
    shop = live(SHOP_FRAME)
    pipe.process(ctx(shop, t + 1, shared))
    if arrange:
        for n in range(4):                           # крок «розставити вікна»
            if pipe.state is SellState.MOVING:
                break
            pipe.process(ctx(shop, t + 1.5 + n * 0.3, shared))
    return shop


def test_drags_the_chosen_cells_into_lots():
    c = cfg(cells=["4:1", "4:2"])
    pipe, shared = SellPipeline(c), {}
    shop = open_shop(pipe, shared)
    bag = where(SHOP_FRAME, c.bag_title)
    panel = where(SHOP_FRAME, c.sell_panel)
    cell = (bag[0] + c.cell_first.x, bag[1] + c.cell_first.y + c.cell_step.y * 3)
    lot = (panel[0] + c.lot_first.x, panel[1] + c.lot_first.y)

    first = pipe.process(ctx(shop, 22.0, shared))
    assert drags(first) == [(cell[0], cell[1], lot[0], lot[1])], "комірка останнього рядка -> лот"
    second = pipe.process(ctx(shop, 23.0, shared))
    assert drags(second)[0][:2] == (cell[0] + c.cell_step.x, cell[1]), "далі сусідня комірка"
    assert drags(second)[0][2:] == (lot[0] + c.lot_step.x, lot[1]), "і наступний лот"


def test_asks_for_maximum_when_the_game_wants_a_number():
    pipe, shared = SellPipeline(cfg(cells=["4:1"])), {}
    open_shop(pipe, shared)
    pipe.process(ctx(live(SHOP_FRAME), 22.0, shared))
    res = pipe.process(ctx(live("frame_1440_amount.png"), 23.0, shared))
    assert pipe.state is SellState.AMOUNT
    res = pipe.process(ctx(live("frame_1440_amount.png"), 23.5, shared))
    assert clicks(res) == [(676, 874), (777, 874)], "«Максимум», потім «Принять»"


def test_presses_sell_confirms_and_closes_windows():
    c = cfg(cells=["4:1"])
    pipe, shared = SellPipeline(c), {}
    shop = open_shop(pipe, shared)
    panel = where(SHOP_FRAME, c.sell_panel)
    pipe.process(ctx(shop, 22.0, shared))                 # перетягнули єдину комірку
    res = pipe.process(ctx(shop, 23.0, shared))
    assert clicks(res) == [(panel[0] + c.sell_button.x, panel[1] + c.sell_button.y)], "«Продать»"
    assert any("продаю" in e for e in res.events)

    # гра перепитує: без цього кроку лот лишався на місці
    ask = live("frame_1440_sell_confirm.png")
    res = pipe.process(ctx(ask, 23.5, shared))
    assert clicks(res) and any("продано" in e for e in res.events), "тиснемо «Да»"
    closing = pipe.process(ctx(shop, 25.0, shared))
    assert keys(closing) == ["esc"]
    done = pipe.process(ctx(BLANK, 25.0, shared))
    assert pipe.state is SellState.IDLE
    assert busy_reasons(shared) == set(), "бот знову вільний"
    assert any("вікна закрито" in e for e in done.events)


def test_nothing_to_sell_is_not_an_error():
    pipe, shared = SellPipeline(cfg(cells=[])), {}
    pipe.process(ctx(BLANK, 0.0, shared))
    assert pipe.process(ctx(BLANK, 20.0, shared)).actions == [], "порожній список — просто мовчимо"


def test_cells_are_configurable():
    """Влад хотів вибирати комірки, а не лише останній рядок."""
    c = cfg(cells=["1:1", "2:5"])
    pipe, shared = SellPipeline(c), {}
    shop = open_shop(pipe, shared)
    bag = where(SHOP_FRAME, c.bag_title)
    first = pipe.process(ctx(shop, 22.0, shared))
    assert drags(first)[0][:2] == (bag[0] + c.cell_first.x, bag[1] + c.cell_first.y), "перший рядок"
    second = pipe.process(ctx(shop, 23.0, shared))
    assert drags(second)[0][:2] == (bag[0] + c.cell_first.x + c.cell_step.x * 4,
                                    bag[1] + c.cell_first.y + c.cell_step.y), "другий рядок, п'ята комірка"


def test_default_cells_are_the_last_row():
    assert SellConfig().cells == [f"4:{c}" for c in range(1, 9)]


def test_survives_the_shop_covering_the_bag_title():
    """
    Вікна в грі накладаються: Лавка перекрила заголовок рюкзака, і продаж зривався
    з «вікно закрилось». Позицію рюкзака треба пам'ятати.
    """
    pipe, shared = SellPipeline(cfg(cells=["4:1", "4:2"])), {}
    shop = open_shop(pipe, shared)
    pipe.process(ctx(shop, 22.0, shared))              # обидва заголовки видно

    covered = shop.copy()                              # тепер заголовок рюкзака закритий
    px = covered.load()
    for y in range(285, 310):
        for x in range(930, 1070):
            px[x, y] = (10, 14, 18)
    res = pipe.process(ctx(covered, 23.0, shared))
    assert drags(res), "перетягуємо далі за запам'ятованою позицією"
    assert pipe.state is SellState.MOVING


def test_parks_the_shop_when_it_covers_the_bag():
    """
    Вікна в грі лягають одне на одне: «Лавка» накриває комірки рюкзака, а рюкзак —
    кнопку «Продать». Тому бот спершу розставляє їх по різних кутах.
    """
    c = cfg(cells=["4:1"], shop_park={"x": 300, "y": 300})
    pipe, shared = SellPipeline(c), {}
    shop = open_shop(pipe, shared, arrange=False)
    title = where(SHOP_FRAME, c.shop_title)
    res = pipe.process(ctx(shop, 21.5, shared))
    assert drags(res) == [(title[0], title[1], 300, 300)], "«Лавку» — на задане місце"
    assert any("накривають" in e for e in res.events)


def test_the_shop_is_parked_beside_the_bag_not_over_it():
    """
    Живий випадок: «Лавка» стояла на (300,300) і накривала ліву половину рюкзака.
    Перші чотири комірки рядка не продавались — бот тягнув їх з-під чужого вікна.
    """
    c = cfg(cells=["4:1"], park_offset=430, park_edge=1250)
    pipe, shared = SellPipeline(c), {}
    shop = open_shop(pipe, shared, arrange=False)
    bag = where(SHOP_FRAME, c.bag_title)
    title = where(SHOP_FRAME, c.shop_title)
    res = pipe.process(ctx(shop, 21.5, shared))
    ((x1, y1, x2, y2),) = drags(res)
    assert (x1, y1) == title, "тягнемо саме «Лавку»"
    assert abs(x2 - bag[0]) == 430, "вбік від рюкзака рівно на заданий відступ"
    assert x2 <= 1250


def test_bag_window_is_never_dragged():
    """
    Рюкзак не соваємо: одного разу бот загнав його під «Лавку» і вже не міг знайти
    заголовок. Соваємо тільки «Лавку», якої достатньо, щоб звільнити комірки.
    """
    c = cfg(cells=["4:1"])
    pipe, shared = SellPipeline(c), {}
    shop = open_shop(pipe, shared, arrange=False)
    bag = where(SHOP_FRAME, c.bag_title)
    for step in range(3):
        res = pipe.process(ctx(shop, 21.5 + step * 0.5, shared))
        for d in drags(res):
            assert d[:2] != bag, "рюкзак лишається там, де його лишив гравець"

def test_arrange_survives_the_shop_covering_the_bag_title():
    """
    «Лавка» відкривається поверх рюкзака і накриває його заголовок. Через це продаж
    щоразу зривався з «вікно зникло під час розкладання».
    """
    c = cfg(cells=["4:1"])
    pipe, shared = SellPipeline(c), {}
    shop = open_shop(pipe, shared, arrange=False)

    covered = shop.copy()                       # заголовок рюкзака перекрито
    px = covered.load()
    bag = where(SHOP_FRAME, c.bag_title)
    for y in range(bag[1] - 12, bag[1] + 12):
        for x in range(bag[0] - 70, bag[0] + 70):
            px[x, y] = (9, 13, 17)
    res = pipe.process(ctx(covered, 21.5, shared))
    assert not any("зникло" in e for e in res.events), "позицію рюкзака ми вже знаємо"
    assert pipe.state is SellState.MOVING


# ---- порожні комірки ----------------------------------------------------------
def test_does_not_drag_from_an_empty_cell():
    """
    Живий випадок: «інколи смикає рюкзак». Порожня комірка предмета не дає, і миша,
    затиснута на порожньому місці, тягне саме вікно рюкзака.
    """
    from app.vision.template import find_template

    base = SellConfig()
    empty = live("frame_1440_bag_new_layout.png")          # останній рядок порожній
    bag = find_template(empty, base.bag_title)
    pipe = SellPipeline(base)
    for col in range(1, 9):
        point = pipe._cell_point(bag, f"4:{col}")
        assert not pipe._has_item(empty, point), f"комірка 4:{col} порожня"

    full = live("frame_1440_bag_only.png")
    bag = find_template(full, base.bag_title)
    filled = [c for c in range(1, 9) if pipe._has_item(full, pipe._cell_point(bag, f"4:{c}"))]
    assert filled == list(range(1, 9)), "а тут предмети в усьому рядку, навіть темні"


def test_waits_instead_of_dragging_by_remembered_coordinates():
    """Заголовка рюкзака в кадрі нема — координати могли застаріти, тягнути не можна."""
    from app.core.geometry import Point

    pipe, shared = SellPipeline(cfg()), {}
    pipe.state = SellState.MOVING
    pipe.queue = ["4:1"]
    pipe.bag_at, pipe.shop_at = Point(x=1045, y=271), Point(x=300, y=300)
    res = pipe.process(ctx(BLANK, 1.0, shared))
    assert drags(res) == [] and pipe.queue == ["4:1"], "комірка лишилась у черзі"


def test_dark_items_are_not_mistaken_for_empty_cells():
    """
    Живий випадок: «лут не продається». Темно-червоні предмети рівні за кольором,
    як порожнє місце, і бот пропускав пів рядка — лут копився в рюкзаку.
    """
    from app.vision.template import find_template

    base = SellConfig()
    bag_frame = live("frame_1440_bag_dark_items.png")
    bag = find_template(bag_frame, base.bag_title)
    pipe = SellPipeline(base)
    filled = [c for c in range(1, 9)
              if pipe._has_item(bag_frame, pipe._cell_point(bag, f"4:{c}"))]
    assert filled == list(range(1, 9)), "у цьому рядку предмет у кожній комірці"


def test_chat_text_is_not_mistaken_for_the_sell_panel():
    """
    Живий випадок: напис «Продажа» знайшовся в чаті (0.79), бот перетягнув туди лут
    і тицьнув «Продать» у порожнечу — лоти не продались.
    """
    from app.vision.template import find_template, match

    frame = live("frame_1440_sell_panel_in_chat.png")
    base = SellConfig()
    assert match(frame, base.sell_panel)[1] < base.sell_panel.threshold, "чат — не «Продажа»"
    assert find_template(frame, base.sell_panel) is None


# ---- вікна після продажу ---------------------------------------------------------
def test_closes_windows_even_when_there_was_nothing_to_sell():
    """
    Найдорожча поламка з усіх: «продавати не було чого» лишало рюкзак і «Лавку»
    відкритими. Гра з відкритим вікном не приймає клавіші, тож бот не пив банку,
    не кликав пета й не бив — за ніч персонаж так загинув 40 разів.
    """
    pipe, shared = SellPipeline(cfg()), {}
    shop = open_shop(pipe, shared)
    pipe.state, pipe.lots, pipe.queue = SellState.SELLING, 0, []

    res = pipe.process(ctx(shop, 30.0, shared))
    assert pipe.state is SellState.CLOSING, "не розходимось з відкритими вікнами"
    assert any("закриваю вікна" in e for e in res.events)

    assert keys(pipe.process(ctx(shop, 31.0, shared))) == ["esc"], "спершу «Лавка»"
    res = pipe.process(ctx(BLANK, 32.0, shared))     # вікон більше нема
    assert pipe.state is SellState.IDLE
    assert busy_reasons(shared) == set(), "зайнятість знята"


def test_bag_left_alone_is_closed_by_its_own_key():
    """Esc знімає «Лавку», а рюкзак — ні: його треба закривати клавішею рюкзака."""
    from app.vision.template import find_template

    pipe, shared = SellPipeline(cfg()), {}
    open_shop(pipe, shared)
    pipe.state, pipe.why_closed = SellState.CLOSING, "лут продано"
    pipe.deadline, pipe.close_at = 100.0, 0.0
    bag_only = live("frame_1440_bag_only.png")
    assert find_template(bag_only, SellConfig().shop_title) is None, "на кадрі лише рюкзак"
    assert keys(pipe.process(ctx(bag_only, 33.0, shared))) == ["b"]
