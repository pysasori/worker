"""
Конфіг бота. Профіль описує ІНТЕРФЕЙС клієнта (координати смужок, клавіші),
вікно описує КОНКРЕТНИЙ клієнт (як його знайти) і посилається на профіль.

Так кілька вікон з однаковим інтерфейсом налаштовуються один раз, а різниця між
персонажами задається точковими overrides.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.capture.window_finder import WindowMatch
from app.vision.nick import NickConfig


class PipelineSpec(BaseModel):
    type: str = Field(description="тип пайплайна з реєстру (combat, pet_heal, periodic_keys...)")
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class ProfileConfig(BaseModel):
    """Набір пайплайнів під один вигляд інтерфейсу."""

    client_size: tuple[int, int] | None = Field(
        default=None, description="під який розмір клієнта калібровані координати")
    pipelines: list[PipelineSpec] = Field(default_factory=list)


class WindowConfig(BaseModel):
    name: str = Field(description="ім'я вікна в логах і веб-інтерфейсі")
    profile: str = Field(description="який профіль інтерфейсу використати")
    match: WindowMatch = Field(default_factory=WindowMatch)
    enabled: bool = True
    poll_interval: float = Field(default=0.5, gt=0, description="пауза між кадрами, с")
    overrides: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="точкові правки конфіга пайплайна: {тип: {поле: значення}}")


class AppSettings(BaseModel):
    """Налаштування самої програми: усе, що не належить ні профілю, ні персонажу."""

    autostart: bool = Field(default=False, title="Запускати бота при старті програми",
                            description="після запуску main.py бот сам стартує для персонажів із "
                                        "галочкою «запускати». Без цього — кнопкою «Запустити»")
    open_browser: bool = Field(default=True, title="Відкривати сторінку при старті",
                               description="діє при запуску main.py; після падіння й автоперезапуску "
                                           "сторінка вдруге не відкривається")
    scan_interval: float = Field(default=5.0, ge=1, title="Шукати вікна гри раз на, с",
                                 description="стільки ж триває, поки новий клієнт з'явиться у списку")
    guard: bool = Field(default=True, title="Наглядач",
                        description="перезапускає сесію, якщо кадри перестали рухатись, і "
                                    "піднімає бота, якщо той помер сам, а не був зупинений")
    guard_every: float = Field(default=15.0, ge=1, title="Наглядач: перевіряти раз на, с",
                               json_schema_extra={"tech": True})
    stall_after: float = Field(default=30.0, ge=5, title="Кадри стоять, якщо не рухаються, с",
                               description="після цього сесія цього вікна перезапускається",
                               json_schema_extra={"tech": True})
    host: str = Field(default="127.0.0.1", title="Адреса сервера",
                      description="зміна діє після перезапуску програми. 0.0.0.0 відкриває "
                                  "сторінку всій мережі — сторінка не захищена паролем",
                      json_schema_extra={"tech": True})
    port: int = Field(default=8765, ge=1, le=65535, title="Порт сервера",
                      description="зміна діє після перезапуску програми",
                      json_schema_extra={"tech": True})
    nick: NickConfig = Field(default_factory=NickConfig, title="Читання ніка з екрана",
                             json_schema_extra={"tech": True})
    status_log_every: float = Field(default=20.0, ge=0, title="Писати стан у консоль раз на, с",
                                    description="0 = не писати. Той самий рядок, що й у картці вікна "
                                                "в інтерфейсі (HP, координати, ціль, кожен пайплайн) — "
                                                "щоб бачити прогрес прямо в консолі чи в логах "
                                                "віддаленої машини, не відкриваючи сторінку")


class CharacterConfig(BaseModel):
    """
    Налаштування персонажа. Ключ у BotConfig.characters — нік, прочитаний з екрана:
    вікно гри при кожному запуску отримує новий hwnd, а нік лишається, тому профіль
    «прилипає» до персонажа, а не до вікна.
    """

    profile: str = Field(description="який профіль із пулу використати")
    label: str = Field(default="", description="своя назва для інтерфейсу; порожньо = нік")
    aliases: list[str] = Field(
        default_factory=list,
        description="те, що OCR звично читає замість справжнього ніка: людина підтвердила, "
                    "що це той самий персонаж")
    enabled: bool = Field(default=False, description="чи запускати бота для цього персонажа")
    poll_interval: float = Field(default=0.5, gt=0, description="пауза між кадрами, с")
    overrides: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="точкові правки конфіга пайплайна: {тип: {поле: значення}}")


class BotConfig(BaseModel):
    profiles: dict[str, ProfileConfig]
    windows: list[WindowConfig] = Field(
        default_factory=list,
        description="старий спосіб: вікна, задані руками. При першому скані переїжджають у characters")
    characters: dict[str, CharacterConfig] = Field(
        default_factory=dict, description="персонажі за ніком: профіль і чи запускати")
    default_profile: str | None = Field(
        default=None, description="профіль для нових персонажів; порожньо = перший у пулі")
    settings: AppSettings = Field(default_factory=AppSettings)
    migrated_windows: bool = Field(default=False, description="службове: windows уже перенесено в characters")

    @model_validator(mode="before")
    @classmethod
    def _legacy_scan_interval(cls, data: Any) -> Any:
        """scan_interval жив на верхньому рівні — тепер у settings."""
        if isinstance(data, dict) and "scan_interval" in data:
            data = dict(data)
            settings = dict(data.get("settings") or {})
            settings.setdefault("scan_interval", data.pop("scan_interval"))
            data["settings"] = settings
        return data

    @model_validator(mode="after")
    def _check_profiles(self) -> "BotConfig":
        missing = {w.profile for w in self.windows} - set(self.profiles)
        if missing:
            raise ValueError(f"вікна посилаються на невідомі профілі: {', '.join(sorted(missing))}")
        lost = {c.profile for c in self.characters.values()} - set(self.profiles)
        if lost:
            raise ValueError(f"персонажі посилаються на невідомі профілі: {', '.join(sorted(lost))}")
        if self.default_profile and self.default_profile not in self.profiles:
            raise ValueError(f"профіль за замовчуванням «{self.default_profile}» не існує")
        dupes = {n for n in (w.name for w in self.windows) if [x.name for x in self.windows].count(n) > 1}
        if dupes:
            raise ValueError(f"імена вікон повторюються: {', '.join(sorted(dupes))}")
        return self

    def specs_for(self, window: WindowConfig) -> list[PipelineSpec]:
        """Пайплайни вікна: профіль + overrides."""
        result: list[PipelineSpec] = []
        for spec in self.profiles[window.profile].pipelines:
            merged = dict(spec.config)
            merged.update(window.overrides.get(spec.type, {}))
            result.append(PipelineSpec(type=spec.type, enabled=spec.enabled, config=merged))
        return result

    def known_nicks(self) -> dict[str, list[str]]:
        """Персонажі для сканера: {нік: [псевдоніми]}."""
        return {nick: list(c.aliases) for nick, c in self.characters.items()}

    def new_character_profile(self) -> str:
        """Профіль, який отримує щойно знайдений персонаж."""
        return self.default_profile or next(iter(self.profiles))

    def window_for(self, nick: str, hwnd: int) -> WindowConfig:
        """
        Вікно для сесії: персонаж + конкретний hwnd. Ім'я вікна в логах — нік, а
        не hwnd: логи й адреси API читаються людиною і не міняються між запусками гри.
        """
        char = self.characters[nick]
        return WindowConfig(name=nick, profile=char.profile, enabled=char.enabled,
                            poll_interval=char.poll_interval, overrides=char.overrides,
                            match=WindowMatch(hwnd=hwnd))
