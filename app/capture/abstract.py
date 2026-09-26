"""Порт захоплення екрана вікна. Реалізація — app/capture/win32.py."""
from __future__ import annotations

from abc import ABC, abstractmethod

from PIL import Image


class CapturePort(ABC):
    """Знімає клієнтську область вікна, не активуючи його."""

    @abstractmethod
    def grab(self) -> Image.Image:
        """Повний кадр клієнтської області, RGB."""

    @abstractmethod
    def client_size(self) -> tuple[int, int]:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Вікно існує і не згорнуте."""
