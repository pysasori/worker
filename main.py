"""
PW136AutoBot — єдина точка входу.

    python main.py

Це все: піднімається веб-сторінка (http://127.0.0.1:8765), вона сама знаходить вікна гри,
читає ніки персонажів, і всі налаштування — профілі, персонажі, запуск, наглядач — на ній.
Ніяких інших скриптів запускати не треба.

Архітектура: один кадр вікна за тік -> пул пайплайнів -> дії -> ввід у вікно. Кожен персонаж
має профіль (config/windows.json), кожна дія бота — окремий пайплайн (app/pipelines/).
Гра лишається у фоні, миша й клавіатура вільні.

Процес-«батько» лише стежить: якщо сервер упав, піднімає його знову (з паузою, щоб не крутитись
на помилці). Усередині сервера працює наглядач за сесіями: зависла — перезапустить.

При кожному запуску сам підтягує код з git (fast-forward, лише якщо нема локальних
правок) — не треба щоразу вручну `git pull` на кожній машині. Не завадить роботі,
якщо git недоступний, нема мережі чи є незакомічені зміни — просто пропустить крок
і піде далі з тим кодом, що є.

Параметри потрібні рідко: --port/--host (перекривають налаштування зі сторінки),
--config (інший файл налаштувань), --log-level DEBUG (кожна дія в лозі),
--no-supervise (без батьківського процесу — для налагодження),
--no-update (не підтягувати git при старті).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from app.core.exceptions import BotError
from app.core.logging import setup_logging

ROOT = Path(__file__).resolve().parent
EXIT_CONFIG = 2                 # битий конфіг: перезапуск нічого не виправить
RESTART_PAUSE = (2, 5, 15, 30)  # паузи між падіннями, с; далі — остання
HEALTHY_AFTER = 60.0            # стільки проробив — падіння вважаємо одиничним


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=None, help="інший файл налаштувань")
    ap.add_argument("--host", default=None, help="адреса сервера (за замовчуванням із налаштувань)")
    ap.add_argument("--port", type=int, default=None, help="порт сервера (за замовчуванням із налаштувань)")
    ap.add_argument("--log-level", default=None, help="DEBUG показує кожну дію")
    ap.add_argument("--no-browser", action="store_true", help="не відкривати сторінку")
    ap.add_argument("--no-supervise", action="store_true", help="без батьківського процесу")
    ap.add_argument("--no-update", action="store_true", help="не підтягувати git при старті")
    ap.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)   # внутрішній: сам сервер
    return ap


def _git(git: str, *args: str, timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run([git, *args], cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)


def _release_template(git: str) -> None:
    """
    config/windows.json у репозиторії — лише шаблон; живий конфіг цієї машини — це
    config/local.json (див. app.config.loader.runtime_config_path). Старі версії писали
    живі налаштування прямо в шаблон, і він лишався «брудним» та блокував оновлення.
    Спершу гарантуємо, що налаштування збережені в local.json, і лише тоді повертаємо
    шаблон до стану репозиторію — так нічого не губиться.
    """
    from app.config.loader import runtime_config_path

    local = runtime_config_path()          # створить local.json із (можливо, брудного) шаблону
    if not local.exists():
        return
    dirty = _git(git, "status", "--porcelain", "--untracked-files=no", "--", "config/windows.json")
    if dirty.stdout.strip():
        _git(git, "checkout", "--", "config/windows.json")
        print("git: налаштування машини збережено в config/local.json, шаблон windows.json відновлено",
              flush=True)


def auto_update() -> None:
    """
    `git pull --ff-only` перед стартом — лише перемотування вперед, без мерджів і без
    ризику зачепити незакомічені правки. Якщо щось не так (нема git, нема мережі, нема
    репозиторію, локальні зміни, розбіжна історія) — тихо пропускаємо крок: бот має
    стартувати з тим кодом, що є, а не падати через недоступний GitHub.
    Сторонні (untracked) файли — діагностичні картинки тощо — оновленню не заважають.
    """
    git = shutil.which("git")
    if git is None:
        return
    try:
        if _git(git, "rev-parse", "--is-inside-work-tree").returncode != 0:
            return
        _release_template(git)
        dirty = _git(git, "status", "--porcelain", "--untracked-files=no")
        if dirty.stdout.strip():
            print("git: є незакомічені зміни — пропускаю автооновлення", flush=True)
            return
        before = _git(git, "rev-parse", "HEAD").stdout.strip()
        pull = _git(git, "pull", "--ff-only", timeout=30)
        if pull.returncode != 0:
            print(f"git: оновлення пропущено ({pull.stderr.strip().splitlines()[-1:] or pull.stdout.strip()})",
                  flush=True)
            return
        after = _git(git, "rev-parse", "HEAD").stdout.strip()
        if before != after:
            print(f"git: оновлено {before[:7]} -> {after[:7]}", flush=True)
    except (OSError, subprocess.SubprocessError):
        pass  # мережа, права доступу тощо — не критично, працюємо з тим, що є

def _settings(args: argparse.Namespace):
    from app.config.loader import load_config

    return load_config(args.config).settings


def serve(args: argparse.Namespace) -> int:
    """Сам сервер: сторінка, сканер вікон, наглядач, бот."""
    from app.web.server import run

    settings = _settings(args)
    host, port = args.host or settings.host, args.port or settings.port
    print(f"веб-інтерфейс: http://{host}:{port}", flush=True)
    run(host=host, port=port, config_path=args.config)
    return 0


def _wait_for_port(host: str, port: int, timeout: float = 25.0) -> bool:
    import socket

    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def supervise(args: argparse.Namespace) -> int:
    """Тримати сервер живим: впав — підняти знову. Ctrl+C зупиняє все."""
    settings = _settings(args)
    host, port = args.host or settings.host, args.port or settings.port
    cmd = [sys.executable, "-u", str(ROOT / "main.py"), "--serve", "--no-browser"]
    for flag, value in (("--config", args.config), ("--host", args.host), ("--port", args.port),
                        ("--log-level", args.log_level)):
        if value is not None:
            cmd += [flag, str(value)]

    opened = False
    crashes = 0
    while True:
        started = time.time()
        child = subprocess.Popen(cmd, cwd=str(ROOT))
        try:
            if not opened and settings.open_browser and not args.no_browser:
                opened = True
                if _wait_for_port(host if host != "0.0.0.0" else "127.0.0.1", port):
                    webbrowser.open(f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}/")
            code = child.wait()
        except KeyboardInterrupt:
            print("\nзупинка за Ctrl+C", flush=True)
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
            return 0
        if code == 0:
            return 0
        if code == EXIT_CONFIG:
            print("сервер не стартував через помилку в налаштуваннях — виправ config/windows.json",
                  file=sys.stderr, flush=True)
            return code
        crashes = 0 if time.time() - started > HEALTHY_AFTER else crashes + 1
        pause = RESTART_PAUSE[min(crashes, len(RESTART_PAUSE)) - 1] if crashes else RESTART_PAUSE[0]
        print(f"!! сервер завершився з кодом {code}, піднімаю знову через {pause} с", flush=True)
        try:
            time.sleep(pause)
        except KeyboardInterrupt:
            return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    setup_logging(args.log_level)
    # лише на вході людини в main.py, не на кожному внутрішньому перезапуску
    # дитини наглядачем (інакше падіння сервера кожні кілька секунд дьоргало б git)
    if not args.serve and not args.no_update:
        auto_update()
    try:
        if args.serve or args.no_supervise:
            return serve(args)
        return supervise(args)
    except BotError as e:
        print(f"\n{e}", file=sys.stderr)
        return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main())
