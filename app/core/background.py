"""
Важкі перевірки кадру (OCR координат, пошук шаблонів на весь кадр) коштують сотні мс
кожна й разом розтягували тік до 2-5 с. Тому в бойовому режимі вони йдуть в окремому
потоці, а тік лише забирає готовий результат (із запізненням на кадр-два).

Вимкнено за замовчуванням: тести й діагностика працюють синхронно. Вмикає запуск
сервера (enable()).
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor

_pool: ThreadPoolExecutor | None = None


def enable() -> None:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="probe")


class Probe:
    """Одна фонова перевірка. Не запускає нову, поки не забрано результат попередньої."""

    def __init__(self) -> None:
        self._fut: Future | None = None

    @property
    def pending(self) -> bool:
        return self._fut is not None

    def reset(self) -> None:
        self._fut = None          # результат старої перевірки вже нікому не потрібен

    def step(self, fn, *args, due: bool = True):
        """(готово, результат). Синхронно, якщо фон вимкнено."""
        if self._fut is not None:
            if not self._fut.done():
                return False, None
            fut, self._fut = self._fut, None
            try:
                return True, fut.result()
            except Exception:
                return False, None
        if not due:
            return False, None
        if _pool is None:
            return True, fn(*args)
        self._fut = _pool.submit(fn, *args)
        return False, None
