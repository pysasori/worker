"""
Персонажі за ніком: сканер вікон, пул профілів, динамічні сесії.

Заголовок вікна гри в усіх клієнтів однаковий, тому персонажа впізнаємо по табличці
з ніком. Тут перевіряємо і саме читання (на справжніх кадрах), і те, що з прочитаним
робить бот: не плодить сміттєвих персонажів, не губить вікно, коли табличку перекрито,
і не зупиняє чужу сесію, коли десь відкрили ще один клієнт.
"""
from __future__ import annotations

import json
import threading

import pytest
from PIL import Image

from app.config.schemas import BotConfig, CharacterConfig, ProfileConfig, WindowConfig
from app.runtime.scanner import WindowScanner
from app.vision.nick import NickConfig, read_nick, same_nick
from tests.conftest import FIXTURES


# ---- читання ніка з екрана ---------------------------------------------------------
def test_nick_is_read_the_same_on_every_real_frame():
    """9 різних кадрів (бій, ремонт, вікна) — одне прочитання: нік не «пливе»."""
    cfg = NickConfig()
    names = ("player_82", "target_and_pet", "coords", "gear_repaired", "no_loot",
             "shop_open", "bag_only", "nav_closed", "ground_loot")
    reads = {read_nick(Image.open(FIXTURES / f"frame_1440_{n}.png").convert("RGB"), cfg)
             for n in names}
    assert reads == {"CEKXU"}, f"нік має читатись однаково й правильно, а вийшло {reads}"


def test_latin_nick_of_another_character_is_read_right():
    """З однією російською моделлю «BroWorker» читалось як «Вгои огКег» — стабільно, але хибно."""
    frame = Image.open(FIXTURES / "frame_1440_nick_broworker.png").convert("RGB")
    assert read_nick(frame, NickConfig()) == "BroWorker"


def test_nick_is_not_read_outside_the_game():
    """
    Екран завантаження чи вибору персонажа — статична картинка: OCR читав би з неї те
    саме сміття щоразу, і воно пройшло б перевірку «двічі підряд». Тому без HUD не читаємо.
    """
    splash = Image.new("RGB", (1440, 1080), (90, 80, 70))
    from PIL import ImageDraw
    ImageDraw.Draw(splash).text((130, 66), "Some Text", fill=(255, 255, 255))
    assert read_nick(splash, NickConfig()) == ""


def test_nick_of_a_blank_screen_is_empty():
    assert read_nick(Image.new("RGB", (1440, 1080), (10, 10, 10)), NickConfig()) == ""


def test_same_nick_tolerates_ocr_slips():
    assert same_nick("СЕКХИ", "СЕКХУ")            # одна літера хибна
    assert same_nick("CEKXU", "СЕКХИ")            # латиниця й кирилиця-двійник
    assert not same_nick("СЕКХИ", "Voron")


# ---- стабілізація ніка --------------------------------------------------------------
def scanner(reads: dict[int, list[str]], **kw) -> WindowScanner:
    """Сканер із підробленими вікнами: кожен виклик читає наступний рядок зі списку."""
    def reader_for(hwnd_holder):
        def reader(_image):
            seq = reads[hwnd_holder["hwnd"]]
            return seq.pop(0) if len(seq) > 1 else seq[0]
        return reader

    holder = {"hwnd": 0}
    sc = WindowScanner(
        find=lambda: list(reads),
        info=lambda h: (f"w{h}", (1440, 1080), True),
        grab=lambda h: holder.__setitem__("hwnd", h) or Image.new("RGB", (4, 4)),
        reader=reader_for(holder), **kw)
    return sc


def test_unknown_nick_needs_two_reads_in_a_row():
    """Екран вибору персонажа читається щоразу по-різному й не має ставати персонажем."""
    sc = scanner({1: ["Voron", "Voron", "Voron"]})
    assert sc.scan([])[0].nick == "", "одне читання — ще сміття"
    assert sc.scan([])[0].nick == "Voron", "друге підтвердило"


def test_garbage_never_becomes_a_character():
    sc = scanner({1: ["x1q", "zzk", "b7w", "m0p"]})
    assert [sc.scan([])[0].nick for _ in range(4)] == [""] * 4


