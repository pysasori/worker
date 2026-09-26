"""Втримання висоти: пробіл угору, Z вниз, і мовчання, коли клавіші не діють."""
from __future__ import annotations

from PIL import Image

from app.pipelines.actions import PressKey
from app.pipelines.altitude_hold import AltitudeHoldConfig, AltitudeHoldPipeline
from app.pipelines.base import Frame, PipelineContext
from app.pipelines.shared import SHARED_ALT, Altitude, set_busy

BLANK = Image.new("RGB", (60, 60))


def ctx_for(z: int | None, ts: float, shared: dict) -> PipelineContext:
    shared[SHARED_ALT] = Altitude(z=z or 0, known=z is not None, at=ts)
    return PipelineContext(window="t", frame=Frame(image=BLANK, ts=ts), shared=shared)


def keys(res) -> list[str]:
    return [a.key for a in res.actions if isinstance(a, PressKey)]


def test_remembers_the_starting_height():
    pipe, shared = AltitudeHoldPipeline(AltitudeHoldConfig()), {}
    assert keys(pipe.process(ctx_for(30, 0.0, shared))) == []
    assert pipe.target == 30, "0 у налаштуваннях = тримати ту висоту, з якої почали"


def test_goes_down_when_too_high():
    cfg = AltitudeHoldConfig(target=25, tolerance=2)
    pipe, shared = AltitudeHoldPipeline(cfg), {}
    res = pipe.process(ctx_for(31, 0.0, shared))
    assert keys(res) == [cfg.down_key]
    assert "вниз" in res.status


def test_goes_up_when_too_low():
    cfg = AltitudeHoldConfig(target=25, tolerance=2)
    pipe, shared = AltitudeHoldPipeline(cfg), {}
    assert keys(pipe.process(ctx_for(19, 0.0, shared))) == [cfg.up_key]


def test_small_wobble_is_left_alone():
    cfg = AltitudeHoldConfig(target=25, tolerance=2)
    pipe, shared = AltitudeHoldPipeline(cfg), {}
    for z in (24, 25, 26, 27, 23):
        assert keys(pipe.process(ctx_for(z, 0.0, shared))) == [], f"{z} у межах допуску"


def test_presses_are_not_spammed():
    cfg = AltitudeHoldConfig(target=25, tolerance=1, interval=1.0)
    pipe, shared = AltitudeHoldPipeline(cfg), {}
    pressed = []
    for i in range(10):
        pressed += keys(pipe.process(ctx_for(30 - i // 3, i * 0.2, shared)))
    assert len(pressed) <= 2, "поправка раз на секунду, а не щокадру"


def test_stops_when_the_keys_do_nothing():
    """
    На землі висота задана рельєфом: скільки не тисни, число не зміниться.
    Бот має це помітити і замовкнути, а не молотити пробіл усю ніч.
    """
    cfg = AltitudeHoldConfig(target=40, tolerance=1, interval=0.0, give_up_after=4)
    pipe, shared = AltitudeHoldPipeline(cfg), {}
    pressed, warned = [], []
    for i in range(12):
        res = pipe.process(ctx_for(29, i * 0.5, shared))
        pressed += keys(res)
        warned += [e for e in res.events if "не міняється" in e]
    assert len(pressed) == cfg.give_up_after
    assert len(warned) == 1


def test_waits_out_the_repair():
    cfg = AltitudeHoldConfig(target=25, tolerance=1, interval=0.0)
    pipe, shared = AltitudeHoldPipeline(cfg), {}
    set_busy(shared, "repair", True)
    res = pipe.process(ctx_for(35, 0.0, shared))
    assert keys(res) == [] and "чекаю: repair" in res.status
    set_busy(shared, "repair", False)
    assert keys(pipe.process(ctx_for(35, 1.0, shared))) == [cfg.down_key]


def test_silent_without_altitude():
    pipe, shared = AltitudeHoldPipeline(AltitudeHoldConfig()), {}
    assert pipe.process(ctx_for(None, 0.0, shared)).actions == []


# ---- зіпсуте читання висоти -------------------------------------------------------
def alt_ctx(ts: float, shared: dict):
    from PIL import Image
    from app.pipelines.base import Frame, PipelineContext

    return PipelineContext(window="t", frame=Frame(image=Image.new("RGB", (1440, 1080)), ts=ts),
                           shared=shared)


def test_single_garbage_number_is_ignored(monkeypatch):
    """
    «22» інколи читається як «6» — одна загублена цифра. Раніше це число йшло далі
    як справжня висота, осідало в «Поверненні на місце» як земля, і бот усю ніч
    «сідав» на рівному місці, тобто сідав на літаючого звіра.
    """
    import app.pipelines.altitude as mod
    from app.pipelines.altitude import AltitudeConfig, AltitudePipeline
    from app.pipelines.shared import SHARED_ALT

    seq = iter([[22], [22], [6], [23], [22]])
    monkeypatch.setattr(mod, "read_numbers", lambda *a, **kw: next(seq))
    pipe, shared = AltitudePipeline(AltitudeConfig(read_every=0.0)), {}
    seen = []
    for i in range(5):
        pipe.process(alt_ctx(float(i), shared))
        seen.append(shared[SHARED_ALT].z)
    assert seen == [22, 22, 22, 23, 22], f"«6» мало відпасти, а вийшло {seen}"


def test_a_real_fall_is_believed(monkeypatch):
    """А от падіння з висоти — це рівний ряд, і йому віримо з другого ж читання."""
    import app.pipelines.altitude as mod
    from app.pipelines.altitude import AltitudeConfig, AltitudePipeline
    from app.pipelines.shared import SHARED_ALT

    seq = iter([[70], [45], [44], [23], [22]])
    monkeypatch.setattr(mod, "read_numbers", lambda *a, **kw: next(seq))
    pipe, shared = AltitudePipeline(AltitudeConfig(read_every=0.0)), {}
    seen = []
    for i in range(5):
        pipe.process(alt_ctx(float(i), shared))
        seen.append(shared[SHARED_ALT].z)
    assert seen[-1] == 22 and 44 in seen, f"падіння мало дочитатись, а вийшло {seen}"
