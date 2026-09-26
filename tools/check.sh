#!/usr/bin/env bash
# Коротка перевірка нічного прогону: зведення монітора і тривоги з логу бота.
cd "$(dirname "$0")/.."
tail -n "${1:-3}" logs/monitor.log
PYTHONIOENCODING=utf-8 .venv/Scripts/python - <<'PY'
lines = open("logs/web.out.log", encoding="utf-8", errors="replace").read().splitlines()
hot = [l for l in lines if "!!" in l or "Traceback" in l or "return_home" in l or "pet_summon" in l or "repair" in l]
print("\n".join(hot[-8:]) or "тривог нема")
PY
