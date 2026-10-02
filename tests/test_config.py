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


# ---- живий конфіг машини й шаблон ---------------------------------------------------------
def test_runtime_config_is_seeded_from_the_template_and_then_left_alone(tmp_path, monkeypatch):
    """
    config/windows.json — шаблон у git, config/local.json — живий конфіг машини. Налаштування
    в кожного бота свої, тому local.json (у .gitignore) створюється з шаблону один раз і
    далі не перезаписується шаблоном.
    """
    from app.config import loader
    from app.core.settings import settings

    template = tmp_path / "windows.json"
    local = tmp_path / "local.json"
    template.write_text('{"profiles": {"p": {}}, "windows": []}', encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_PATH", template)
    monkeypatch.setattr(settings, "LOCAL_CONFIG_PATH", local)

    assert loader.runtime_config_path() == local and local.exists()
    cfg = loader.load_config()
    from app.config.schemas import CharacterConfig

    cfg.characters["Mine"] = CharacterConfig(profile="p")
    loader.save_config(cfg)
    assert "Mine" in local.read_text(encoding="utf-8")
    assert "Mine" not in template.read_text(encoding="utf-8"), "шаблон сервер не пише"

    template.write_text('{"profiles": {"p": {}}, "windows": [], "default_profile": "p"}', encoding="utf-8")
    assert "Mine" in loader.load_config().characters, "оновлений шаблон не затирає живий конфіг"
