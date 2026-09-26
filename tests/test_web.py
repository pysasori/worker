"""Смоук-тести веб-API: конструктор має віддавати каталог, конфіг і стан."""
from __future__ import annotations

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


def test_catalog_lists_pipelines_with_wiring(client):
    catalog = client.get("/api/catalog").json()
    types = {c["type"]: c for c in catalog}
    assert {"target_search", "attack", "loot", "pet_heal", "periodic_keys"} <= set(types)
    assert types["attack"]["requires"] == ["target"]
    assert types["target_search"]["provides"] == ["target"]
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