def test_known_character_is_trusted_at_once_and_named_canonically():
    sc = scanner({1: ["СЕКХУ"]})                 # OCR хибнув в одній літері
    assert sc.scan(["СЕКХИ"])[0].nick == "СЕКХИ", "ключем лишається нік із конфіга"


def test_covered_nameplate_keeps_the_last_nick():
    """У бою табличку може перекрити вікно — сесія не має втрачати персонажа."""
    sc = scanner({1: ["СЕКХИ", "", "", "|"]})
    nicks = [sc.scan(["СЕКХИ"])[0].nick for _ in range(4)]
    assert nicks == ["СЕКХИ"] * 4


def test_accepted_nick_is_pinned_to_the_window():
    """
    Нік прилипає до hwnd: OCR, який схибив посеред бою, не може ні підмінити персонажа,
    ні розколоти його надвоє. Раніше нове читання міняло нік, і сесія «переїжджала».
    """
    sc = scanner({1: ["СЕКХИ", "Voron", "Voron", "Voron", "Voron"]})
    assert sc.scan(["СЕКХИ"])[0].nick == "СЕКХИ"
    for _ in range(4):
        found = sc.scan(["СЕКХИ"])[0]
        assert found.nick == "СЕКХИ" and found.pinned


def test_pinned_window_is_not_ocr_read_again():
    """Закріпленому вікну OCR не потрібен: економимо ~150 мс на кожному скані."""
    reads = {"n": 0}

    def reader(_img):
        reads["n"] += 1
        return "СЕКХИ"

    sc = scanner({1: ["СЕКХИ"]})
    sc.reader = reader
    sc.scan(["СЕКХИ"])
    sc.scan(["СЕКХИ"])
    sc.scan(["СЕКХИ"])
    assert reads["n"] == 1


def test_unpin_reads_the_nick_again():
    sc = scanner({1: ["СЕКХИ", "Voron", "Voron"]})
    sc.scan(["СЕКХИ"])
    sc.unpin(1)
    sc.scan([])
    assert sc.scan([])[0].nick == "Voron"


def test_manual_pin_wins_over_any_reading():
    sc = scanner({1: ["garbage", "garbage"]})
    sc.pin(1, "Voron")
    found = sc.scan([])[0]
    assert (found.nick, found.pinned, found.manual) == ("Voron", True, True)


def test_alias_maps_a_habitual_misreading_to_the_character():
    """Людина один раз пояснила, що «Bpo Work» — це BroWorker; далі так само хибне читання впізнається."""
    sc = scanner({1: ["Zxq Wrt", "Zxq Wrt"]})
    assert sc.scan({"BroWorker": ["Zxq Wrt"]})[0].nick == "BroWorker", "з першого читання, без підтвердження"


def test_two_clients_are_told_apart():
    sc = scanner({1: ["СЕКХИ"], 2: ["Voron", "Voron"]})
    sc.scan(["СЕКХИ"])
    found = sc.scan(["СЕКХИ"])
    assert {f.hwnd: f.nick for f in found} == {1: "СЕКХИ", 2: "Voron"}


def test_closed_window_is_forgotten():
    """Той самий hwnd може дістатись іншому клієнту — старий нік до нього не липне."""
    reads = {1: ["СЕКХИ"]}
    sc = scanner(reads)
    sc.scan([])
    assert 1 in sc._seen
    reads.clear()
    sc.scan([])
    assert 1 not in sc._seen


def test_minimized_window_is_listed_but_not_read():
    sc = scanner({1: ["СЕКХИ"]})
    sc.scan(["СЕКХИ"])
    sc.info = lambda h: (f"w{h}", (1440, 1080), False)
    found = sc.scan(["СЕКХИ"])[0]
    assert found.available is False and found.nick == "СЕКХИ"


# ---- конфіг: пул профілів ------------------------------------------------------------
def pool() -> BotConfig:
    return BotConfig(profiles={"фарм": ProfileConfig(), "лут": ProfileConfig()}, windows=[])


def test_character_with_unknown_profile_is_rejected():
    with pytest.raises(ValueError, match="невідомі профілі"):
        BotConfig(profiles={"фарм": ProfileConfig()},
                  characters={"Voron": CharacterConfig(profile="нема")})


