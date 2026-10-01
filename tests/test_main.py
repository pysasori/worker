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


class FakeRun:
    """Підробка subprocess.run: маршрутизує за командою git."""

    def __init__(self, responses: dict[tuple, object]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        key = tuple(cmd[1:])  # без шляху до git.exe
        for pattern, result in self.responses.items():
            if cmd[1:len(pattern) + 1] == list(pattern):
                return result
        raise AssertionError(f"непередбачена команда: {cmd}")


class Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_auto_update_skips_with_uncommitted_changes(monkeypatch, capsys):
    monkeypatch.setattr(entry.shutil, "which", lambda name: "git")
    fake = FakeRun({
        ("rev-parse", "--is-inside-work-tree"): Result(0),
        ("status", "--porcelain"): Result(0, stdout="M config/windows.json\n"),
    })
    monkeypatch.setattr(entry.subprocess, "run", fake)
    entry.auto_update()
    assert not any(c[1:3] == ["pull", "--ff-only"] for c in fake.calls), "брудний репо — pull не викликаємо"
    assert "незакомічені" in capsys.readouterr().out


def test_auto_update_pulls_a_clean_repo(monkeypatch, capsys):
    monkeypatch.setattr(entry.shutil, "which", lambda name: "git")
    fake = FakeRun({
        ("rev-parse", "--is-inside-work-tree"): Result(0),
        ("status", "--porcelain"): Result(0, stdout=""),
        ("rev-parse", "HEAD"): Result(0, stdout="aaa1111\n"),
        ("pull", "--ff-only"): Result(0, stdout="Updating aaa1111..bbb2222\n"),
    })
    # друге rev-parse HEAD (після pull) має віддати інший хеш — підміняємо послідовно
    calls = {"n": 0}
    heads = ["aaa1111\n", "bbb2222\n"]

    def run(cmd, **kw):
        fake.calls.append(cmd)
        if cmd[1:3] == ["rev-parse", "HEAD"]:
            out = heads[calls["n"]]
            calls["n"] += 1
            return Result(0, stdout=out)
        for pattern, result in fake.responses.items():
            if cmd[1:len(pattern) + 1] == list(pattern):
                return result
        raise AssertionError(f"непередбачена команда: {cmd}")

    monkeypatch.setattr(entry.subprocess, "run", run)
    entry.auto_update()
    assert any(c[1:3] == ["pull", "--ff-only"] for c in fake.calls)
    assert "aaa1111 -> bbb2222" in capsys.readouterr().out


def test_auto_update_is_quiet_without_git(monkeypatch):
    monkeypatch.setattr(entry.shutil, "which", lambda name: None)
    entry.auto_update()  # не падає, якщо git узагалі нема


def test_auto_update_does_not_crash_on_pull_failure(monkeypatch, capsys):
    monkeypatch.setattr(entry.shutil, "which", lambda name: "git")
    fake = FakeRun({
        ("rev-parse", "--is-inside-work-tree"): Result(0),
        ("status", "--porcelain"): Result(0, stdout=""),
        ("rev-parse", "HEAD"): Result(0, stdout="aaa1111\n"),
        ("pull", "--ff-only"): Result(1, stdout="", stderr="fatal: couldn't find remote ref main\n"),
    })
    monkeypatch.setattr(entry.subprocess, "run", fake)
    entry.auto_update()
    assert "пропущено" in capsys.readouterr().out


def test_no_update_flag_is_available():
    args = entry.build_parser().parse_args(["--no-update"])
    assert args.no_update is True
