"""
Зв'язки між пайплайнами — "конструктор".

Кожен пайплайн оголошує, що він ДАЄ на спільну дошку (provides) і що йому ПОТРІБНО
звідти (requires). Рантайм із цього сам вибудовує порядок виконання: постачальник
завжди раніше за споживача, тому лут бачить смерть цілі тим самим кадром.

Порядок у конфізі лишається підказкою (стабільне сортування), але помилку скласти
не можна: якщо блок, який щось віддає, забули увімкнути — бот скаже про це на старті,
а не мовчки нічого не робитиме.
"""
from __future__ import annotations

from app.core.exceptions import ConfigError
from app.pipelines.base import Pipeline


def describe_wiring(pipelines: list[Pipeline]) -> str:
    parts = []
    for p in pipelines:
        gives = "+".join(sorted(p.provides)) or "-"
        needs = "+".join(sorted(p.requires)) or "-"
        parts.append(f"{p.name}({needs}→{gives})")
    return " ".join(parts)


def order_pipelines(pipelines: list[Pipeline], window: str = "") -> list[Pipeline]:
    """Стабільне топологічне сортування за provides/requires."""
    available: set[str] = set()
    for p in pipelines:
        available |= p.provides

    where = f" (вікно '{window}')" if window else ""
    for p in pipelines:
        missing = p.requires - available
        if missing:
            raise ConfigError(
                f"пайплайн '{p.name}' потребує {sorted(missing)}, але цього ніхто не віддає{where}. "
                f"Додай у профіль пайплайн, що дає {sorted(missing)}")

    remaining = list(pipelines)
    satisfied: set[str] = set()
    ordered: list[Pipeline] = []
    while remaining:
        for i, p in enumerate(remaining):
            if p.requires <= satisfied:
                ordered.append(remaining.pop(i))
                satisfied |= p.provides
                break
        else:
            stuck = ", ".join(f"{p.name}({sorted(p.requires)})" for p in remaining)
            raise ConfigError(f"кільцева залежність між пайплайнами{where}: {stuck}")
    return ordered
