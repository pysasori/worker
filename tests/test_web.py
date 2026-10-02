"""Смоук-тести веб-API: конструктор має віддавати каталог, конфіг і стан."""
from __future__ import annotations

import copy
import shutil

import pytest
from fastapi.testclient import TestClient

from app.core.settings import settings
from app.web.server import create_app


@pytest.fixture(scope="module")
def client(tmp_path_factory) -> TestClient:
    """Працюємо на копії конфіга: тести не мають переписувати робочий config/windows.json."""
    tmp = tmp_path_factory.mktemp("config") / "windows.json"
    shutil.copy(settings.CONFIG_PATH, tmp)
    return TestClient(create_app(tmp))


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "AutoBot" in r.text


def test_profile_management_is_visible_in_ui(client):
    script = client.get("/static/app.js")
    assert script.status_code == 200
    assert "створити профіль" in script.text
    assert "перейменувати" in script.text
    assert "профіль цього персонажа" in script.text
    assert "if (S.dirty) await saveConfig()" in script.text
    assert "/${action}`" in script.text
    assert "лише цього персонажа" in script.text
    assert "призначити вибраним" not in script.text
    assert "для нових персонажів" not in script.text
    assert "orderedPipelineItems" in script.text
    assert "обрати все" in script.text


def test_profile_creation_is_never_disabled(client):
    """
    Живий випадок: «створити профіль» була сірою (disabled), поки не вибрано персонажа —
    без жодного пояснення чому. Кнопка більше не блокується цією умовою; без персонажа
    новий профіль просто лягає в пул, а призначається пізніше вибором у картці.
    """
    script = client.get("/static/app.js").text
    assert "create.disabled = !currentChar()" not in script
    assert "видалити" in script, "пул без видалення росте назавжди"
    assert "поки нікому не призначено" in script


def test_catalog_lists_pipelines_with_wiring(client):
    catalog = client.get("/api/catalog").json()
    types = {c["type"]: c for c in catalog}
    assert {"target_search", "attack", "loot", "pet_heal", "periodic_keys"} <= set(types)
    assert types["attack"]["requires"] == ["target"]
    assert types["target_search"]["provides"] == ["target"]
    assert types["repair"]["run_order"] < types["target_search"]["run_order"]
    assert types["target_search"]["run_order"] < types["loot"]["run_order"] < types["attack"]["run_order"]
    assert "properties" in types["loot"]["schema"], "схема потрібна для автоформи"


def test_loot_schema_exposes_square_and_interval(client):
    loot = next(c for c in client.get("/api/catalog").json() if c["type"] == "loot")
    props = loot["schema"]["properties"]
    assert {"key", "max_presses", "interval", "check"} <= set(props)
    check = loot["schema"]["$defs"]["GroundCheckConfig"]["properties"]
    assert {"size", "offset_x", "offset_y", "exclude", "enabled"} <= set(check)


def test_config_roundtrip(client):
    config = client.get("/api/config").json()
    assert config["windows"], "у конфізі має бути хоч одне вікно"
    saved = client.put("/api/config", json=config)
    assert saved.status_code == 200
    assert saved.json()["windows"][0]["name"] == config["windows"][0]["name"]


def test_named_profile_can_be_assigned_to_multiple_characters(client):
    config = client.get("/api/config").json()
    source = next(iter(config["profiles"]))
    config["profiles"]["фарм для двох"] = copy.deepcopy(config["profiles"][source])
    config["characters"]["Вікно А"] = {"profile": "фарм для двох"}
    config["characters"]["Вікно Б"] = {"profile": "фарм для двох"}
    saved = client.put("/api/config", json=config)
    assert saved.status_code == 200
    body = saved.json()
    assert body["characters"]["Вікно А"]["profile"] == "фарм для двох"
    assert body["characters"]["Вікно Б"]["profile"] == "фарм для двох"


def test_bad_config_is_rejected(client):
    r = client.put("/api/config", json={"profiles": {}, "windows": [{"name": "x", "profile": "нема"}]})
    assert r.status_code == 400
    assert "профіл" in r.json()["detail"].lower() or "profile" in r.json()["detail"].lower()