def test_default_profile_must_exist():
    with pytest.raises(ValueError, match="за замовчуванням"):
        BotConfig(profiles={"фарм": ProfileConfig()}, default_profile="нема")


def test_new_characters_get_the_default_profile():
    cfg = pool()
    assert cfg.new_character_profile() == "фарм", "без вибору — перший у пулі"
    cfg.default_profile = "лут"
    assert cfg.new_character_profile() == "лут"


def test_window_is_built_from_character_and_hwnd():
    cfg = pool()
    cfg.characters["Voron"] = CharacterConfig(profile="лут", enabled=True, poll_interval=0.1,
                                              overrides={"attack": {"key": "f3"}})
    win = cfg.window_for("Voron", 4242)
    assert (win.name, win.profile, win.match.hwnd, win.poll_interval) == ("Voron", "лут", 4242, 0.1)
    assert cfg.specs_for(win) == [], "пайплайни беруться з профілю персонажа"


# ---- сервіс: нові персонажі й переїзд старих налаштувань -------------------------------
@pytest.fixture
def service(tmp_path):
    from app.web.service import BotService

    raw = {"profiles": {"фарм": {"client_size": [1440, 1080], "pipelines": []},
                        "лут": {"pipelines": []}},
           "windows": [{"name": "main", "profile": "лут", "enabled": True,
                        "poll_interval": 0.07, "overrides": {"a": {"x": 1}}}]}
    path = tmp_path / "windows.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    svc = BotService(path)
    return svc


def fake_scan(svc, reads: dict[int, list[str]]):
    svc.scanner = scanner(reads)


def test_legacy_window_settings_move_to_the_character(service):
    """Старе вікно «клієнт №0» → персонаж, який зараз у першому клієнті, зі своїм профілем."""
    fake_scan(service, {11: ["СЕКХИ", "СЕКХИ"]})
    service.scan_once()
    service.scan_once()
    char = service.config.characters["СЕКХИ"]
    assert (char.profile, char.enabled, char.poll_interval) == ("лут", True, 0.07)
    assert char.overrides == {"a": {"x": 1}}
    assert service.config.migrated_windows is True
    saved = json.loads(service.config_path.read_text(encoding="utf-8"))
    assert "СЕКХИ" in saved["characters"], "переїзд збережено на диск"


def test_legacy_enabled_flag_is_not_carried_over_when_clients_multiplied(service):
    """
    Було одне налаштоване вікно, а клієнтів стало два: невідомо, у якому з них той самий
    персонаж, тож бот не має сам стартувати в чужому клієнті — лише профіль переїжджає.
    """
    fake_scan(service, {11: ["Voron", "Voron"], 12: ["СЕКХИ", "СЕКХИ"]})
    service.scan_once()
    service.scan_once()
    char = service.config.characters["Voron"]              # він у клієнті №0, як і старе вікно
    assert char.profile == "лут" and char.enabled is False


def test_new_character_starts_disabled_with_the_default_profile(service):
    service.config.migrated_windows = True
    service.config.default_profile = "фарм"
    fake_scan(service, {21: ["Voron", "Voron"]})
    service.scan_once()
    service.scan_once()
    char = service.config.characters["Voron"]
    assert char.profile == "фарм" and char.enabled is False, \
        "новий клієнт не має сам почати ботити, поки його не ввімкнули"


def test_only_enabled_characters_get_sessions(service):
    service.config.migrated_windows = True
    service.config.characters["A"] = CharacterConfig(profile="фарм", enabled=True)
    service.config.characters["B"] = CharacterConfig(profile="лут", enabled=False)
    fake_scan(service, {31: ["A"], 32: ["B"]})
    service.scan_once()
    desired = service._desired()
    assert [(w.name, w.match.hwnd, w.profile) for w in desired] == [("A", 31, "фарм")] or \
        [(w.name, w.match.hwnd) for w in desired] == [("A", 31)]


def test_state_lists_online_and_offline_characters(service):
    service.config.migrated_windows = True
    service.config.characters["Ghost"] = CharacterConfig(profile="фарм")
    fake_scan(service, {41: ["Voron", "Voron"]})
    service.scan_once()
    service.scan_once()
    state = {w["name"]: w for w in service.state()["windows"]}
    assert state["Voron"]["online"] and state["Voron"]["hwnd"] == 41
    assert state["Ghost"]["online"] is False and state["Ghost"]["hwnd"] is None


