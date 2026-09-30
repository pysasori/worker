"""Контракт пайплайна: один кадр на вході, список дій на виході."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from PIL import Image
from pydantic import BaseModel

from app.core.geometry import Region
from app.pipelines.actions import PipelineResult


@dataclass
class Frame:
    """Один кадр вікна. Робиться ОДИН раз за тік і роздається всім пайплайнам."""

    image: Image.Image
    ts: float = field(default_factory=time.time)
    _crops: dict[tuple[int, int, int, int], Image.Image] = field(default_factory=dict, repr=False)

    def crop(self, region: Region) -> Image.Image:
        """Вирізка з кешем: два пайплайни з однаковою зоною не ріжуть кадр двічі."""
        key = region.box
        if key not in self._crops:
            self._crops[key] = self.image.crop(key)
        return self._crops[key]

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size


@dataclass
class PipelineContext:
    """Усе, що пайплайн бачить. Вікно і клавіші сюди НЕ передаються навмисно."""

    window: str                 # ім'я вікна з конфіга
    frame: Frame
    tick: int = 0
    foreground: bool = False    # гра зараз активна
    shared: dict[str, Any] = field(default_factory=dict)  # спільна дошка між пайплайнами

    @property
    def now(self) -> float:
        return self.frame.ts


class PipelineConfig(BaseModel):
    """База конфіга пайплайна. Кожен пайплайн описує свій наслідок."""

    model_config = {"extra": "forbid"}


class Pipeline(ABC):
    """
    Одна дія бота: бій, лікування піта, клавіші по таймеру, пошук моба...

    Правила:
      - не робить скрін сам (кадр дає рантайм);
      - не тисне клавіші сам (повертає дії);
      - тримає свій стан (кулдауни) всередині;
      - на кожне вікно створюється свій екземпляр;
      - не викликає інші пайплайни: обмін тільки через ctx.shared, а зв'язок
        оголошується в provides/requires (див. app/pipelines/wiring.py).
    """

    type_name: ClassVar[str]
    config_model: ClassVar[type[PipelineConfig]]
    label: ClassVar[str] = ""                         # людська назва для інтерфейсу
    category: ClassVar[str] = "інше"                  # група в конструкторі: pet, combat, loot, service
    run_order: ClassVar[int] = 0                      # більші значення виконуються пізніше
    provides: ClassVar[frozenset[str]] = frozenset()  # що кладе на спільну дошку
    requires: ClassVar[frozenset[str]] = frozenset()  # що звідти читає

    def __init__(self, config: PipelineConfig, window: str = "") -> None:
        self.config = config
        self.window = window

    @property
    def name(self) -> str:
        return self.type_name

    @abstractmethod
    def process(self, ctx: PipelineContext) -> PipelineResult:
        """Подивитись на кадр і вирішити, що робити."""

    def reset(self) -> None:
        """Скинути внутрішній стан (рестарт вікна, зміна профілю)."""
