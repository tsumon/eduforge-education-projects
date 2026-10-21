import tempfile
import unittest
from pathlib import Path


class ServiceTests(unittest.TestCase):
    def setUp(self):
        from app.service import DiagnosticsService
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = DiagnosticsService(Path(self.tmp.name))

    def tearDown(self):
        self.svc.store.close()
        self.tmp.cleanup()

    def test_demo_is_explicit_synthetic_and_persists(self):
        report = self.svc.demo()
        self.assertTrue(report["is_demo"])
        self.assertGreater(report["summary"]["student_count"], 3)
        self.assertTrue(any("合成" in w for w in report["warnings"]))
        self.assertEqual(self.svc.get_report(report["id"]), report)
        self.assertEqual(self.svc.reports()["reports"][0]["id"], report["id"])
        self.assertIn("补讲清单", self.svc.export_markdown(report["id"]))
        self.assertIsNone(self.svc.export_markdown("missing"))

    def test_upload_uses_supplied_progress_without_changing_defaults(self):
        import json
        raw = self.svc.sample("csv")[1]
        syllabus = self.svc.schema()["syllabus"]
        syllabus["taught_through_week"] = 0
        report = self.svc.upload("class.csv", raw.encode("utf-8"), syllabus_raw=json.dumps(syllabus).encode("utf-8"), class_name="自定义班级")
        self.assertEqual(report["class_name"], "自定义班级")
        self.assertIsNone(report["summary"]["average_mastery"])
        self.assertEqual(report["reteaching"], [])
        self.assertGreater(self.svc.schema()["syllabus"]["taught_through_week"], 0)

    def test_input_limits_and_malformed_configs_are_rejected(self):
        for filename, raw, kwargs in [("a.exe", b"x", {}), ("a.csv", b"x" * (8 * 1024 * 1024 + 1), {}),
                                      ("a.csv", b"x", {"syllabus_raw": b"[]"}), ("a.csv", b"x", {"rubric_raw": b"not json"})]:
            with self.subTest(filename=filename, kwargs=kwargs), self.assertRaises(ValueError):
                self.svc.upload(filename, raw, **kwargs)

    def test_report_survives_reopen_and_isolated_from_other_data_directories(self):
        from app.service import DiagnosticsService
        report = self.svc.demo()
        reopened = DiagnosticsService(Path(self.tmp.name))
        with tempfile.TemporaryDirectory() as other_dir:
            other = DiagnosticsService(other_dir)
            try:
                self.assertEqual(reopened.get_report(report["id"]), report)
                self.assertIsNone(other.get_report(report["id"]))
            finally:
                reopened.store.close()
                other.store.close()

    def test_manual_review_is_explicit_audited_and_persistent(self):
        report = self.svc.demo()
        self.assertEqual(report["review"]["status"], "pending")
        updated = self.svc.review(report["id"], reviewer="授课教师", note="已核对原始成绩")
        self.assertEqual(updated["review"]["status"], "reviewed")
        self.assertEqual(updated["review"]["reviewer"], "授课教师")
        self.assertTrue(updated["review"]["reviewed_at"])
        self.assertEqual([event["action"] for event in updated["audit_log"]], ["created", "reviewed"])
        self.assertEqual(self.svc.get_report(report["id"]), updated)
        self.assertEqual(updated["methodology"], report["methodology"])
        pending = self.svc.review(report["id"], reviewer="授课教师", reviewed=False)
        self.assertEqual(pending["review"]["status"], "pending")
        self.assertEqual(len(pending["audit_log"]), 3)
        with self.assertRaises(ValueError):
            self.svc.review(report["id"], reviewer="")


if __name__ == "__main__":
    unittest.main()
