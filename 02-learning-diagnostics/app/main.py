import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .api import create_router
from .service import DiagnosticsService
from .settings import AgentSettings

ROOT = Path(__file__).resolve().parent.parent


def create_app(data_dir: Path | str | None = None, *, settings: AgentSettings | None = None, service=None) -> FastAPI:
    if service is None:
        settings = settings or AgentSettings.from_env()
        service = DiagnosticsService(Path(data_dir or os.environ.get("DIAGNOSTICS_DATA_DIR") or ROOT / "runtime"),
            agent_client=settings.client(), agent_enabled=settings.enabled, agent_max_steps=settings.max_steps)

    @asynccontextmanager
    async def lifespan(app):
        yield
        service.store.close()

    app = FastAPI(title="EduForge 学情诊断 Agent", version="2.0.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.service = service
    app.include_router(create_router(service=service))

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True, "version": app.version, "mode": "agent" if service.agent_enabled and service.agent_client.configured() else "statistics_preview",
                "agent_configured": service.agent_client.configured()}

    @app.get("/api/auth/check", include_in_schema=False)
    def auth_check():
        return {"required": bool(os.environ.get("EDU_API_TOKEN"))}

    app.mount("/", StaticFiles(directory=str(ROOT / "static"), html=True, check_dir=False), name="static")
    return app
