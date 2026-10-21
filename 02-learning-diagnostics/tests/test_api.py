import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import httpx  # 显式探测可选的 API 测试依赖。
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "需要 fastapi/httpx")
class ApiTests(unittest.TestCase):
    def setUp(self):
        from app.main import create_app
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.tmp.name))
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.app.state.service.store.close()
        self.tmp.cleanup()

    def test_schema_sample_and_empty_report_list(self):
        schema = self.client.get("/api/diagnostics/schema")
        self.assertEqual(schema.status_code, 200)
        self.assertIn("rubric", schema.json())
        self.assertIn("syllabus", schema.json())
        sample = self.client.get("/api/diagnostics/sample")
        self.assertIn("text/csv", sample.headers["content-type"])
        self.assertIn("学号", sample.text)
        self.assertIn("attachment", sample.headers["content-disposition"])
        self.assertEqual(self.client.get("/api/diagnostics/reports").json(), {"reports": []})

    def test_demo_detail_and_markdown_export(self):
        response = self.client.post("/api/diagnostics/demo")
        self.assertEqual(response.status_code, 200)
        report = response.json()
        self.assertTrue(report["is_demo"])
        self.assertGreater(report["summary"]["record_count"], 0)
        self.assertEqual(self.client.get(f"/api/diagnostics/reports/{report['id']}").json(), report)
        markdown = self.client.get(f"/api/diagnostics/reports/{report['id']}/export")
        self.assertEqual(markdown.status_code, 200)
        self.assertIn("text/markdown", markdown.headers["content-type"])
        self.assertIn("补讲清单", markdown.text)
        self.assertIn("参考答案", markdown.text)
        self.assertEqual(self.client.get("/api/diagnostics/reports/nope").status_code, 404)

    def test_upload_accepts_custom_progress_and_rejects_invalid_inputs(self):
        raw = self.client.get("/api/diagnostics/sample").content
        syllabus = self.client.get("/api/diagnostics/schema").json()["syllabus"]
        syllabus["taught_through_week"] = 0
        response = self.client.post("/api/diagnostics/upload", files={
            "file": ("class.csv", io.BytesIO(raw), "text/csv"),
            "syllabus": ("syllabus.json", io.BytesIO(json.dumps(syllabus).encode()), "application/json")},
            data={"class_name": "测试班级"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["summary"]["average_mastery"])
        self.assertEqual(response.json()["class_name"], "测试班级")
        bad = self.client.post("/api/diagnostics/upload", files={"file": ("bad.csv", b"invalid")})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("detail", bad.json())
        bad = self.client.post("/api/diagnostics/upload", files={"file": ("bad.txt", raw)})
        self.assertEqual(bad.status_code, 400)
        bad = self.client.post("/api/diagnostics/upload", files={"file": ("a.csv", raw), "rubric": ("r.json", b"[]")})
        self.assertEqual(bad.status_code, 400)

    def test_static_and_health(self):
        self.assertTrue(self.client.get("/healthz").json()["ok"])
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_optional_token_controls_all_report_operations(self):
        with patch.dict(os.environ, {"EDU_API_TOKEN": "test-secret"}):
            self.assertTrue(self.client.get("/api/auth/check").json()["required"])
            self.assertEqual(self.client.get("/api/diagnostics/schema").status_code, 401)
            headers = {"Authorization": "Bearer test-secret"}
            response = self.client.post("/api/diagnostics/demo", headers=headers)
            self.assertEqual(response.status_code, 200)
            report_id = response.json()["id"]
            self.assertEqual(self.client.get(f"/api/diagnostics/reports/{report_id}/export").status_code, 401)
            review = self.client.post(f"/api/diagnostics/reports/{report_id}/review", headers=headers,
                                      json={"reviewer": "教师", "note": "已核对"})
            self.assertEqual(review.status_code, 200)
            self.assertEqual(review.json()["review"]["status"], "reviewed")
            self.assertTrue(self.client.get("/healthz").json()["ok"])

    def test_oversized_diagnostic_dimensions_return_friendly_400(self):
        sample = self.client.get("/api/diagnostics/sample").content
        with patch("app.core.diagnosis.MAX_MATRIX_CELLS", 4, create=True):
            response = self.client.post("/api/diagnostics/upload", files={"file": ("class.csv", sample)})
        self.assertEqual(response.status_code, 400)
        self.assertIn("学生", response.json()["detail"])
        self.assertIn("知识点", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
