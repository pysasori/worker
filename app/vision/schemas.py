"""Типізовані результати розпізнавання. Vision нічого не тисне і не знає про пайплайни."""
from __future__ import annotations

from pydantic import BaseModel, Field


class BarReading(BaseModel):
    """Показник смужки (HP/MP/досвід)."""

    present: bool = False           # смужку видно на екрані
    filled: int = 0                 # px заповненої частини
    total: int = 0                  # px повної ширини (0 = невідома)
    x0: int = -1                    # лівий край смужки в координатах вікна
    row: int = -1                   # рядок, з якого знято показник

    @property
    def ratio(self) -> float:
        """0..1. Без відомої повної ширини — 0."""
        return min(1.0, self.filled / self.total) if self.total > 0 else 0.0

    @property
    def percent(self) -> int:
        return round(self.ratio * 100)

    def __str__(self) -> str:
        if not self.present:
            return "нема"
        return f"{self.percent}% ({self.filled}/{self.total or '?'}px)"


class BarScanConfig(BaseModel):
    """
    Смужка, що плаває по екрану (рамка цілі). Шукається за ознаками: найдовший
    червоний відрізок у смузі рядків. Діапазон X не задається — рамка цілі
    з'їжджає залежно від довжини назви моба, тому прив'язка до координат тільки шкодить.
    """

    rows: tuple[int, int] = Field(default=(9, 17), title="Рядки [від, до)",
                                  json_schema_extra={"tech": True})
    x0_range: tuple[int, int] | None = Field(
        default=None, title="Обмеження по X", json_schema_extra={"tech": True},
        description="зазвичай не потрібне: смужка шукається за виглядом")
    gap: int = Field(default=8, title="Розрив у смужці, px", json_schema_extra={"tech": True})
    bright_min: int = Field(default=150, title="Поріг тексту", json_schema_extra={"tech": True})
    min_filled: int = Field(default=2, title="Мінімум заповнення, px", json_schema_extra={"tech": True},
                            description="у моба з 12 HP із 726 червоного лишається 3 px — "
                                        "з порогом 6 бот вважав його мертвим і кидав недобитим")
    empty_rgb: tuple[int, int, int] = Field(default=(29, 54, 59), title="Колір порожньої частини",
                                            json_schema_extra={"tech": True})
    empty_tol: int = Field(default=16, title="Допуск кольору", json_schema_extra={"tech": True})
    min_span: int = Field(default=120, title="Мінімальна ширина рамки, px",
                          json_schema_extra={"tech": True},
                          description="червоне + порожнє: так видно рамку цілі навіть при HP 1%, "
                                      "а руда земля й дрібні плями рамкою не прикинуться")


class BarFixedConfig(BaseModel):
    """
    Смужка сталого розміру (рамка піта). Шукається за ОЗНАКАМИ, а не за координатами:
    у смузі рядків шукаємо суцільний відрізок з пікселів «червоне або порожнє тло»
    завширшки приблизно `width`. Тому смужка знаходиться, навіть якщо інтерфейс
    зсунувся або гра стоїть в іншій роздільній здатності.
    """

    rows: tuple[int, int] = Field(default=(180, 202), title="Рядки [від, до)", json_schema_extra={"tech": True})
    width: int = Field(default=82, title="Ширина смужки, px", json_schema_extra={"tech": True})
    width_tol: float = Field(default=0.25, ge=0, le=1, title="Допуск ширини", json_schema_extra={"tech": True})
    x0: int | None = Field(default=None, title="Підказка X", json_schema_extra={"tech": True})
    empty_rgb: tuple[int, int, int] = Field(default=(26, 46, 49), title="Колір порожньої частини", json_schema_extra={"tech": True})
    fill_rgb: tuple[int, int, int] | None = Field(
        default=None, title="Колір заповненої частини", json_schema_extra={"tech": True},
        description="порожньо = червона смужка HP")
    fill_tol: int = Field(default=45, title="Допуск кольору заповнення", json_schema_extra={"tech": True})
    empty_tol: int = Field(default=14, title="Допуск кольору", json_schema_extra={"tech": True})
    present_ratio: float = Field(default=0.7, title="Частка покриття", json_schema_extra={"tech": True})
