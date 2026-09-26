"""Вибір мобів за назвою: читання тексту, нечітке порівняння і поведінка пошуку цілі."""
from __future__ import annotations

import pytest
from PIL import Image

from app.pipelines.actions import PressKey
from app.pipelines.base import Frame, PipelineContext
from app.pipelines.target_search import TargetSearchPipeline
from app.vision.text import OcrConfig, best_match, normalize, read_line, similarity

MOB = "Горный варвар"
NAME_BAND = (600, 24, 820, 42)


def ctx_for(image: Image.Image, ts: float, shared: dict | None = None) -> PipelineContext:
    return PipelineContext(window="test", frame=Frame(image=image, ts=ts),
                           shared={} if shared is None else shared)


def keys(res) -> list[str]:
    return [a.key for a in res.actions if isinstance(a, PressKey)]


# ---- нормалізація і схожість --------------------------------------------------
def test_level_and_punctuation_are_ignored():
    assert normalize("[8] Горный варвар!") == normalize("горный варвар")


def test_latin_lookalikes_are_folded():
    """OCR часто дає латинські двійники: «вapвap» замість «варвар»."""
    assert similarity("Гopный вapвap", MOB) == 1.0


@pytest.mark.parametrize("text", ["Горный варвао", "Горныи варвар", "[8] горный варвар", "Горн варвар"])
def test_small_ocr_errors_still_match(text):
    assert best_match(text, [MOB], 0.75) is not None


@pytest.mark.parametrize("text", ["Дикий кабан", "Малахитовый мотыль", ""])
def test_other_names_do_not_match(text):
    assert best_match(text, [MOB], 0.75) is None


def test_threshold_can_be_tightened():
    assert best_match("Горн варвар", [MOB], 0.95) is None


# ---- читання з кадру ----------------------------------------------------------
def test_reads_mob_name_from_real_frame(real_frame):
    text = read_line(real_frame.crop(NAME_BAND), OcrConfig())
    if not text:
        pytest.skip("tesseract недоступний")
    assert best_match(text, [MOB], 0.75) is not None


# ---- поведінка пошуку цілі ----------------------------------------------------
def cfg_with_names(search_cfg, **names):
    data = search_cfg.model_dump()
    data["names"] = {**data["names"], "enabled": True, **names}
    return type(search_cfg)(**data)


def run(pipe, frame, shared, frames=3):
    """Прокрутити кілька кадрів і зібрати всі дії та події разом."""
    from app.pipelines.actions import PipelineResult

    total = PipelineResult()
    for i in range(frames):
        res = pipe.process(ctx_for(frame, i * 0.2, shared))
        total.actions += res.actions
        total.events += res.events
    return total


def test_allowed_mob_is_taken(real_frame, search_cfg, monkeypatch):
    monkeypatch.setattr("app.pipelines.target_search.read_line", lambda *a, **k: "[8] Горный варвар")
    pipe = TargetSearchPipeline(cfg_with_names(search_cfg, allow=[MOB]))
    shared: dict = {}
    run(pipe, real_frame, shared)
    assert shared["target"].present


def test_mob_outside_the_list_is_skipped(real_frame, search_cfg, monkeypatch):
    monkeypatch.setattr("app.pipelines.target_search.read_line", lambda *a, **k: "Дикий кабан")
    pipe = TargetSearchPipeline(cfg_with_names(search_cfg, allow=[MOB]))
    shared: dict = {}
    res = run(pipe, real_frame, shared)
    assert not shared["target"].present
    assert set(keys(res)) <= {search_cfg.target_key}, "скидати Esc не треба, лише перемкнутись"
    assert any("не зі списку" in e for e in res.events)


def test_denied_mob_is_skipped(real_frame, search_cfg, monkeypatch):
    monkeypatch.setattr("app.pipelines.target_search.read_line", lambda *a, **k: "[8] Горный варвар")
    pipe = TargetSearchPipeline(cfg_with_names(search_cfg, deny=[MOB]))
    shared: dict = {}
    res = run(pipe, real_frame, shared)
    assert not shared["target"].present
    assert any("заборонених" in e for e in res.events)


def test_ocr_error_still_recognises_allowed_mob(real_frame, search_cfg, monkeypatch):
    """Саме заради цього порівняння нечітке."""
    monkeypatch.setattr("app.pipelines.target_search.read_line", lambda *a, **k: "[8] Гopный варвао")
    pipe = TargetSearchPipeline(cfg_with_names(search_cfg, allow=[MOB]))
    shared: dict = {}
    run(pipe, real_frame, shared)
    assert shared["target"].present


def test_unreadable_name_does_not_block_farming(real_frame, search_cfg, monkeypatch):
    """Не прочиталось — б'ємо як раніше, краще так, ніж стояти."""
    monkeypatch.setattr("app.pipelines.target_search.read_line", lambda *a, **k: "")
    pipe = TargetSearchPipeline(cfg_with_names(search_cfg, allow=[MOB]))
    shared: dict = {}
    run(pipe, real_frame, shared)
    assert shared["target"].present


def test_filter_off_means_everything_is_a_target(real_frame, search_cfg, monkeypatch):
    monkeypatch.setattr("app.pipelines.target_search.read_line", lambda *a, **k: "Дикий кабан")
    pipe = TargetSearchPipeline(search_cfg)          # names.enabled = False
    shared: dict = {}
    run(pipe, real_frame, shared)
    assert shared["target"].present


def test_warns_when_the_list_does_not_match_the_place(real_frame, search_cfg, monkeypatch):
    """Список мобів з іншої локації = бот крутиться намарно, тому пише про це в лог."""
    monkeypatch.setattr("app.pipelines.target_search.read_line", lambda *a, **k: "Дикий кабан")
    pipe = TargetSearchPipeline(cfg_with_names(search_cfg, allow=[MOB]))
    shared: dict = {}
    warned = []
    # відмова тепер коштує одного перемикання на секунду, а не кадру, тому й часу
    # на потрібну кількість відмов треба більше
    for i in range(search_cfg.warn_after_rejects * 8):
        res = pipe.process(ctx_for(real_frame, i * 0.5, shared))
        warned += [e for e in res.events if "не зі списку «Бити тільки цих»" in e]
    assert len(warned) == 1, "попереджаємо один раз, а не щокадру"
