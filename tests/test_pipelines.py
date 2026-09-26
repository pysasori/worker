"""Тести логіки пайплайнів: кадр на вході — дії на виході, без гри й без клавіш."""
from __future__ import annotations

import pytest
from PIL import Image

from app.core.exceptions import ConfigError
from app.pipelines.actions import PressKey, Wait
from app.pipelines.attack import AttackPipeline
from app.pipelines.base import Frame, PipelineContext
from app.pipelines.loot import LootPipeline
from app.pipelines.periodic import PeriodicKeysConfig, PeriodicKeysPipeline
from app.pipelines.pet import PetHealPipeline
from app.pipelines.registry import build_pipeline, known_types
from app.pipelines.shared import SHARED_TARGET, read_target
from app.pipelines.target_search import TargetSearchPipeline
from app.pipelines.wiring import order_pipelines
from tests.conftest import set_bar_fill

TARGET_BAR = (605, 208, range(9, 17))
PET_BAR = (46, 82, range(188, 194))


def ctx_for(image: Image.Image, ts: float, shared: dict | None = None) -> PipelineContext:
    return PipelineContext(window="test", frame=Frame(image=image, ts=ts),
                           shared={} if shared is None else shared)


def keys_of(result) -> list[str]:
    return [a.key for a in result.actions if isinstance(a, PressKey)]


# ---- target_search: єдиний, хто дивиться на екран ---------------------------
def test_search_confirms_target_after_two_frames(real_frame, search_cfg):
    pipe = TargetSearchPipeline(search_cfg)
    shared: dict = {}
    pipe.process(ctx_for(real_frame, 0.0, shared))
    assert not read_target(shared).present, "одного кадру мало для підтвердження"
    pipe.process(ctx_for(real_frame, 0.1, shared))
    info = read_target(shared)
    assert info.present and info.bar.percent == 100


def test_search_taps_target_key_when_no_target(real_frame, search_cfg):
    empty = set_bar_fill(real_frame, *TARGET_BAR, 0.0)
    pipe = TargetSearchPipeline(search_cfg)
    assert keys_of(pipe.process(ctx_for(empty, 0.0))) == [search_cfg.target_key]
    assert keys_of(pipe.process(ctx_for(empty, 0.2))) == [], "не спамити Tab частіше за retarget_delay"
    assert keys_of(pipe.process(ctx_for(empty, 2.0))) == [search_cfg.target_key]


def test_search_reports_death_and_holds_tab_that_frame(real_frame, search_cfg):
    """На кадрі смерті Tab не тиснемо — інакше нова ціль перебила б лут з трупа."""
    dead = set_bar_fill(real_frame, *TARGET_BAR, 0.0)
    pipe = TargetSearchPipeline(search_cfg)
    shared: dict = {}
    pipe.process(ctx_for(real_frame, 0.0, shared))
    pipe.process(ctx_for(real_frame, 0.1, shared))
    death = pipe.process(ctx_for(dead, 1.0, shared))
    assert read_target(shared).died_now
    assert keys_of(death) == []
    after = pipe.process(ctx_for(dead, 1.2, shared))
    assert keys_of(after) == [search_cfg.target_key], "наступним кадром беремо нову ціль"
    assert not read_target(shared).died_now, "подія смерті живе один кадр"


def test_search_can_only_watch(real_frame, search_cfg):
    cfg = search_cfg.model_copy(update={"target_key": None})
    empty = set_bar_fill(real_frame, *TARGET_BAR, 0.0)
    res = TargetSearchPipeline(cfg).process(ctx_for(empty, 0.0))
    assert res.actions == []


# ---- attack: живе з дошки ---------------------------------------------------
def attack_ctx(real_frame, search_cfg, ts: float, shared: dict, image=None):
    """Один тік: спершу пошук кладе стан, потім його читає атака."""
    ctx = ctx_for(image if image is not None else real_frame, ts, shared)
    shared["_search"].process(ctx)
    return ctx


def test_attack_hits_live_target(real_frame, search_cfg, attack_cfg):
    shared = {"_search": TargetSearchPipeline(search_cfg)}
    attack = AttackPipeline(attack_cfg)
    attack.process(attack_ctx(real_frame, search_cfg, 0.0, shared))     # ціль ще не підтверджена
    res = attack.process(attack_ctx(real_frame, search_cfg, 0.1, shared))
    assert keys_of(res) == [attack_cfg.key]


def test_attack_respects_interval(real_frame, search_cfg, attack_cfg):
    shared = {"_search": TargetSearchPipeline(search_cfg)}
    attack = AttackPipeline(attack_cfg)
    attack.process(attack_ctx(real_frame, search_cfg, 0.0, shared))
    attack.process(attack_ctx(real_frame, search_cfg, 0.1, shared))     # тут б'є
    assert keys_of(attack.process(attack_ctx(real_frame, search_cfg, 1.0, shared))) == []
    assert keys_of(attack.process(attack_ctx(real_frame, search_cfg, 5.0, shared))) == [attack_cfg.key]