def test_window_without_a_read_nick_is_addressable_by_hwnd(service):
    service.config.migrated_windows = True
    fake_scan(service, {51: ["x1q"]})
    service.scan_once()
    names = [w["name"] for w in service.state()["windows"]]
    assert names == ["hwnd:51"]
    assert service._hwnd("hwnd:51") == 51, "кадр і кнопки працюють і до того, як нік прочитався"


def test_two_clients_with_one_nick_do_not_collide(service):
    service.config.migrated_windows = True
    service.config.characters["A"] = CharacterConfig(profile="фарм", enabled=True)
    fake_scan(service, {61: ["A"], 62: ["A"]})
    service.scan_once()
    assert [w.match.hwnd for w in service._desired()] == [61], "друге вікно з тим самим ніком пропущено"


# ---- оркестратор: сесії на ходу --------------------------------------------------------
class FakeSession:
    """Замість справжньої сесії: просто чекає сигналу зупинки."""

    created: list[str] = []

    def __init__(self, window, bot_cfg, dry_run=False):
        self.cfg = window
        self.status = type("S", (), {"window": window.name, "connected": True, "tick": 0,
                                     "fps": 0.0, "last_error": "", "pipelines": {}})()
        self.stopped = threading.Event()
        FakeSession.created.append(window.name)

    def run_forever(self, stop):
        stop.wait(5)
        self.stopped.set()


@pytest.fixture
def orch(monkeypatch):
    import app.runtime.orchestrator as mod

    FakeSession.created = []
    monkeypatch.setattr(mod, "WindowSession", FakeSession)
    cfg = pool()
    o = mod.Orchestrator(cfg, windows=[])
    o.start()
    yield o, cfg
    o.stop()


def win(cfg: BotConfig, nick: str, hwnd: int, profile: str = "фарм") -> WindowConfig:
    cfg.characters[nick] = CharacterConfig(profile=profile, enabled=True)
    return cfg.window_for(nick, hwnd)


def test_new_window_starts_without_touching_the_others(orch):
    o, cfg = orch
    a = win(cfg, "A", 1)
    o.sync([a])
    first = o._slots["A"].session
    o.sync([a, win(cfg, "B", 2)])
    assert set(o._slots) == {"A", "B"}
    assert o._slots["A"].session is first, "працююча сесія не перезапускалась"
    assert FakeSession.created == ["A", "B"]


def test_closed_window_stops_only_its_own_session(orch):
    o, cfg = orch
    a, b = win(cfg, "A", 1), win(cfg, "B", 2)
    o.sync([a, b])
    gone = o._slots["B"].session
    o.sync([a])
    assert set(o._slots) == {"A"}
    assert gone.stopped.wait(2), "потік закритого вікна зупинився"
    assert not o._slots["A"].stop.is_set()


def test_changed_profile_or_hwnd_restarts_that_session(orch):
    o, cfg = orch
    o.sync([win(cfg, "A", 1)])
    o.sync([win(cfg, "A", 1, profile="лут")])
    o.sync([win(cfg, "A", 9, profile="лут")])
    assert FakeSession.created == ["A", "A", "A"]


def test_sync_after_stop_starts_nothing(orch):
    o, cfg = orch
    o.stop()
    assert o.sync([win(cfg, "A", 1)]) == ([], [])
    assert FakeSession.created == []


def test_disabled_character_is_dropped(orch):
    o, cfg = orch
    a = win(cfg, "A", 1)
    o.sync([a])
    cfg.characters["A"].enabled = False
    o.sync([cfg.window_for("A", 1)])
    assert o._slots == {}


