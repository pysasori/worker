"""Тести рантайму: кадр знімається один раз за тік, дії реально доходять до вводу."""
from __future__ import annotations

from PIL import Image

from app.capture.abstract import CapturePort
from app.config.loader import load_config
from app.core.settings import settings
from app.input.abstract import NullInput
from app.pipelines.actions import PressKey, Wait
from app.pipelines.shared import combat_ready
from app.runtime.executor import ActionExecutor
from app.runtime.session import WindowSession


class FakeCapture(CapturePort):
    def __init__(self, image: Image.Image) -> None:
        self.image = image
        self.grabs = 0

    def grab(self) -> Image.Image:
        self.grabs += 1
        return self.image

    def client_size(self) -> tuple[int, int]:
        return self.image.size

    def is_available(self) -> bool:
        return True


def make_session(image: Image.Image) -> tuple[WindowSession, FakeCapture, NullInput]:
    config = load_config(settings.CONFIG_PATH)
    # фільтр мобів залежить від того, кого зараз обрано в конструкторі;
    # рантайм перевіряємо без нього, інакше тест ламався б від чужих налаштувань
    for spec in config.profiles[config.windows[0].profile].pipelines:
        if spec.type == "target_search":
            spec.config["names"] = {**spec.config.get("names", {}), "enabled": False}
    session = WindowSession(config.windows[0], config, dry_run=False)
    # Загальні runtime-тести моделюють уже перевірене місце фарму. Окремо стартовий
    # бойовий шлюз перевіряється в test_return_home/test_pipelines.
    position = next((p for p in session.pipelines if p.name == "position"), None)
    if position is not None and position.home is not None:
        position.pos = position.home.model_copy(update={"known": True})
    capture, sink = FakeCapture(image), NullInput()
    session.capture = capture
    session.input = sink
    session.executor = ActionExecutor(sink, dry_run=False)
    session.status.connected = True
    return session, capture, sink


def test_one_frame_per_tick_for_all_pipelines(real_frame):
    from app.config.loader import load_config
    from app.core.settings import settings

    config = load_config(settings.CONFIG_PATH)
    session, capture, _ = make_session(real_frame)
    session.tick()
    assert capture.grabs == 1, "кадр має зніматись один раз і роздаватись усім пайплайнам"
    # рівно стільки, скільки увімкнено в конфізі (ремонт може бути тимчасово вимкнений)
    enabled = [sp for sp in config.specs_for(config.windows[0]) if sp.enabled]
    assert len(session.pipelines) == len(enabled)


def test_combat_tail_runs_after_startup_checks(real_frame):
    session, _, _ = make_session(real_frame)
    names = [pipeline.name for pipeline in session.pipelines]
    assert names[-1] == "attack"
    assert names.index("return_home") < names.index("target_search") < names.index("attack")


def test_disabled_return_home_skips_location_gate():
    config = load_config(settings.CONFIG_PATH)
    window = config.windows[0]
    for spec in config.profiles[window.profile].pipelines:
        if spec.type == "return_home":
            spec.enabled = False
    session = WindowSession(window, config)
    names = [pipeline.name for pipeline in session.pipelines]
    assert "return_home" not in names
    assert {"target_search", "attack"} <= set(names)
    assert combat_ready(session.shared), "без повернення бій не має чекати перевірку місця"


def test_actions_reach_the_input_layer(real_frame):
    session, _, sink = make_session(real_frame)
    session.tick()
    session.tick()  # другий кадр підтверджує ціль -> атака
    pressed = [value for kind, value in sink.sent if kind == "press"]
    assert "f1" in pressed
    assert "1" in pressed


def test_status_collects_every_pipeline(real_frame):
    session, _, _ = make_session(real_frame)
    status = session.tick()
    assert set(status.pipelines) == {p.name for p in session.pipelines}
    assert {"pet_heal", "pet_feed", "target_search", "attack", "loot"} <= set(status.pipelines)


def test_dry_run_sends_nothing(real_frame):
    session, _, sink = make_session(real_frame)
    session.executor = ActionExecutor(sink, dry_run=True)
    session.tick()
    session.tick()
    assert sink.sent == []


def test_executor_waits_but_sends_nothing_for_wait():
    sink = NullInput()
    executor = ActionExecutor(sink, dry_run=False)
    done = executor.run([Wait(0.01, reason="труп"), PressKey("f2", reason="лут")])
    assert sink.sent == [("press", "f2")]
    assert "пауза" in done[0]
