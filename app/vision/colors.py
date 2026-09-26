"""Предикати кольору. Пороги підібрані на клієнті PW 1.3.6."""
from __future__ import annotations


# Піщаний ґрунт деяких локацій (≈190,95,70) теж «червоний» за наївною перевіркою,
# і бот залипав на ньому як на смужці цілі. Рятує різниця R-G: у ґрунту вона до 63,
# у смужки HP — 88 і вище (виміряно на живих кадрах).
RED_MIN_DIFF = 70


def is_red(px) -> bool:
    """Червоний HP-бар (≈194,46,34 … 254,52,57), але не руда земля."""
    return (px[0] > 150 and px[1] < 100 and px[2] < 100
            and px[0] - px[1] >= RED_MIN_DIFF)


def is_bright(px, threshold: int = 150) -> bool:
    """Світлий піксель — білий текст HP поверх смужки і його антиаліасинг."""
    return min(px[0], px[1], px[2]) > threshold


def is_close(px, rgb: tuple[int, int, int], tol: int) -> bool:
    return abs(px[0] - rgb[0]) <= tol and abs(px[1] - rgb[1]) <= tol and abs(px[2] - rgb[2]) <= tol