def test_attack_silent_without_target(real_frame, search_cfg, attack_cfg):
    empty = set_bar_fill(real_frame, *TARGET_BAR, 0.0)
    shared = {"_search": TargetSearchPipeline(search_cfg)}
    res = AttackPipeline(attack_cfg).process(attack_ctx(real_frame, search_cfg, 0.0, shared, empty))
    assert keys_of(res) == []


def test_attack_hits_new_target_immediately(real_frame, search_cfg, attack_cfg):
    """Після зміни цілі перший удар іде одразу, не чекаючи інтервалу."""
    empty = set_bar_fill(real_frame, *TARGET_BAR, 0.0)
    shared = {"_search": TargetSearchPipeline(search_cfg)}
    attack = AttackPipeline(attack_cfg)
    attack.process(attack_ctx(real_frame, search_cfg, 0.0, shared))
    attack.process(attack_ctx(real_frame, search_cfg, 0.1, shared))     # удар по першій цілі
    attack.process(attack_ctx(real_frame, search_cfg, 0.5, shared, empty))   # померла
    attack.process(attack_ctx(real_frame, search_cfg, 0.7, shared))     # нова ціль, кадр 1
    res = attack.process(attack_ctx(real_frame, search_cfg, 0.8, shared))
    assert keys_of(res) == [attack_cfg.key]


def test_attack_without_search_pipeline_is_quiet(real_frame, attack_cfg):
    res = AttackPipeline(attack_cfg).process(ctx_for(real_frame, 0.0))
    assert keys_of(res) == []
    assert "нема даних" in res.status


# ---- loot -------------------------------------------------------------------
def loot_run(loot, search_cfg, real_frame, dead, shared, start: float, ticks: int = 12) -> list[str]:
    """Прокрутити кадри після смерті й зібрати всі натискання."""
    pressed: list[str] = []
    for i in range(ticks):
        res = loot.process(attack_ctx(real_frame, search_cfg, start + i * 0.2, shared, dead))
        pressed += keys_of(res)
    return pressed


def test_loot_presses_up_to_max_when_ground_has_labels(real_frame, search_cfg, loot_cfg, loot_frame):
    """Натискання розкидані по кадрах, а не пачкою — між ними перевіряється земля."""
    shared = {"_search": TargetSearchPipeline(search_cfg)}
    loot = LootPipeline(loot_cfg)
    loot.process(attack_ctx(real_frame, search_cfg, 0.0, shared))
    loot.process(attack_ctx(real_frame, search_cfg, 0.1, shared))
    dead = set_bar_fill(loot_frame, *TARGET_BAR, 0.0)
    loot.process(attack_ctx(real_frame, search_cfg, 1.0, shared, dead))   # кадр смерті
    pressed = loot_run(loot, search_cfg, real_frame, dead, shared, 1.4)
    assert pressed == [loot_cfg.key] * loot_cfg.max_presses


def test_loot_stops_early_when_ground_empty(real_frame, search_cfg, loot_cfg):
    """Нічого не лежить — не витрачаємо решту натискань."""
    dead = set_bar_fill(real_frame, *TARGET_BAR, 0.0)
    shared = {"_search": TargetSearchPipeline(search_cfg)}
    loot = LootPipeline(loot_cfg)
    loot.process(attack_ctx(real_frame, search_cfg, 0.0, shared))
    loot.process(attack_ctx(real_frame, search_cfg, 0.1, shared))
    loot.process(attack_ctx(real_frame, search_cfg, 1.0, shared, dead))
    assert loot_run(loot, search_cfg, real_frame, dead, shared, 1.4) == []


def test_loot_without_check_presses_blindly(real_frame, search_cfg, loot_cfg):
    cfg = loot_cfg.model_copy(deep=True)
    cfg.check.enabled = False
    dead = set_bar_fill(real_frame, *TARGET_BAR, 0.0)
    shared = {"_search": TargetSearchPipeline(search_cfg)}
    loot = LootPipeline(cfg)
    loot.process(attack_ctx(real_frame, search_cfg, 0.0, shared))
    loot.process(attack_ctx(real_frame, search_cfg, 0.1, shared))
    loot.process(attack_ctx(real_frame, search_cfg, 1.0, shared, dead))
    assert loot_run(loot, search_cfg, real_frame, dead, shared, 1.4) == [cfg.key] * cfg.max_presses


def test_loot_respects_interval(real_frame, search_cfg, loot_cfg, loot_frame):
    shared = {"_search": TargetSearchPipeline(search_cfg)}
    loot = LootPipeline(loot_cfg)
    loot.process(attack_ctx(real_frame, search_cfg, 0.0, shared))
    loot.process(attack_ctx(real_frame, search_cfg, 0.1, shared))
    dead = set_bar_fill(loot_frame, *TARGET_BAR, 0.0)
    loot.process(attack_ctx(real_frame, search_cfg, 1.0, shared, dead))
    first = loot.process(attack_ctx(real_frame, search_cfg, 1.0 + loot_cfg.corpse_delay, shared, dead))
    assert keys_of(first) == [loot_cfg.key]
    too_soon = loot.process(attack_ctx(real_frame, search_cfg, 1.0 + loot_cfg.corpse_delay + 0.05, shared, dead))
    assert keys_of(too_soon) == []