def test_editing_one_profile_restarts_only_its_own_sessions(orch):
    """
    Кілька акаунтів: правка налаштувань профілю одного персонажа не має перезапускати
    інших — бот у них саме б'ється, і перезапуск скидає пета, ціль і повернення на місце.
    """
    o, cfg = orch
    a, b = win(cfg, "A", 1, "фарм"), win(cfg, "B", 2, "лут")
    o.sync([a, b])
    session_a, session_b = o._slots["A"].session, o._slots["B"].session

    from app.config.schemas import PipelineSpec
    new = cfg.model_copy(deep=True)
    new.profiles["лут"].pipelines.append(PipelineSpec(type="heal", config={"heal_below": 0.5}))
    o.set_config(new)
    o.sync([new.window_for("A", 1), new.window_for("B", 2)])
    assert o._slots["A"].session is session_a, "чужий профіль не змінився — сесія та сама"
    assert o._slots["B"].session is not session_b, "профіль змінився — сесія перезапущена"


# ---- ручне призначення вікна ----------------------------------------------------------
def test_assign_pins_the_window_and_learns_the_misreading(service):
    service.config.migrated_windows = True
    service.config.characters["BroWorker"] = CharacterConfig(profile="фарм")
    fake_scan(service, {71: ["Zxq Wrt"]})
    service.scan_once()
    service.scan_once()                                   # нік «Zxq Wrt» ще невідомий -> створено як новий
    service.assign_window("Zxq Wrt", "BroWorker")
    char = service.config.characters["BroWorker"]
    assert "Zxq Wrt" in char.aliases, "хибне читання запам'ятали як псевдонім"
    assert "Zxq Wrt" not in service.config.characters, "хибного «персонажа» прибрано"
    win = next(w for w in service.state()["windows"] if w["hwnd"] == 71)
    assert (win["nick"], win["pinned"], win["manual"]) == ("BroWorker", True, True)


def test_assign_keeps_a_character_the_user_has_configured(service):
    """Прибираємо лише те, що створив сканер і чого ніхто не чіпав."""
    service.config.migrated_windows = True
    service.config.characters["Zxq Wrt"] = CharacterConfig(profile="лут", enabled=True)
    fake_scan(service, {72: ["Zxq Wrt"]})
    service.scan_once()
    service.assign_window("Zxq Wrt", "BroWorker")
    assert "Zxq Wrt" in service.config.characters


def test_assign_refuses_a_nick_taken_by_another_window(service):
    service.config.migrated_windows = True
    service.config.characters["A"] = CharacterConfig(profile="фарм")
    fake_scan(service, {81: ["A"], 82: ["x1q"]})
    service.scan_once()
    from app.core.exceptions import BotError
    with pytest.raises(BotError, match="уже закріплено"):
        service.assign_window("hwnd:82", "A")


def test_window_without_a_nick_can_be_assigned_by_hwnd(service):
    service.config.migrated_windows = True
    fake_scan(service, {91: [""]})
    service.scan_once()
    assert [w["name"] for w in service.state()["windows"]] == ["hwnd:91"]
    service.assign_window("hwnd:91", "Voron")
    assert service.config.characters["Voron"].enabled is False
    assert [w["name"] for w in service.state()["windows"]] == ["Voron"]


# ---- налаштування програми ------------------------------------------------------------
def test_legacy_top_level_scan_interval_moves_into_settings():
    cfg = BotConfig(profiles={"p": ProfileConfig()}, scan_interval=9)
    assert cfg.settings.scan_interval == 9


def test_settings_have_sane_defaults_and_bounds():
    from app.config.schemas import AppSettings

    s = AppSettings()
    assert (s.autostart, s.open_browser, s.guard, s.port) == (False, True, True, 8765)
    with pytest.raises(ValueError):
        AppSettings(port=70000)
    with pytest.raises(ValueError):
        AppSettings(scan_interval=0)


def test_settings_schema_is_served_for_the_page(tmp_path):
    from fastapi.testclient import TestClient

    from app.web.server import create_app

    raw = {"profiles": {"p": {}}, "windows": [], "migrated_windows": True}
    path = tmp_path / "windows.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    client = TestClient(create_app(path, scan=False))
    schema = client.get("/api/settings-schema").json()
    assert {"autostart", "scan_interval", "guard", "nick", "port"} <= set(schema["properties"])
    saved = client.get("/api/config").json()
    assert saved["settings"]["scan_interval"] == 5.0


