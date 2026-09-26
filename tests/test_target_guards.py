"""
Запобіжники пошуку цілі: не брати в ціль свого пета, дрібні червоні плями і
не зависати на цілі, яку не виходить убити.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.pipelines.actions import PressKey
from app.pipelines.base import Frame, PipelineContext
from app.pipelines.target_search import TargetSearchPipeline
from app.vision.bars import scan_bar
from app.vision.nameplate import HostileCheckConfig, TargetKind, classify_target, name_region
from tests.conftest import FIXTURES, set_bar_fill

PET_TARGET = FIXTURES / "frame_1440_target_pet.png"
TARGET_BAR = (605, 208, range(9, 17))


def ctx_for(image: Image.Image, ts: float, shared: dict | None = None) -> PipelineContext:
    return PipelineContext(window="test", frame=Frame(image=image, ts=ts),
                           shared={} if shared is None else shared)


def keys(res) -> list[str]:
    return [a.key for a in res.actions if isinstance(a, PressKey)]


@pytest.fixture(scope="module")
def pet_target_frame() -> Image.Image:
    return Image.open(PET_TARGET).convert("RGB")


# ---- колір назви --------------------------------------------------------------
def classify(frame: Image.Image, search_cfg) -> TargetKind:
    reading = scan_bar(frame.crop(search_cfg.region.box), search_cfg.region, search_cfg.bar)
    assert reading.present, "смужка цілі має бути видима"
    region = name_region(reading.x0, reading.filled, reading.row, search_cfg.hostile_check)
    return classify_target(frame.crop(region.box), search_cfg.hostile_check)[0]


def test_mob_name_is_hostile(real_frame, search_cfg):
    assert classify(real_frame, search_cfg) is TargetKind.HOSTILE


def test_own_pet_name_is_friendly(pet_target_frame, search_cfg):
    """У пета назва синя, у моба жовта — цього досить, щоб їх розрізнити."""
    assert classify(pet_target_frame, search_cfg) is TargetKind.FRIENDLY


# ---- поведінка пайплайна ------------------------------------------------------
def test_pet_is_dropped_not_attacked(pet_target_frame, search_cfg):
    pipe = TargetSearchPipeline(search_cfg)
    shared: dict = {}
    drops = []
    for i in range(4):
        res = pipe.process(ctx_for(pet_target_frame, i * 0.2, shared))
        if any("не моб" in e for e in res.events):
            drops.append(res)
        assert not shared["target"].present, "пет не має ставати ціллю в жодному кадрі"
    assert drops, "пета треба скинути"
    # ані Esc, ані Tab на цьому ж кадрі: нову ціль візьме звичайний пошук трохи згодом
    assert keys(drops[0]) == []


def test_small_red_spot_is_not_a_target(real_frame, search_cfg):
    """
    Руда земля й дрібні плями не мають ставати ціллю. Раніше від них рятував поріг
    «смужка не коротша за 60 px», тепер — ширина рамки: у плями нема ані порожньої
    частини смужки, ані самої рамки.
    """
    ground = Image.new("RGB", real_frame.size, (150, 110, 70))
    px = ground.load()
    for y in range(8, 18):                       # яскрава червона пляма завширшки 25 px
        for x in range(700, 725):
            px[x, y] = (205, 60, 40)
    pipe = TargetSearchPipeline(search_cfg)
    shared: dict = {}
    for i in range(4):
        pipe.process(ctx_for(ground, i * 0.2, shared))
    assert not shared["target"].present


def test_empty_bar_without_red_is_not_a_target(real_frame, search_cfg):
    """Сама лише порожня смужка (без жодного червоного) — теж не ціль."""
    empty = Image.new("RGB", real_frame.size, (40, 70, 40))
    px = empty.load()
    for y in range(8, 18):
        for x in range(605, 813):
            px[x, y] = (29, 54, 59)
    pipe = TargetSearchPipeline(search_cfg)
    shared: dict = {}
    for i in range(4):
        pipe.process(ctx_for(empty, i * 0.2, shared))
    assert not shared["target"].present


def test_normal_target_still_accepted(real_frame, search_cfg):
    pipe = TargetSearchPipeline(search_cfg)
    shared: dict = {}
    pipe.process(ctx_for(real_frame, 0.0, shared))
    pipe.process(ctx_for(real_frame, 0.2, shared))
    assert shared["target"].present


def test_target_with_frozen_hp_is_dropped(real_frame, search_cfg):
    """HP не рухається — б'ємо марно, беремо іншу ціль."""
    pipe = TargetSearchPipeline(search_cfg)
    shared: dict = {}
    pipe.process(ctx_for(real_frame, 0.0, shared))
    pipe.process(ctx_for(real_frame, 0.2, shared))
    quiet = pipe.process(ctx_for(real_frame, 5.0, shared))
    assert keys(quiet) == [], "до таймауту нічого не робимо"
    res = pipe.process(ctx_for(real_frame, 0.2 + search_cfg.stale_after + 0.1, shared))
    assert keys(res) == []
    assert any("HP не падає" in e for e in res.events)


# ---- зміна цілі без Esc -------------------------------------------------------
def test_switches_by_tab_without_escape(real_frame, search_cfg, monkeypatch):
    """
    Влад просив міняти ціль просто клавішею вибору по затримці. Esc у грі закриває
    вікна (так одного разу зникла рамка пета), тому за замовчуванням його нема.
    """
    monkeypatch.setattr("app.pipelines.target_search.read_line", lambda *a, **k: "Чужий моб")
    cfg = search_cfg.model_copy(update={
        "drop_key": None,
        "names": search_cfg.names.model_copy(update={"enabled": True, "allow": ["Потрібний моб"]}),
    })
    pipe, shared = TargetSearchPipeline(cfg), {}
    pressed: list[str] = []
    for i in range(20):
        pressed += keys(pipe.process(ctx_for(real_frame, i * 0.3, shared)))
    assert "esc" not in pressed, "Esc не тиснемо взагалі"
    assert pressed and set(pressed) == {cfg.target_key}
    # перемикань має бути приблизно раз на retarget_delay, а не щокадру
    assert len(pressed) <= 20 * 0.3 / cfg.retarget_delay + 1


