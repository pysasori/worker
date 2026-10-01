"""Скільки мілісекунд їсть кожен пайплайн на одному кадрі (клавіші не шлються)."""
import sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from app.config.loader import load_config
from app.runtime.session import WindowSession
from app.pipelines.base import Frame, PipelineContext

cfg = load_config()
for name in sys.argv[1:] or ["Karasu"]:
    hw = {"Karasu": 460180, "Kuroari": 525550}.get(name) or int(name)
    ch = cfg.characters.get(name) if hasattr(cfg, "characters") else None
    base = cfg.windows[0]
    m = base.match.model_copy(update={"hwnd": hw})
    wc = base.model_copy(update={"name": name, "match": m, "profile": ch.profile if ch else base.profile})
    s = WindowSession(wc, cfg, dry_run=True)
    if not s.connect():
        print("не підключилось", name); continue
    t = time.perf_counter(); img = s.capture.grab(); print(f"[{name}] grab {1000*(time.perf_counter()-t):.0f} мс")
    for n in range(2):
        ctx = PipelineContext(window=name, frame=Frame(image=img), tick=n+1, shared=s.shared)
        tot = 0
        for p in s.pipelines:
            t = time.perf_counter(); p.process(ctx); d = 1000*(time.perf_counter()-t); tot += d
            if d > 20: print(f"  {p.name:14} {d:7.0f} мс")
        print(f"  усього {tot:.0f} мс")

if "--prof" in sys.argv:
    pass