# ---- наглядач і автостарт --------------------------------------------------------------
@pytest.fixture
def running(service, monkeypatch):
    """Сервіс із «запущеним» оркестратором на підроблених сесіях."""
    import app.runtime.orchestrator as mod

    FakeSession.created = []
    monkeypatch.setattr(mod, "WindowSession", FakeSession)
    service.config.migrated_windows = True
    service.config.characters["A"] = CharacterConfig(profile="фарм", enabled=True)
    fake_scan(service, {31: ["A"]})
    service.start()
    yield service
    service.stop()


def set_tick(svc, name, tick, connected=True):
    st = svc.orchestrator._slots[name].session.status
    st.tick, st.connected = tick, connected


def test_guard_restarts_a_session_whose_frames_stopped(running):
    svc = running
    svc.config.settings.stall_after = 30
    set_tick(svc, "A", 100)
    assert svc.guard_once(now=1000.0) == []
    set_tick(svc, "A", 100)                                 # 20 с без руху — ще терпимо
    assert svc.guard_once(now=1020.0) == []
    before = svc.orchestrator._slots["A"].session
    assert svc.guard_once(now=1040.0) == ["A"], "30+ с без руху — перезапуск"
    assert svc.orchestrator._slots["A"].session is not before


def test_one_window_can_be_stopped_and_started_without_touching_the_other(service, monkeypatch):
    import app.runtime.orchestrator as mod

    FakeSession.created = []
    monkeypatch.setattr(mod, "WindowSession", FakeSession)
    service.config.migrated_windows = True
    service.config.characters["A"] = CharacterConfig(profile="фарм", enabled=True)
    service.config.characters["B"] = CharacterConfig(profile="лут", enabled=True)
    fake_scan(service, {31: ["A"], 32: ["B"]})
    try:
        service.start()
        assert set(service.orchestrator._slots) == {"A", "B"}

        service.stop_window("A")
        assert set(service.orchestrator._slots) == {"B"}
        service.scan_once()
        assert set(service.orchestrator._slots) == {"B"}, \
            "фоновий сканер не має піднімати локально зупинене вікно"

        service.start_window("A")
        assert set(service.orchestrator._slots) == {"A", "B"}
    finally:
        service.stop()


def test_one_window_can_start_while_whole_bot_is_stopped(service, monkeypatch):
    import app.runtime.orchestrator as mod

    FakeSession.created = []
    monkeypatch.setattr(mod, "WindowSession", FakeSession)
    service.config.migrated_windows = True
    service.config.characters["A"] = CharacterConfig(profile="фарм", enabled=True)
    service.config.characters["B"] = CharacterConfig(profile="лут", enabled=True)
    fake_scan(service, {31: ["A"], 32: ["B"]})
    try:
        service.start_window("A")
        assert set(service.orchestrator._slots) == {"A"}
    finally:
        service.stop()


def test_guard_does_not_touch_a_moving_or_disconnected_session(running):
    svc = running
    for now, tick in ((1000.0, 1), (1100.0, 2), (1200.0, 3)):
        set_tick(svc, "A", tick)
        assert svc.guard_once(now=now) == []
    set_tick(svc, "A", 3, connected=False)                  # вікна нема — це не зависання
    assert svc.guard_once(now=1300.0) == []
    assert svc.guard_once(now=1500.0) == []


def test_guard_can_be_switched_off(running):
    svc = running
    svc.config.settings.guard = False
    set_tick(svc, "A", 5)
    svc.guard_once(now=1000.0)
    assert svc.guard_once(now=9000.0) == []


def test_autostart_starts_the_bot_once_and_respects_a_manual_stop(service, monkeypatch):
    import app.runtime.orchestrator as mod

    monkeypatch.setattr(mod, "WindowSession", FakeSession)
    service.config.migrated_windows = True
    service.config.settings.autostart = True
    service.config.characters["A"] = CharacterConfig(profile="фарм", enabled=True)
    fake_scan(service, {41: ["A"]})
    try:
        service._maybe_autostart()
        assert service.is_running()
        service.stop()                                      # людина зупинила
        service._maybe_autostart()
        assert not service.is_running(), "автостарт спрацьовує лише раз"
    finally:
        service.stop()


def test_autostart_is_off_by_default(service):
    service._maybe_autostart()
    assert not service.is_running()
