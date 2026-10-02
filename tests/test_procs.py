"""Режим «одне вікно — один процес»: стан доходить із дочірнього процесу, зупинка чиста."""
from __future__ import annotations

import time

from app.config.loader import load_config
from app.core.settings import settings
from app.runtime.procs import ProcessOrchestrator


def test_child_process_reports_status_and_stops():
    config = load_config(settings.CONFIG_PATH)
    window = config.windows[0].model_copy(update={"name": "proc-test", "enabled": True})
    window.match = window.match.model_copy(update={"hwnd": 987654321})   # вікна нема — сесія просто чекає
    orch = ProcessOrchestrator(config, dry_run=True, windows=[window])
    orch.start()
    try:
        deadline = time.time() + 40
        while time.time() < deadline and not orch.statuses()[0].running:
            time.sleep(0.2)
        status = orch.statuses()[0]
        assert status.window == "proc-test" and status.running, "дочірній процес не віддав стан"
        assert orch.dead() == []
    finally:
        orch.stop()
    assert not orch._slots["proc-test"].proc.is_alive()
