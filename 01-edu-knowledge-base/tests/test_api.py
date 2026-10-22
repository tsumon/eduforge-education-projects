"""API 集成测试（需要 fastapi + httpx；缺失时自动跳过）。"""
import io
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.settings import RAGSettings
except ImportError:  # pragma: no cover
    TestClient = None


@unittest.skipIf(TestClient is None, "未安装 fastapi/httpx")
class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = create_app(Path(cls.tmp.name), settings=RAGSettings(profile="lexical", llm_provider="none"))
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.state.service.store.close()
        cls.tmp.cleanup()

    def test_01_empty_stats_and_refusal(self):
        r = self.client.get("/api/knowledge/stats")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["document_count"], 0)
        r = self.client.post("/api/knowledge/ask", json={"question": "递归深度"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["refused"])

    def test_02_load_samples_and_ask(self):
        r = self.client.post("/api/knowledge/sample/load")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.json()["imported"]), 7)
        r = self.client.get("/api/knowledge/documents")
        docs = r.json()["documents"]
        self.assertGreaterEqual(len(docs), 7)
        r = self.client.post("/api/knowledge/ask", json={"question": "Python 默认递归深度限制是多少？"})
        body = r.json()
        self.assertFalse(body["refused"])
        self.assertEqual(body["mode"], "extractive")
        self.assertTrue(body["citations"])
        for key in ("id", "question", "answer", "mode", "citations", "refused", "created_at"):
            self.assertIn(key, body)

    def test_03_documents_crud_and_validation(self):
        r = self.client.post("/api/knowledge/documents", json={"title": "测试", "content": "短", "category": "课程文档"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/knowledge/documents", json={
            "title": "缓存讲义", "content": "缓存穿透是指查询一个一定不存在的数据，由于缓存不命中而直接打到数据库。", "category": "课程文档"})
        self.assertEqual(r.status_code, 201)
        doc_id = r.json()["id"]
        r = self.client.delete(f"/api/knowledge/documents/{doc_id}")
        self.assertEqual(r.status_code, 200)
        r = self.client.delete(f"/api/knowledge/documents/{doc_id}")
        self.assertEqual(r.status_code, 404)

    def test_04_import_multipart(self):
        files = [
            ("files", ("讲义.md", io.BytesIO("# 导入测试\n\n这是通过 multipart 上传的测试资料，内容足够长以通过校验。".encode("utf-8")), "text/markdown")),
            ("files", ("bad.exe", io.BytesIO(b"xx"), "application/octet-stream")),
        ]
        r = self.client.post("/api/knowledge/import", files=files, data={"category": "课程资料"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["imported"]), 1)
        self.assertEqual(len(r.json()["failed"]), 1)
        r = self.client.post("/api/knowledge/import", files=[("files", ("bad.exe", io.BytesIO(b"xx"), "application/octet-stream"))])
        self.assertEqual(r.status_code, 400)

    def test_05_history_feedback_and_export(self):
        self.client.post("/api/knowledge/ask", json={"question": "作业迟交怎么扣分？"})
        r = self.client.get("/api/knowledge/history?limit=10&refused=false")
        body = r.json()
        self.assertTrue(body["history"])
        self.assertIn("total", body)
        hid = body["history"][0]["id"]
        r = self.client.post(f"/api/knowledge/history/{hid}/feedback", json={"helpful": True, "note": "准确"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["feedback"]["helpful"])
        r = self.client.get(f"/api/knowledge/export/{hid}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/markdown", r.headers["content-type"])
        self.assertIn("# 问答记录", r.text)
        self.assertEqual(self.client.get("/api/knowledge/export/qa_nope").status_code, 404)
        r = self.client.get("/api/knowledge/history/export.jsonl")
        self.assertEqual(r.status_code, 200)
        self.assertIn("x-ndjson", r.headers["content-type"])
        r = self.client.get(f"/api/knowledge/documents/{self.client.get('/api/knowledge/documents').json()['documents'][0]['id']}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("chunks", r.json())

    def test_07_token_auth(self):
        import os
        os.environ["EDU_API_TOKEN"] = "secret-token"
        try:
            self.assertEqual(self.client.get("/api/knowledge/stats").status_code, 401)
            self.assertEqual(self.client.get("/api/knowledge/stats", headers={"Authorization": "Bearer wrong"}).status_code, 401)
            self.assertEqual(self.client.get("/api/knowledge/stats", headers={"Authorization": "Bearer secret-token"}).status_code, 200)
            self.assertTrue(self.client.get("/api/auth/check").json()["required"])
            self.assertEqual(self.client.get("/healthz").status_code, 200)
        finally:
            del os.environ["EDU_API_TOKEN"]

    def test_06_sample_questions_and_static(self):
        r = self.client.get("/api/knowledge/sample")
        self.assertTrue(r.json()["questions"])
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("教育知识库", r.text)


if __name__ == "__main__":
    unittest.main()
