"""Підтримка форми: бій чекає значок, але сервісні дії не блокуються."""
from __future__ import annotations

from PIL import Image

from app.core.geometry import Point
from app.pipelines.actions import PressKey
from app.pipelines.base import Frame, PipelineContext
from app.pipelines.form_keep import FormKeepConfig, FormKeepPipeline
from app.pipelines.shared import SHARED_COMBAT_READY, busy_reasons, set_busy
from app.vision.template import _load


BLANK = Image.new("RGB", (1440, 1080))


def ctx(ts: float, shared: dict) -> PipelineContext:
    return PipelineContext(window="test", frame=Frame(image=BLANK, ts=ts), shared=shared)


def keys(result) -> list[str]:
    return [action.key for action in result.actions if isinstance(action, PressKey)]


def test_form_template_is_bundled():
    template = _load("animal_form.pgm")
    assert template is not None and template.shape == (21, 22)


def test_present_form_releases_combat(monkeypatch):
    monkeypatch.setattr("app.pipelines.form_keep.find_template", lambda *_: Point(x=100, y=90))
    shared: dict = {}
    set_busy(shared, "form_keep", True)
    result = FormKeepPipeline(FormKeepConfig()).process(ctx(0.0, shared))
    assert keys(result) == []
    assert busy_reasons(shared) == set()
    assert "форма є" in result.status


def test_missing_form_presses_configured_key_and_blocks_combat(monkeypatch):
    monkeypatch.setattr("app.pipelines.form_keep.find_template", lambda *_: None)
    pipe = FormKeepPipeline(FormKeepConfig(key="7", confirm_missing=2, retry_after=5))
    shared: dict = {}
    assert keys(pipe.process(ctx(0.0, shared))) == []
    result = pipe.process(ctx(0.1, shared))
    assert keys(result) == ["7"]
    assert busy_reasons(shared) == {"form_keep"}
    assert keys(pipe.process(ctx(1.0, shared))) == [], "до retry_after не спамимо клавішу"


def test_form_does_not_block_navigation_or_service(monkeypatch):
    monkeypatch.setattr("app.pipelines.form_keep.find_template", lambda *_: None)
    pipe = FormKeepPipeline(FormKeepConfig(confirm_missing=1))
    shared: dict = {}
    set_busy(shared, "repair", True)
    result = pipe.process(ctx(0.0, shared))
    assert keys(result) == []
    assert busy_reasons(shared) == {"repair"}


def test_form_waits_until_location_is_ready_without_claiming_busy(monkeypatch):
    monkeypatch.setattr("app.pipelines.form_keep.find_template", lambda *_: None)
    shared = {SHARED_COMBAT_READY: False}
    result = FormKeepPipeline(FormKeepConfig(confirm_missing=1)).process(ctx(0.0, shared))
    assert keys(result) == []
    assert busy_reasons(shared) == set()
    assert "місця" in result.status
