"""应用入口：create_app(data_dir=None) → FastAPI。

    uvicorn app.main:app --reload
或  python run.py
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import create_router
from .service import KnowledgeService

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"


def create_app(data_dir: Path | str | None = None) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("EDU_DATA_DIR") or ROOT / "runtime")
    service = KnowledgeService(data_dir)
    app = FastAPI(title="EduForge 教育知识库", version="1.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.service = service
    app.include_router(create_router(service=service))

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        s = service.store.stats()
        return {"ok": True, "version": app.version, "documents": s["document_count"], "chunks": s["chunk_count"]}

    @app.get("/api/auth/check", include_in_schema=False)
    def auth_check():
        """前端用来判断是否需要令牌（本接口本身不鉴权）。"""
        return {"required": bool(os.environ.get("EDU_API_TOKEN"))}

    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


app = create_app()
