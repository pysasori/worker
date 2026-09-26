"""Глобальні налаштування застосунку (env / .env). Профілі вікон — у config/windows.json."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    CONFIG_PATH: Path = ROOT / "config" / "windows.json"
    SCREENSHOT_DIR: Path = ROOT / "screens"

    # Скільки разів перезняти кадр, якщо він прийшов порожній (гонка з D3D Present)
    CAPTURE_RETRIES: int = 3
    # Глобальний стоп-кран: нічого не тиснути, тільки дивитись
    DRY_RUN: bool = False


settings = AppSettings()
