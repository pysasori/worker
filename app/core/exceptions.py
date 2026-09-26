"""Ієрархія помилок бота."""
from __future__ import annotations


class BotError(Exception):
    """Базова помилка."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return f"BOT ERROR => {self.message}"


class WindowNotFoundError(BotError):
    def __init__(self, match: str) -> None:
        super().__init__(f"вікно не знайдено: {match}")


class WindowGoneError(BotError):
    """Вікно закрилось або згорнулось під час роботи."""

    def __init__(self, name: str, reason: str = "") -> None:
        super().__init__(f"вікно '{name}' недоступне{': ' + reason if reason else ''}")


class CaptureError(BotError):
    pass


class ConfigError(BotError):
    pass


class PipelineNotRegisteredError(ConfigError):
    def __init__(self, type_name: str, known: list[str]) -> None:
        super().__init__(f"невідомий пайплайн '{type_name}'. Доступні: {', '.join(sorted(known))}")
