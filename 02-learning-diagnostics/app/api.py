from pathlib import Path
import os
import hmac
from urllib.parse import quote
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from .service import DiagnosticsService, MAX_BYTES, MAX_CONFIG_BYTES


def download(filename: str, text: str, media_type: str):
    return Response(text.encode("utf-8-sig" if media_type == "text/csv" else "utf-8"), media_type=media_type,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


class ReviewIn(BaseModel):
    reviewer: str = Field(..., min_length=1, max_length=80)
    note: str = Field("", max_length=500)
    reviewed: bool = True


def require_token(authorization: str | None = Header(None)):
    token = os.environ.get("EDU_API_TOKEN", "")
    if token and not hmac.compare_digest((authorization or "").encode("utf-8"), f"Bearer {token}".encode("utf-8")):
        raise HTTPException(401, "需要有效的访问令牌")


def create_router(data_dir: Path | str | None = None, service: DiagnosticsService | None = None) -> APIRouter:
    svc = service or DiagnosticsService(Path(data_dir or "runtime"))
    router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"], dependencies=[Depends(require_token)])

    @router.get("/schema")
    def schema():
        return svc.schema()

    @router.get("/sample")
    def sample(kind: str = "csv"):
        try:
            return download(*svc.sample(kind))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.post("/demo")
    def demo():
        return svc.demo()

    @router.post("/upload")
    async def upload(file: UploadFile = File(...), syllabus: UploadFile | None = File(None),
                     rubric: UploadFile | None = File(None), class_name: str = Form("")):
        try:
            return svc.upload(file.filename or "", await file.read(MAX_BYTES + 1),
                              syllabus_raw=await syllabus.read(MAX_CONFIG_BYTES + 1) if syllabus else None,
                              rubric_raw=await rubric.read(MAX_CONFIG_BYTES + 1) if rubric else None,
                              class_name=class_name)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.get("/reports")
    def reports(limit: int = Query(100, ge=1, le=200)):
        return svc.reports(limit)

    @router.get("/reports/{report_id}")
    def detail(report_id: str):
        report = svc.get_report(report_id)
        if report is None:
            raise HTTPException(404, "报告不存在")
        return report

    @router.get("/reports/{report_id}/export")
    def export(report_id: str):
        text = svc.export_markdown(report_id)
        if text is None:
            raise HTTPException(404, "报告不存在")
        return download(f"学情诊断_{report_id}.md", text, "text/markdown")

    @router.post("/reports/{report_id}/review")
    def review(report_id: str, body: ReviewIn):
        try:
            report = svc.review(report_id, body.reviewer, body.note, body.reviewed)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if report is None:
            raise HTTPException(404, "报告不存在")
        return report

    return router
