"""Єдина точка входу: main.py піднімає сторінку й тримає сервер живим."""
from __future__ import annotations

import json

import pytest

import main as entry


class FakeChild:
    def __init__(self, code: int) -> None:
        self.code = code

    def wait(self, timeout=None) -> int:
        return self.code

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


@pytest.fixture
def cfg_path(tmp_path):
    path = tmp_path / "windows.json"
    path.write_text(json.dumps({"profiles": {"p": {}}, "windows": [], "migrated_windows": True,
                                "settings": {"open_browser": False, "port": 9999}}), encoding="utf-8")
    return path


def run_supervised(monkeypatch, cfg_path, codes):
    """Прогнати супервізор на підроблених дочірніх процесах з цими кодами виходу."""
    spawned, pauses = [], []
    codes = iter(codes)

    def popen(cmd, **kw):
        spawned.append(cmd)
        return FakeChild(next(codes))

    monkeypatch.setattr(entry.subprocess, "Popen", popen)
    monkeypatch.setattr(entry.time, "sleep", pauses.append)
    args = entry.build_parser().parse_args(["--config", str(cfg_path), "--no-browser"])
    return entry.supervise(args), spawned, pauses


def test_crashed_server_is_started_again_with_growing_pauses(monkeypatch, cfg_path, capsys):
    code, spawned, pauses = run_supervised(monkeypatch, cfg_path, [1, 1, 1, 0])
    assert code == 0 and len(spawned) == 4, "три падіння — три перезапуски, потім чистий вихід"
    assert pauses == [2, 5, 15], "паузи ростуть, щоб не крутитись на помилці"
    assert all("--serve" in c for c in spawned)


def test_clean_exit_is_not_restarted(monkeypatch, cfg_path):
    code, spawned, _ = run_supervised(monkeypatch, cfg_path, [0])
    assert code == 0 and len(spawned) == 1


def test_broken_config_is_not_restarted_in_a_loop(monkeypatch, cfg_path):
    """Перезапуск не виправить помилку в налаштуваннях — тільки засипле консоль."""
    code, spawned, _ = run_supervised(monkeypatch, cfg_path, [entry.EXIT_CONFIG])
    assert code == entry.EXIT_CONFIG and len(spawned) == 1


def test_child_gets_the_same_settings(monkeypatch, cfg_path):
    _, spawned, _ = run_supervised(monkeypatch, cfg_path, [0])
    cmd = spawned[0]
    assert "--config" in cmd and str(cfg_path) in cmd


def test_port_comes_from_settings_unless_overridden(cfg_path):
    args = entry.build_parser().parse_args(["--config", str(cfg_path)])
    assert entry._settings(args).port == 9999
    assert entry.build_parser().parse_args(["--port", "1234"]).port == 1234


def test_only_one_entry_point_remains():
    """Ніяких підкоманд: усе керується зі сторінки."""
    with pytest.raises(SystemExit):
        entry.build_parser().parse_args(["web"])