def test_state_reports_stopped_bot(client):
    state = client.get("/api/state").json()
    assert state["running"] is False
    assert isinstance(state["windows"], list) and "scanned_at" in state


def test_press_unknown_window_is_400(client):
    r = client.post("/api/windows/нема-такого/press", json={"key": "f1"})
    assert r.status_code == 400


def test_log_endpoint_returns_recent_events(client):
    """
    Живий бот пише через logging, а не напряму в API — тому подія в логах має
    з'явитись і тут, незалежно від того, чи хтось перенаправляв stdout у файл.
    """
    import logging

    logging.getLogger("СЕКХИ").info("тестова подія для перевірки логу")
    events = client.get("/api/log", params={"window": "СЕКХИ"}).json()
    assert events and events[-1]["message"] == "тестова подія для перевірки логу"
    assert events[-1]["window"] == "СЕКХИ"


def test_log_endpoint_filters_by_level(client):
    import logging

    log = logging.getLogger("рівень-тест")
    log.info("звичайна подія")
    log.warning("!! тривожна подія")
    warn_only = client.get("/api/log", params={"window": "рівень-тест", "level": "WARNING"}).json()
    assert all(e["level"] == "WARNING" for e in warn_only)
    assert any("тривожна" in e["message"] for e in warn_only)
    assert not any("звичайна" in e["message"] for e in warn_only)


def test_loot_config_is_none_for_an_unknown_window(tmp_path):
    """
    Живий випадок: вікно ще без закріпленого ніка («hwnd:N») — персонажа в конфізі
    нема. Раніше _loot_config тут падав з BotError, і той валив увесь запит кадру:
    квадрат лута увімкнений у прев'ю за замовчуванням, тому щойно відкрита сторінка
    одразу показувала «вікно гри недоступне» для будь-якого свіжого клієнта.
    """
    import json

    from app.web.service import BotService

    raw = {"profiles": {"p": {"pipelines": [{"type": "loot", "enabled": True, "config": {}}]}},
          "windows": []}
    path = tmp_path / "windows.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    svc = BotService(path)
    assert svc._loot_config("hwnd:12345") is None


def test_loot_overlay_leaves_the_frame_untouched_for_an_unknown_window(tmp_path):
    import json

    from PIL import Image

    from app.web.service import BotService

    raw = {"profiles": {"p": {"pipelines": [{"type": "loot", "enabled": True, "config": {}}]}},
          "windows": []}
    path = tmp_path / "windows.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    svc = BotService(path)
    frame = Image.new("RGB", (40, 30), (1, 2, 3))
    out = svc._draw_loot_overlay("hwnd:12345", frame)
    assert list(out.getdata()) == list(frame.getdata())


def test_removed_character_cannot_be_re_adopted_before_saving(client):
    """
    Живий випадок: «видалення вікон у вебі не працює». Прибраного персонажа сервер, де
    видалення ще не збережене, далі віддавав у /api/state, і adoptNewCharacters()
    щосекунди повертав його назад. Сторінка тепер пам'ятає прибраних до збереження.
    """
    script = client.get("/static/app.js").text
    assert "S.removed" in script
    assert "!S.removed.has(e.nick)" in script, "прибраних не підхоплюємо назад"
    assert "S.removed.clear()" in script, "після збереження забуваємо"
    assert "function removeCharacter" in script
    assert "window-remove" in script, "× прямо на картці офлайн-вікна"


def test_saving_without_a_character_removes_it_for_good(tmp_path):
    import json

    from app.web.service import BotService

    raw = {"profiles": {"p": {"pipelines": []}}, "windows": [], "migrated_windows": True,
           "characters": {"Gone": {"profile": "p"}, "Stay": {"profile": "p"}}}
    path = tmp_path / "windows.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    svc = BotService(path)
    cfg = svc.get_config()
    del cfg["characters"]["Gone"]
    svc.update_config(cfg)
    assert [w["name"] for w in svc.state()["windows"]] == ["Stay"]
    assert "Gone" not in json.loads(path.read_text(encoding="utf-8"))["characters"]