def test_rejected_target_is_not_re_read_every_frame(real_frame, search_cfg, monkeypatch):
    """OCR коштує ~100 мс, тому відкинуту ціль не перечитуємо до наступного перемикання."""
    reads = []

    def fake_read(*a, **k):
        reads.append(1)
        return "Чужий моб"

    monkeypatch.setattr("app.pipelines.target_search.read_line", fake_read)
    cfg = search_cfg.model_copy(update={
        "names": search_cfg.names.model_copy(update={"enabled": True, "allow": ["Потрібний моб"]}),
    })
    pipe, shared = TargetSearchPipeline(cfg), {}
    for i in range(10):
        pipe.process(ctx_for(real_frame, i * 0.1, shared))    # одна секунда
    assert len(reads) <= 2, f"назву читали {len(reads)} разів замість одного"


def test_regenerating_target_is_dropped(real_frame, search_cfg):
    """
    Недосяжний моб: удар збиває HP до половини, а він відновлюється до повного —
    HP весь час «рухається», але моб не вмирає. Наживо так загинув пет.
    """
    pipe, shared = TargetSearchPipeline(search_cfg), {}
    cycle = [1.0, 0.5, 0.65, 0.78, 0.91, 1.0]
    frames = {f: set_bar_fill(real_frame, *TARGET_BAR, f) for f in set(cycle)}
    t, dropped = 0.0, []
    for _ in range(6):
        for f in cycle:
            res = pipe.process(ctx_for(frames[f], t, shared))
            dropped += [e for e in res.events if "HP не падає" in e]
            t += 0.7
        if dropped:
            break
    assert dropped, "по колу 50–100% — це не бій, ціль треба кинути"
    assert t <= search_cfg.stale_after + 6 * 0.7 + 1


def test_target_that_keeps_losing_hp_is_kept(real_frame, search_cfg):
    pipe, shared = TargetSearchPipeline(search_cfg), {}
    t = 0.0
    for f in [1.0, 1.0] + [1 - i * 0.05 for i in range(1, 18)]:
        res = pipe.process(ctx_for(set_bar_fill(real_frame, *TARGET_BAR, f), t, shared))
        assert not any("HP не падає" in e for e in res.events)
        t += 1.5                                  # повільно, але падає — б'ємо далі


def test_almost_dead_target_is_still_a_target(real_frame, search_cfg):
    """
    Головний баг: у моба лишалось 12 HP із 726 (три червоні пікселі), бот вважав
    його мертвим, кидав недобитим і брав нового. Рамку цілі видно по порожній
    частині смужки, тому ціль лишається ціллю до самого кінця.
    """
    pipe, shared = TargetSearchPipeline(search_cfg), {}
    for i in range(3):
        pipe.process(ctx_for(real_frame, i * 0.2, shared))
    assert shared["target"].present

    dying = set_bar_fill(real_frame, *TARGET_BAR, 0.02)      # ~4 px червоного
    res = pipe.process(ctx_for(dying, 1.0, shared))
    assert shared["target"].present, "три пікселі HP — це ще живий моб"
    assert not any("мертва" in e for e in res.events)
    assert keys(res) == [], "і нової цілі не шукаємо"


def test_dead_target_is_still_recognised(real_frame, search_cfg):
    """Коли моб справді вмирає, рамка зникає повністю — ось це і є смерть."""
    pipe, shared = TargetSearchPipeline(search_cfg), {}
    for i in range(3):
        pipe.process(ctx_for(real_frame, i * 0.2, shared))
    gone = Image.new("RGB", real_frame.size, (40, 70, 40))
    res = pipe.process(ctx_for(gone, 1.0, shared))
    assert not shared["target"].present
    assert any("мертва" in e for e in res.events)


def test_wounded_target_can_be_taken_again(real_frame, search_cfg):
    """Пораненого моба бот має могти взяти назад, а не оминати як «дрібну пляму»."""
    pipe, shared = TargetSearchPipeline(search_cfg), {}
    wounded = set_bar_fill(real_frame, *TARGET_BAR, 0.05)
    for i in range(3):
        pipe.process(ctx_for(wounded, i * 0.2, shared))
    assert shared["target"].present


def test_warns_when_standing_without_targets(search_cfg):
    """
    Годину тиші в лозі легко сплутати із зависанням. Якщо цілей просто нема, бот
    має сам про це сказати.
    """
    empty = Image.new("RGB", (1440, 1080), (40, 70, 40))
    cfg = search_cfg.model_copy(update={"idle_warn": 60.0})
    pipe, shared = TargetSearchPipeline(cfg), {}
    warned = []
    for i in range(40):
        res = pipe.process(ctx_for(empty, i * 5.0, shared))
        warned += [e for e in res.events if "без цілі" in e]
    assert 2 <= len(warned) <= 4, "нагадує раз на хвилину, а не щокадру"


def test_no_warning_while_fighting(real_frame, search_cfg):
    cfg = search_cfg.model_copy(update={"idle_warn": 5.0})
    pipe, shared = TargetSearchPipeline(cfg), {}
    for i in range(20):
        res = pipe.process(ctx_for(real_frame, i * 2.0, shared))
        assert not [e for e in res.events if "без цілі" in e]
