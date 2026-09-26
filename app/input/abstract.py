"""Порт вводу у вікно. Реалізація — app/input/win32.py."""
from __future__ import annotations

from abc import ABC, abstractmethod


class InputPort(ABC):
    """Шле клавіші/мишу в конкретне вікно, не роблячи його активним."""

    @abstractmethod
    def press(self, key: str, hold: float = 0.05) -> None:
        ...

    @abstractmethod
    def click(self, x: int, y: int, button: str = "left", hold: float = 0.05) -> None:
        ...

    @abstractmethod
    def move(self, x: int, y: int) -> None:
        """Навести курсор без кліку: кнопки інтерфейсу PW реагують лише на підсвічені."""

    @abstractmethod
    def type_text(self, text: str, delay: float = 0.03) -> None:
        ...


class NullInput(InputPort):
    """Заглушка для --dry і тестів: нічого не шле."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    def press(self, key: str, hold: float = 0.05) -> None:
        self.sent.append(("press", key))

    def click(self, x: int, y: int, button: str = "left", hold: float = 0.05) -> None:
        self.sent.append(("click", (x, y, button)))

    def move(self, x: int, y: int) -> None:
        self.sent.append(("move", (x, y)))

    def type_text(self, text: str, delay: float = 0.03) -> None:
        self.sent.append(("type", text))
