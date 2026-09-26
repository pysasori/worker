"""Геометрія в координатах КЛІЄНТСЬКОЇ області вікна (не екрана)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class Point(BaseModel):
    x: int = Field(title="X")
    y: int = Field(title="Y")

    def as_tuple(self) -> tuple[int, int]:
        return self.x, self.y


class Region(BaseModel):
    """Прямокутник (x, y, w, h) у клієнтських координатах вікна."""

    x: int = Field(ge=0, title="X")
    y: int = Field(ge=0, title="Y")
    w: int = Field(gt=0, title="Ширина")
    h: int = Field(gt=0, title="Висота")

    @model_validator(mode="before")
    @classmethod
    def _accept_list(cls, v: Any) -> Any:
        """У конфізі зручніше писати [x, y, w, h], ніж об'єкт."""
        if isinstance(v, (list, tuple)):
            if len(v) != 4:
                raise ValueError("region має бути [x, y, w, h]")
            return dict(zip(("x", "y", "w", "h"), v))
        return v

    @classmethod
    def of(cls, x: int, y: int, w: int, h: int) -> "Region":
        return cls(x=x, y=y, w=w, h=h)

    @property
    def box(self) -> tuple[int, int, int, int]:
        """Для PIL.Image.crop: (left, top, right, bottom)."""
        return self.x, self.y, self.x + self.w, self.y + self.h

    def to_local(self, x: int, y: int) -> tuple[int, int]:
        """Координати вікна -> координати всередині вирізки."""
        return x - self.x, y - self.y

    def to_window(self, x: int, y: int) -> tuple[int, int]:
        """Координати всередині вирізки -> координати вікна."""
        return x + self.x, y + self.y

    def contains_x(self, x: int) -> bool:
        return self.x <= x < self.x + self.w


class Color(BaseModel):
    r: int = Field(ge=0, le=255)
    g: int = Field(ge=0, le=255)
    b: int = Field(ge=0, le=255)

    @classmethod
    def of(cls, r: int, g: int, b: int) -> "Color":
        return cls(r=r, g=g, b=b)

    def as_tuple(self) -> tuple[int, int, int]:
        return self.r, self.g, self.b

    def close_to(self, px, tol: int) -> bool:
        return (abs(px[0] - self.r) <= tol and abs(px[1] - self.g) <= tol
                and abs(px[2] - self.b) <= tol)
