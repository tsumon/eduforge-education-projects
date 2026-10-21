import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .api import create_router
from .service import DiagnosticsService

ROOT = Path(__file__).resolve().parent.parent


def create_app(data_dir: Path | str | None = None) -> FastAPI:
    service = DiagnosticsService(Path(data_dir or os.environ.get("DIAGNOSTICS_DATA_DIR") or ROOT / "runtime"))
    app = FastAPI(title="EduForge 学情诊断", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.service = service
    app.include_router(create_router(service=service))

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True, "version": app.version, "mode": "rule_based"}

    @app.get("/api/auth/check", include_in_schema=False)
    def auth_check():
        return {"required": bool(os.environ.get("EDU_API_TOKEN"))}

    app.mount("/", StaticFiles(directory=str(ROOT / "static"), html=True, check_dir=False), name="static")
    return app


app = create_app()
