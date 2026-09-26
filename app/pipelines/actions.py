"""
Дії, які пайплайн ПРОСИТЬ виконати. Сам він нічого не тисне.

Завдяки цьому пайплайн — чиста функція (кадр + стан -> список дій): його можна
тестувати на збережених скрінах без гри, показувати в веб-інтерфейсі як план дій
і в будь-який момент виконати "в холосту" (dry-run).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PressKey:
    key: str
    hold: float = 0.05
    delay_after: float = 0.0
    reason: str = ""

    def describe(self) -> str:
        return f"{self.key}{f' ({self.reason})' if self.reason else ''}"


@dataclass(frozen=True)
class ClickAt:
    x: int
    y: int
    button: str = "left"
    delay_after: float = 0.0
    reason: str = ""
    hover_delay: float = 0.0
    """Кнопки в інтерфейсі PW не реагують на «сухий» клік: спершу треба навести курсор
    і дати грі підсвітити кнопку. hover_delay > 0 робить саме це."""
    double: bool = False
    """Подвійний клік — так у «Списку» запускається біг до точки."""

    def describe(self) -> str:
        kind = "подвійний клік" if self.double else f"клік {self.button}"
        return f"{kind} ({self.x},{self.y}){f' ({self.reason})' if self.reason else ''}"


@dataclass(frozen=True)
class DragTo:
    """Перетягнути мишею: так у грі кладуть предмет із рюкзака в лот на продаж."""

    x1: int
    y1: int
    x2: int
    y2: int
    hover_delay: float = 0.25
    delay_after: float = 0.0
    reason: str = ""

    def describe(self) -> str:
        return f"перетягнути ({self.x1},{self.y1}) -> ({self.x2},{self.y2})" + (
            f" ({self.reason})" if self.reason else "")


@dataclass(frozen=True)
class TypeText:
    text: str
    delay_after: float = 0.0
    reason: str = ""

    def describe(self) -> str:
        return f"текст {self.text!r}"


@dataclass(frozen=True)
class Wait:
    seconds: float
    reason: str = ""

    def describe(self) -> str:
        return f"пауза {self.seconds:.2f}с{f' ({self.reason})' if self.reason else ''}"


Action = PressKey | ClickAt | DragTo | TypeText | Wait


@dataclass
class PipelineResult:
    """Що пайплайн вирішив за цей кадр."""

    actions: list[Action] = field(default_factory=list)
    status: str = ""            # короткий рядок для консолі / веба
    events: list[str] = field(default_factory=list)  # що варто написати в лог

    @classmethod
    def idle(cls, status: str = "") -> "PipelineResult":
        return cls(status=status)