def test_loot_blocks_retarget_while_collecting(real_frame, search_cfg, loot_cfg, loot_frame):
    """Поки лут збирається, пошук не тисне Tab — інакше нова ціль перебила б підбір."""
    search = TargetSearchPipeline(search_cfg)
    shared = {"_search": search}
    loot = LootPipeline(loot_cfg)
    loot.process(attack_ctx(real_frame, search_cfg, 0.0, shared))
    loot.process(attack_ctx(real_frame, search_cfg, 0.1, shared))
    dead = set_bar_fill(loot_frame, *TARGET_BAR, 0.0)
    loot.process(attack_ctx(real_frame, search_cfg, 1.0, shared, dead))
    loot.process(attack_ctx(real_frame, search_cfg, 1.4, shared, dead))   # почав збирати -> busy
    ctx = ctx_for(dead, 1.6, shared)
    assert keys_of(search.process(ctx)) == []


def test_loot_quiet_while_target_alive(real_frame, search_cfg, loot_cfg):
    shared = {"_search": TargetSearchPipeline(search_cfg)}
    loot = LootPipeline(loot_cfg)
    loot.process(attack_ctx(real_frame, search_cfg, 0.0, shared))
    assert keys_of(loot.process(attack_ctx(real_frame, search_cfg, 0.1, shared))) == []


# ---- pet --------------------------------------------------------------------
def test_pet_heals_below_threshold(real_frame, pet_cfg):
    from tests.conftest import set_pet_hp

    hurt = set_pet_hp(real_frame, 0.3)
    res = PetHealPipeline(pet_cfg).process(ctx_for(hurt, 10.0))
    assert keys_of(res) == [pet_cfg.heal_key]


def test_pet_quiet_when_healthy(real_frame, pet_cfg):
    assert keys_of(PetHealPipeline(pet_cfg).process(ctx_for(real_frame, 10.0))) == []


def test_pet_heal_cooldown(real_frame, pet_cfg):
    from tests.conftest import set_pet_hp

    hurt = set_pet_hp(real_frame, 0.3)
    pipe = PetHealPipeline(pet_cfg)
    assert keys_of(pipe.process(ctx_for(hurt, 0.0))) == [pet_cfg.heal_key]
    assert keys_of(pipe.process(ctx_for(hurt, 1.0))) == []
    assert keys_of(pipe.process(ctx_for(hurt, 10.0))) == [pet_cfg.heal_key]


def test_pet_silent_without_pet(pet_cfg):
    blank = Image.new("RGB", (1440, 1080), (28, 48, 51))
    res = PetHealPipeline(pet_cfg).process(ctx_for(blank, 0.0))
    assert keys_of(res) == []
    assert "нема" in res.status


# ---- periodic ---------------------------------------------------------------
def test_periodic_presses_on_start_then_by_interval():
    pipe = PeriodicKeysPipeline(PeriodicKeysConfig(keys={"1": 1.5}))
    blank = Image.new("RGB", (100, 100))
    assert keys_of(pipe.process(ctx_for(blank, 0.0))) == ["1"]
    assert keys_of(pipe.process(ctx_for(blank, 1.0))) == []
    assert keys_of(pipe.process(ctx_for(blank, 1.6))) == ["1"]


def test_periodic_multiple_keys_independent():
    pipe = PeriodicKeysPipeline(PeriodicKeysConfig(keys={"1": 1.0, "2": 5.0}, press_on_start=False))
    blank = Image.new("RGB", (100, 100))
    pipe.process(ctx_for(blank, 0.0))
    assert keys_of(pipe.process(ctx_for(blank, 2.0))) == ["1"]
    assert sorted(keys_of(pipe.process(ctx_for(blank, 8.0)))) == ["1", "2"]


# ---- конструктор: реєстр і зв'язки ------------------------------------------
def test_registry_builds_every_pipeline_from_config(bot_config_raw):
    for spec in bot_config_raw["profiles"]["pw136_1440x1080"]["pipelines"]:
        assert build_pipeline(spec["type"], spec["config"], window="test").name == spec["type"]


def test_registry_knows_all_types():
    assert {"target_search", "attack", "loot", "pet_heal", "periodic_keys"} <= set(known_types())


def test_wiring_puts_provider_before_consumers(search_cfg, attack_cfg, loot_cfg):
    messy = [LootPipeline(loot_cfg), AttackPipeline(attack_cfg), TargetSearchPipeline(search_cfg)]
    ordered = [p.name for p in order_pipelines(messy)]
    assert ordered[0] == "target_search"
    assert set(ordered[1:]) == {"attack", "loot"}


def test_wiring_rejects_consumer_without_provider(attack_cfg):
    with pytest.raises(ConfigError, match="target"):
        order_pipelines([AttackPipeline(attack_cfg)], window="test")
