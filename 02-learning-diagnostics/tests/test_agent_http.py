"""测试 API 工具协议，不调用外部模型；真实执行 LangGraph 与 SQLite。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import AgentSettings
from app.service import DiagnosticsService
from tests.test_agent import call, plan


@pytest.fixture
def provider():
    calls, control = [], {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append({"body": body, "authorization": self.headers.get("Authorization"), "path": self.path})
            if control.get("fail"):
                self.send_error(503, "fixture-secret-and-provider-address")
                return
            observations = [m for m in body["messages"] if m["role"] == "tool"]
            responses = [call("get_class_summary", {}), call("get_rubric_and_progress", {}, "progress"),
                         call("inspect_topic", {"topic": "变量与类型"}, "topic"),
                         {"role": "assistant", "content": json.dumps(plan(), ensure_ascii=False)}]
            message = responses[min(len(observations), len(responses) - 1)]
            raw = json.dumps({"choices": [{"message": message, "finish_reason": "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", calls, control
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_upload_agent_review_rerun_and_history_via_real_http(provider, tmp_path):
    base, calls, _ = provider
    settings = AgentSettings(base_url=base, model="fixture-tools", api_key="fixture-chat-only")
    with TestClient(create_app(tmp_path, settings=settings)) as client:
        raw = client.get("/api/diagnostics/sample").content
        response = client.post("/api/diagnostics/upload", files={"file": ("class.csv", raw, "text/csv")},
                               data={"goal": "核对变量类型的失分证据并设计基础练习"})
        assert response.status_code == 200
        report = response.json()
        assert report["mode"] == "agent" and report["agent"]["status"] == "completed"
        report_id, run_id = report["id"], report["agent"]["id"]
        review = client.post(f"/api/diagnostics/reports/{report_id}/review", json={"reviewer": "教师", "agent_run_id": run_id})
        assert review.json()["agent"]["review"]["status"] == "reviewed"
        new = client.post(f"/api/diagnostics/reports/{report_id}/agent", json={"goal": "改出一道类型转换的基础变式"}).json()
        assert new["status"] == "completed" and new["id"] != run_id
        assert client.post(f"/api/diagnostics/reports/{report_id}/review", json={"reviewer": "教师", "agent_run_id": run_id}).status_code == 400
        latest = client.get(f"/api/diagnostics/reports/{report_id}").json()
        assert latest["review"]["status"] == "pending"
        assert len(client.get(f"/api/diagnostics/reports/{report_id}/agent-runs").json()["runs"]) == 2
        assert "Agent 诊断建议" in client.get(f"/api/diagnostics/reports/{report_id}/export").text
        assert client.post("/api/diagnostics/reports/missing/agent", json={}).status_code == 404
        assert client.post(f"/api/diagnostics/reports/{report_id}/agent", json={"goal": " "}).status_code == 400
        assert base not in json.dumps(latest) and "fixture-chat-only" not in json.dumps(latest)
    reopened = DiagnosticsService(tmp_path)
    try:
        assert reopened.get_report(report_id)["agent"] == latest["agent"]
    finally:
        reopened.store.close()
    assert len(calls) == 8 and all(c["path"] == "/v1/chat/completions" for c in calls)
    assert all(c["authorization"] == "Bearer fixture-chat-only" for c in calls)


def test_provider_error_is_recorded_and_never_replaced_by_a_fake_agent_result(provider, tmp_path):
    base, _, control = provider
    control["fail"] = True
    with TestClient(create_app(tmp_path, settings=AgentSettings(base_url=base, model="fixture"))) as client:
        report = client.post("/api/diagnostics/demo").json()
        assert report["agent"]["status"] == "failed" and report["agent"]["result"] is None
        assert report["mode"] == "statistics_preview" and report["summary"]["student_count"] > 0
        assert "fixture-secret" not in json.dumps(report)
        assert client.get(f"/api/diagnostics/reports/{report['id']}/agent-runs").json()["runs"][0]["status"] == "failed"


def test_agent_endpoints_share_existing_token_protection(provider, tmp_path, monkeypatch):
    base, calls, _ = provider
    monkeypatch.setenv("EDU_API_TOKEN", "fixture-access-only")
    with TestClient(create_app(tmp_path, settings=AgentSettings(base_url=base, model="fixture"))) as client:
        assert client.post("/api/diagnostics/reports/any/agent", json={}).status_code == 401
        assert client.get("/api/diagnostics/reports/any/agent-runs").status_code == 401
        assert not calls
        headers = {"Authorization": "Bearer fixture-access-only"}
        report = client.post("/api/diagnostics/demo", headers=headers).json()
        assert report["agent"]["status"] == "completed"
