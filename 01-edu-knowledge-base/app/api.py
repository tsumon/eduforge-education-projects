"""FastAPI 路由：只做参数解析与 HTTP 映射，业务全部在 service 层。

鉴权：设置环境变量 EDU_API_TOKEN 后，所有 /api/knowledge/* 请求须带 `Authorization: Bearer <token>`；
未设置则不鉴权（本地/内网使用）。
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .core.store import CATEGORIES
from .service import KnowledgeService


class DocumentIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    category: str = Field("课程资料")


class AskIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


class FeedbackIn(BaseModel):
    helpful: bool
    note: str = Field("", max_length=500)


def _download(text: str, filename: str, media_type: str) -> Response:
    return Response(
        content=text.encode("utf-8"),
        media_type=f"{media_type}; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def require_token(authorization: str | None = Header(default=None)):
    token = os.environ.get("EDU_API_TOKEN", "")
    if not token:
        return
    if authorization != f"Bearer {token}":
        raise HTTPException(401, "需要有效的访问令牌")


def create_router(data_dir: Path | str | None = None, service: KnowledgeService | None = None) -> APIRouter:
    svc = service or KnowledgeService(Path(data_dir or "runtime"))
    router = APIRouter(prefix="/api/knowledge", tags=["knowledge"], dependencies=[Depends(require_token)])

    # ---- 概览 ----
    @router.get("/stats")
    def stats():
        return svc.stats()

    @router.post("/index/rebuild")
    def rebuild_index():
        svc.rebuild_index(force_vectors=True)
        return svc.stats()

    # ---- 资料 ----
    @router.get("/documents")
    def list_documents():
        return {"documents": svc.list_documents(), "categories": list(CATEGORIES)}

    @router.get("/documents/{doc_id}")
    def get_document(doc_id: str):
        doc = svc.get_document(doc_id)
        if not doc:
            raise HTTPException(404, "资料不存在")
        return doc

    @router.post("/documents", status_code=201)
    def add_document(body: DocumentIn):
        try:
            return svc.add_document(body.title, body.content, body.category)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/import")
    async def import_files(files: list[UploadFile] = File(...), category: str = Form("课程资料")):
        if category not in CATEGORIES:
            raise HTTPException(400, f"分类必须是 {'/'.join(CATEGORIES)} 之一")
        payload = [(f.filename or "", await f.read()) for f in files]
        result = svc.import_files(payload, category=category)
        if not result["imported"] and result["failed"]:
            raise HTTPException(400, {"message": "全部文件导入失败", **result})
        return result

    @router.delete("/documents/{doc_id}")
    def delete_document(doc_id: str):
        if not svc.delete_document(doc_id):
            raise HTTPException(404, "资料不存在")
        return {"deleted": doc_id}

    # ---- 问答 ----
    @router.post("/ask")
    def ask(body: AskIn):
        try:
            return svc.ask(body.question)
        except ValueError as e:
            raise HTTPException(400, str(e))

    # ---- 记录 ----
    @router.get("/history")
    def history(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        refused: bool | None = Query(None, description="true 只看拒答，false 只看已回答"),
        q: str = Query("", max_length=100, description="按问题关键字过滤"),
    ):
        return svc.history(limit=limit, offset=offset, refused=refused, q=q.strip())

    @router.get("/history/export.jsonl")
    def history_export_jsonl():
        return _download(svc.export_jsonl(), "knowledge_qa_history.jsonl", "application/x-ndjson")

    @router.get("/history/{hid}")
    def history_item(hid: str):
        rec = svc.get_history(hid)
        if not rec:
            raise HTTPException(404, "记录不存在")
        return rec

    @router.post("/history/{hid}/feedback")
    def feedback(hid: str, body: FeedbackIn):
        rec = svc.set_feedback(hid, body.helpful, body.note)
        if not rec:
            raise HTTPException(404, "记录不存在")
        return rec

    @router.get("/export/{hid}")
    def export(hid: str):
        md = svc.export_markdown(hid)
        if md is None:
            raise HTTPException(404, "记录不存在")
        return _download(md, f"问答记录_{hid}.md", "text/markdown")

    # ---- 示例数据 ----
    @router.get("/sample")
    def sample():
        return {"questions": svc.sample_questions()}

    @router.post("/sample/load")
    def load_samples():
        return svc.load_samples()

    return router
