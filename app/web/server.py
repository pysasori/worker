"""FastAPI: тонкий шар над BotService. Уся логіка — в сервісі."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from app.core.exceptions import BotError
from app.core.logging import setup_logging
from app.web.service import BotService

STATIC_DIR = Path(__file__).parent / "static"


class PressRequest(BaseModel):
    key: str = Field(min_length=1)
    times: int = Field(default=1, ge=1, le=50)
    interval: float = Field(default=0.3, ge=0, le=10)


class CalibrateRequest(BaseModel):
    what: str
    x: int = Field(ge=0)
    y: int = Field(ge=0)


class AssignRequest(BaseModel):
    nick: str = Field(min_length=1, max_length=64)


class StartRequest(BaseModel):
    dry_run: bool = False
    windows: list[str] | None = None


def create_app(config_path: Path | None = None, scan: bool = True) -> FastAPI:
    """scan — стежити за вікнами гри у фоні (тести вимикають: там нема клієнтів)."""
    setup_logging()
    service = BotService(config_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if scan:
            service.start_scanner()
        yield
        service.stop_scanner()

    app = FastAPI(title="PW136AutoBot", docs_url="/api/docs", openapi_url="/api/openapi.json",
                  lifespan=lifespan)
    app.state.service = service

    @app.exception_handler(BotError)
    async def _bot_error(_, exc: BotError):
        return JSONResponse(status_code=400, content={"detail": exc.message})

    # ---- конструктор ---------------------------------------------------------
    @app.get("/api/catalog")
    def catalog() -> list[dict[str, Any]]:
        return service.catalog()

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return service.get_config()

    @app.put("/api/config")
    def put_config(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return service.update_config(payload)

    # ---- керування -----------------------------------------------------------
    @app.get("/api/state")
    def state() -> dict[str, Any]:
        return service.state()

    @app.post("/api/control/start")
    def start(req: StartRequest) -> dict[str, Any]:
        service.start(dry_run=req.dry_run, only=req.windows)
        return service.state()

    @app.post("/api/control/stop")
    def stop() -> dict[str, Any]:
        service.stop()
        return service.state()

    # ---- вікна ---------------------------------------------------------------
    @app.get("/api/windows")
    def windows(rescan: bool = Query(default=True)) -> list[dict[str, Any]]:
        return service.discover_windows(rescan=rescan)

    @app.post("/api/windows/{name}/assign")
    def assign(name: str, req: AssignRequest) -> dict[str, Any]:
        """Закріпити вікно за персонажем вручну — коли OCR не впорався з ніком."""
        return service.assign_window(name, req.nick)

    @app.post("/api/windows/{name}/unpin")
    def unpin(name: str) -> dict[str, Any]:
        """Скинути закріплення вікна й прочитати нік заново."""
        return service.unpin_window(name)

    @app.get("/api/settings-schema")
    def settings_schema() -> dict[str, Any]:
        """Схема загальних налаштувань програми: сторінка будує з неї форму."""
        from app.config.schemas import AppSettings

        return AppSettings.model_json_schema()

    @app.post("/api/scan")
    def scan_now() -> dict[str, Any]:
        """Не чекати наступного проходу сканера: знайти вікна й прочитати ніки просто зараз."""
        service.scan_once()
        return service.state()

    @app.post("/api/windows/{name}/press")
    def press(name: str, req: PressRequest) -> dict[str, Any]:
        return service.press(name, req.key, req.times, req.interval)

    @app.get("/api/windows/{name}/frame.png")
    def frame(name: str, overlay: str = Query(default=""), scale: float = Query(default=0.5, gt=0, le=1)):
        return Response(content=service.frame_png(name, overlay=overlay, scale=scale),
                        media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    @app.post("/api/windows/{name}/calibrate")
    def calibrate(name: str, req: CalibrateRequest) -> dict[str, Any]:
        return service.calibrate(name, req.what, req.x, req.y)

    @app.get("/api/windows/{name}/loot-check")
    def loot_check(name: str) -> dict[str, Any]:
        return service.loot_check(name)

    # ---- статика -------------------------------------------------------------
    # Без кешу: інакше браузер тримає стару версію скрипта після кожної правки
    # і сторінка «не працює» з нізвідки.
    NO_CACHE = {"Cache-Control": "no-store, max-age=0"}

    @app.get("/")
    def index() -> FileResponse:
        page = STATIC_DIR / "index.html"
        if not page.exists():
            raise HTTPException(status_code=500, detail="нема static/index.html")
        return FileResponse(page, headers=NO_CACHE)

    @app.get("/static/{name}")
    def static_file(name: str) -> FileResponse:
        path = (STATIC_DIR / name).resolve()
        if not path.is_file() or STATIC_DIR.resolve() not in path.parents:
            raise HTTPException(status_code=404, detail="нема такого файлу")
        return FileResponse(path, headers=NO_CACHE)

    return app


def run(host: str = "127.0.0.1", port: int = 8765, config_path: Path | None = None) -> None:
    import uvicorn

    uvicorn.run(create_app(config_path), host=host, port=port, log_level="warning")
