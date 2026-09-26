"""Тести конфіга: він має валідуватись і давати кожному вікну свій набір пайплайнів."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config.loader import load_config
from app.config.schemas import BotConfig, PipelineSpec, ProfileConfig, WindowConfig
from app.core.settings import settings


def test_real_config_loads():
    config = load_config(settings.CONFIG_PATH)
    assert config.windows
    assert all(w.profile in config.profiles for w in config.windows)


def test_specs_merge_overrides():
    config = BotConfig(
        profiles={"p": ProfileConfig(pipelines=[
            PipelineSpec(type="combat", config={"attack_key": "f1", "attack_interval": 3.0}),
        ])},
        windows=[
            WindowConfig(name="a", profile="p"),
            WindowConfig(name="b", profile="p", overrides={"combat": {"attack_key": "f5"}}),
        ],
    )
    assert config.specs_for(config.windows[0])[0].config["attack_key"] == "f1"
    b = config.specs_for(config.windows[1])[0].config
    assert b["attack_key"] == "f5"
    assert b["attack_interval"] == 3.0, "override не має стирати решту полів"


def test_duplicate_window_names_rejected():
    with pytest.raises(ValidationError):
        BotConfig(profiles={"p": ProfileConfig()},
                  windows=[WindowConfig(name="x", profile="p"), WindowConfig(name="x", profile="p")])


def test_unknown_profile_rejected():
    with pytest.raises(ValidationError):
        BotConfig(profiles={"p": ProfileConfig()}, windows=[WindowConfig(name="x", profile="nope")])


def test_region_accepts_list_form():
    from app.core.geometry import Region

    assert Region.model_validate([10, 20, 30, 40]).box == (10, 20, 40, 60)


def test_unknown_setting_does_not_break_the_start():
    """
    Забута калібровка в профілі колись не дала ботові стартувати взагалі. Тепер такі
    поля просто пропускаються — краще працювати, ніж мовчки падати на старті.
    """
    from app.pipelines.registry import build_pipeline

    pipe = build_pipeline("repair", {"every": 900, "shop_icon": {"x": 464, "y": 293}})
    assert pipe.config.every == 900
    assert not hasattr(pipe.config, "shop_icon")
