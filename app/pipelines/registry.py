"""
Реєстр пайплайнів: type у конфізі -> клас.

Новий пайплайн додається одним декоратором @register і імпортом у __init__.py —
більше нічого правити не треба (ні рантайм, ні конфіг-лоадер, ні веб).
"""
from __future__ import annotations

import logging

from typing import Any

from app.core.exceptions import PipelineNotRegisteredError
from app.pipelines.base import Pipeline

_REGISTRY: dict[str, type[Pipeline]] = {}


def register(cls: type[Pipeline]) -> type[Pipeline]:
    _REGISTRY[cls.type_name] = cls
    return cls


def known_types() -> list[str]:
    return sorted(_REGISTRY)


log = logging.getLogger("bot")


def get_pipeline_class(type_name: str) -> type[Pipeline]:
    if type_name not in _REGISTRY:
        raise PipelineNotRegisteredError(type_name, known_types())
    return _REGISTRY[type_name]


def build_pipeline(type_name: str, raw_config: dict[str, Any] | None, window: str = "") -> Pipeline:
    """
    Зайві ключі в конфізі НЕ валять запуск: після зміни налаштувань у профілі лишаються
    старі поля (одного разу через забуту калібровку «shop_icon» бот взагалі не стартував).
    Такі ключі відкидаємо з попередженням у лог.
    """
    cls = get_pipeline_class(type_name)
    raw = dict(raw_config or {})
    known = set(cls.config_model.model_fields)
    extra = sorted(k for k in raw if k not in known)
    for key in extra:
        raw.pop(key)
        log.warning("[%s] %s: налаштування «%s» більше нема — пропускаю", window, type_name, key)
    return cls(cls.config_model(**raw), window=window)


def config_schema(type_name: str) -> dict[str, Any]:
    """JSON Schema конфіга — для майбутнього веб-інтерфейсу (автоформи)."""
    return get_pipeline_class(type_name).config_model.model_json_schema()
