"""Читання і запис config/windows.json."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.config.schemas import BotConfig
from app.core.exceptions import ConfigError
from app.core.settings import settings


def load_config(path: Path | None = None) -> BotConfig:
    path = Path(path or settings.CONFIG_PATH)
    if not path.exists():
        raise ConfigError(f"конфіг не знайдено: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path.name}: битий JSON у рядку {e.lineno}: {e.msg}") from e
    try:
        return BotConfig(**_strip_comments(raw))
    except ValidationError as e:
        raise ConfigError(f"{path.name}: {e}") from e


def save_config(config: BotConfig, path: Path | None = None) -> Path:
    """Знадобиться веб-інтерфейсу для збереження налаштувань."""
    path = Path(path or settings.CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def _strip_comments(obj):
    """Ключі, що починаються з "_", — коментарі в JSON."""
    if isinstance(obj, dict):
        return {k: _strip_comments(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_comments(v) for v in obj]
    return obj
