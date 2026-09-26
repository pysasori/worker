"""Виконавець дій: єдине місце, де щось реально тиснеться."""
from __future__ import annotations

import time

from app.input.abstract import InputPort
from app.pipelines.actions import Action, ClickAt, DragTo, PressKey, TypeText, Wait


class ActionExecutor:
    def __init__(self, input_port: InputPort, dry_run: bool = False) -> None:
        self.input = input_port
        self.dry_run = dry_run
        self.executed = 0

    def run(self, actions: list[Action]) -> list[str]:
        """Виконує послідовно. Повертає опис виконаного (для логів/веба)."""
        done: list[str] = []
        for action in actions:
            done.append(action.describe())
            self._run_one(action)
            self.executed += 1
        return done

    def _run_one(self, action: Action) -> None:
        if isinstance(action, Wait):
            time.sleep(action.seconds)
            return
        if not self.dry_run:
            if isinstance(action, PressKey):
                self.input.press(action.key, hold=action.hold)
            elif isinstance(action, ClickAt):
                if action.hover_delay:
                    self.input.move(action.x, action.y)
                    time.sleep(action.hover_delay)
                if action.double:
                    self.input.double_click(action.x, action.y)
                else:
                    self.input.click(action.x, action.y, button=action.button)
            elif isinstance(action, DragTo):
                if action.hover_delay:
                    self.input.move(action.x1, action.y1)
                    time.sleep(action.hover_delay)
                self.input.drag(action.x1, action.y1, action.x2, action.y2)
            elif isinstance(action, TypeText):
                self.input.type_text(action.text)
        if action.delay_after:
            time.sleep(action.delay_after)
