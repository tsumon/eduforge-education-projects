"""应用工厂：python run.py；或 uvicorn app.main:create_app --factory。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from .api import create_router
from .service import KnowledgeService
from .settings import RAGSettings
from .core.answer import AnswerPolicy
from .core.llm import LLMClient

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"


def create_app(data_dir: Path | str | None = None, *, settings: RAGSettings | None = None, service=None) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("EDU_DATA_DIR") or ROOT / "runtime")
    if service is None:
        settings = settings or RAGSettings.from_env()
        settings.validate_configured()
        vector = None
        if settings.profile == "rag":
            from .core.embeddings import EmbeddingClient
            from .core.vector_store import VectorIndex
            embedder = EmbeddingClient(base_url=settings.embedding_base_url, api_key=settings.embedding_api_key,
                model=settings.embedding_model, dimension=settings.embedding_dimension, version=settings.embedding_version,
                query_prefix=settings.embedding_query_prefix, timeout=settings.timeout)
            vector = VectorIndex(data_dir / "qdrant", embedder)
        try:
            if settings.llm_provider == "openai":
                llm = LLMClient(base_url=settings.llm_base_url, api_key=settings.llm_api_key,
                                model=settings.llm_model, timeout=settings.timeout)
            else:
                llm = LLMClient(base_url="", model="", api_key="")
            agent_client = None
            if settings.agent_enabled and settings.llm_provider == "openai":
                from .agent_runtime import ToolChatClient
                agent_client = ToolChatClient(base_url=settings.llm_base_url, api_key=settings.llm_api_key,
                                             model=settings.llm_model, timeout=settings.timeout)
            service = KnowledgeService(data_dir, llm=llm, vector_index=vector,
                                       policy=AnswerPolicy(semantic_min_score=settings.semantic_min_score),
                                       agent_client=agent_client, agent_max_steps=settings.agent_max_steps)
        except Exception:
            if vector:
                vector.close()
            raise

    @asynccontextmanager
    async def lifespan(app):
        yield
        service.close()

    app = FastAPI(title="EduForge 教育知识库 Agent", version="3.0.0", lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.service = service
    app.include_router(create_router(service=service))

    @app.exception_handler(RuntimeError)
    async def unavailable(request, exc):
        return JSONResponse(status_code=503, content={"detail": "知识库依赖暂不可用，请检查模型 API 配置和向量索引；已保存的资料可在设置中重建索引。"})

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        s = service.store.stats()
        ready = not bool(service._index_error)
        return JSONResponse(status_code=200 if ready else 503, content={
            "ok": ready, "version": app.version, "documents": s["document_count"], "chunks": s["chunk_count"],
            "retrieval": "hybrid" if service.vector_index else "lexical",
        })

    @app.get("/api/auth/check", include_in_schema=False)
    def auth_check():
        """前端用来判断是否需要令牌（本接口本身不鉴权）。"""
        return {"required": bool(os.environ.get("EDU_API_TOKEN"))}

    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app
