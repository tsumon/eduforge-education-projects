"""service 层测试：引用来源可追溯、资料不足拒答、历史与导出、文档增删。不依赖 FastAPI。"""
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.core.answer import REFUSAL_PREFIX
from app.core.llm import LLMClient
from app.core.store import KnowledgeStore
from app.service import KnowledgeService


class _FakeLLM(LLMClient):
    """用于测试的假模型：按脚本返回文本。"""

    def __init__(self, reply: str):
        super().__init__(base_url="http://fake", api_key="x", model="fake")
        self.reply = reply
        self.calls = 0

    def answer(self, question, passages):
        self.calls += 1
        return self.reply


class ServiceWithSamplesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.svc = KnowledgeService(Path(cls.tmp.name), llm=LLMClient(base_url="", model=""))
        cls.svc.load_samples()

    @classmethod
    def tearDownClass(cls):
        cls.svc.store.close()
        cls.tmp.cleanup()

    def test_samples_loaded(self):
        docs = self.svc.list_documents()
        self.assertGreaterEqual(len(docs), 7)
        self.assertTrue(all(d["chunk_count"] > 0 for d in docs))
        self.assertEqual(self.svc.load_samples()["imported"], [])  # 幂等

    def test_sample_questions_behave_as_labelled(self):
        """演示问题中的 answer/refuse 标签必须与系统实际行为一致。"""
        failures = []
        for q in self.svc.sample_questions():
            rec = self.svc.ask(q["question"])
            got = "refuse" if rec["refused"] else "answer"
            if got != q["expect"]:
                failures.append((q["question"], q["expect"], got, rec["diagnostics"].get("reason")))
        self.assertEqual(failures, [])

    def test_answer_has_citations_from_stored_chunks_only(self):
        rec = self.svc.ask("Python 默认递归深度限制是多少？")
        self.assertFalse(rec["refused"])
        self.assertEqual(rec["mode"], "extractive")
        self.assertTrue(rec["citations"])
        stored = {c["id"]: c for c in self.svc.store.all_chunks()}
        for c in rec["citations"]:
            self.assertIn(c["chunk_id"], stored)
            self.assertEqual(stored[c["chunk_id"]]["text"], c["text"])
            self.assertEqual(stored[c["chunk_id"]]["document_id"], c["document_id"])
            self.assertIn(f"[{c['n']}]", rec["answer"])
            self.assertIn(c["excerpt"], c["text"])  # 摘录必须是分片原文的子串
            self.assertIn(c["excerpt"], rec["answer"])
        self.assertIn("1000", rec["answer"])

    def test_secondary_evidence_must_cover_primary_segments(self):
        """次要证据不能只是"提到同一门课"：必须覆盖主证据覆盖到的全部词组。"""
        rec = self.svc.ask("Python 程序设计课程的总评成绩由哪几部分构成？")
        self.assertFalse(rec["refused"])
        headings = "|".join(c["heading"] for c in rec["citations"])
        self.assertIn("考核方式", headings)
        self.assertNotIn("项目背景", headings)
        self.assertNotIn("课程基本信息", headings)
        rec = self.svc.ask("什么是 LEGB 作用域规则？")
        self.assertNotIn("教学进度", "|".join(c["heading"] for c in rec["citations"]))

    def test_rerank_prefers_chunk_covering_all_question_parts(self):
        """BM25 首位是反复提到"成绩管理系统"的背景段，但证据首位应是同时提到并发与响应时间的段落。"""
        rec = self.svc.ask("成绩管理系统对并发和响应时间有什么要求？")
        self.assertFalse(rec["refused"])
        self.assertIn("并发", rec["citations"][0]["text"])
        self.assertIn("响应时间", rec["citations"][0]["text"])
        self.assertNotIn("合成演示资料", rec["answer"])

    def test_same_subject_but_unknown_fact_is_refused(self):
        """资料提到"期末考试"但没有具体日期：覆盖率不足应拒答而不是硬凑。"""
        rec = self.svc.ask("本学期的期末考试具体日期是哪天？")
        self.assertTrue(rec["refused"])

    def test_refusal_has_no_citations_and_explains(self):
        rec = self.svc.ask("量子计算中 Shor 算法的时间复杂度是多少？")
        self.assertTrue(rec["refused"])
        self.assertEqual(rec["citations"], [])
        self.assertTrue(rec["answer"].startswith(REFUSAL_PREFIX))
        self.assertIn(rec["diagnostics"]["reason"],
                      {"no_candidates", "segment_coverage_below_threshold", "coverage_below_threshold", "score_below_threshold"})

    def test_question_without_terms_is_refused(self):
        rec = self.svc.ask("的了是吗")
        self.assertTrue(rec["refused"])
        self.assertEqual(rec["diagnostics"]["reason"], "no_question_terms")

    def test_empty_question_rejected(self):
        with self.assertRaises(ValueError):
            self.svc.ask("   ")

    def test_history_and_export(self):
        rec = self.svc.ask("作业迟交怎么扣分？")
        page = self.svc.history()
        self.assertEqual(page["history"][0]["id"], rec["id"])
        self.assertGreaterEqual(page["total"], 1)
        md = self.svc.export_markdown(rec["id"])
        self.assertIn("# 问答记录", md)
        self.assertIn("作业迟交怎么扣分", md)
        self.assertIn("## 引用资料", md)
        self.assertIn("判定依据", md)
        self.assertIsNone(self.svc.export_markdown("qa_not_exist"))

    def test_history_filters_and_pagination(self):
        self.svc.ask("红楼梦的作者是谁？")
        self.svc.ask("两个重要极限分别是什么？")
        refused = self.svc.history(refused=True)
        self.assertTrue(all(h["refused"] for h in refused["history"]))
        answered = self.svc.history(refused=False)
        self.assertTrue(all(not h["refused"] for h in answered["history"]))
        kw = self.svc.history(q="极限")
        self.assertTrue(all("极限" in h["question"] for h in kw["history"]))
        page1 = self.svc.history(limit=1, offset=0)
        page2 = self.svc.history(limit=1, offset=1)
        self.assertNotEqual(page1["history"][0]["id"], page2["history"][0]["id"])

    def test_feedback_and_jsonl_export(self):
        rec = self.svc.ask("两个重要极限分别是什么？")
        self.assertIsNone(rec["feedback"])
        upd = self.svc.set_feedback(rec["id"], helpful=False, note="少了等价形式")
        self.assertEqual(upd["feedback"]["helpful"], False)
        self.assertEqual(upd["feedback"]["note"], "少了等价形式")
        upd = self.svc.set_feedback(rec["id"], helpful=True)  # 可覆盖
        self.assertTrue(upd["feedback"]["helpful"])
        self.assertIsNone(self.svc.set_feedback("qa_nope", True))
        jsonl = self.svc.export_jsonl()
        rows = [json.loads(line) for line in jsonl.strip().split("\n")]
        self.assertTrue(any(r["id"] == rec["id"] and r["feedback"]["helpful"] for r in rows))
        self.assertTrue(all({"question", "answer", "refused", "citations"} <= set(r) for r in rows))

    def test_document_detail_has_chunks(self):
        doc = self.svc.list_documents()[0]
        detail = self.svc.get_document(doc["id"])
        self.assertEqual(len(detail["chunks"]), doc["chunk_count"])
        self.assertTrue(all(c["text"] for c in detail["chunks"]))
        self.assertIsNone(self.svc.get_document("doc_nope"))

    def test_stats_shape(self):
        s = self.svc.stats()
        for k in ("document_count", "chunk_count", "question_count", "refused_count", "categories", "llm", "policy"):
            self.assertIn(k, s)
        self.assertFalse(s["llm"]["configured"])


class ServiceMutationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = KnowledgeService(Path(self.tmp.name), llm=LLMClient(base_url="", model=""))

    def tearDown(self):
        self.svc.store.close()
        self.tmp.cleanup()

    def test_empty_index_refuses(self):
        rec = self.svc.ask("递归深度")
        self.assertTrue(rec["refused"])
        self.assertEqual(rec["diagnostics"]["reason"], "empty_index")

    def test_add_then_delete_document_updates_index(self):
        doc = self.svc.add_document(
            "测试讲义", "# 缓存\n\n缓存穿透是指查询一个一定不存在的数据，由于缓存不命中而直接打到数据库。常见对策是布隆过滤器与空值缓存。", "课程文档"
        )
        rec = self.svc.ask("什么是缓存穿透？")
        self.assertFalse(rec["refused"])
        self.assertEqual(rec["citations"][0]["document_id"], doc["id"])
        self.assertTrue(self.svc.delete_document(doc["id"]))
        self.assertFalse(self.svc.delete_document(doc["id"]))
        rec2 = self.svc.ask("什么是缓存穿透？")
        self.assertTrue(rec2["refused"])

    def test_add_document_validation(self):
        with self.assertRaises(ValueError):
            self.svc.add_document("", "x" * 50, "课程文档")
        with self.assertRaises(ValueError):
            self.svc.add_document("t", "太短", "课程文档")
        with self.assertRaises(ValueError):
            self.svc.add_document("t", "x" * 50, "不存在的分类")

    def test_import_files_partial_failure(self):
        res = self.svc.import_files([
            ("a.md", "# 讲义A\n\n这是一份足够长的测试讲义内容，用于验证导入成功路径。".encode("utf-8")),
            ("b.exe", b"bad"),
        ], category="课程资料")
        self.assertEqual(len(res["imported"]), 1)
        self.assertEqual(len(res["failed"]), 1)
        self.assertEqual(res["failed"][0]["file"], "b.exe")

    def test_corrupt_office_files_do_not_abort_the_import_batch(self):
        valid = "缓存穿透是指查询一个一定不存在的数据，常见对策是布隆过滤器与空值缓存。".encode("utf-8")
        try:
            result = self.svc.import_files([
                ("first.txt", valid),
                ("broken.docx", b"not a valid document file"),
                ("broken.pdf", b"not a valid document file"),
                ("last.md", valid),
            ])
        except Exception as exc:
            self.fail(f"损坏文件不应中断整批导入：{type(exc).__name__}: {exc}")
        self.assertEqual([d["source_name"] for d in result["imported"]], ["first.txt", "last.md"])
        self.assertEqual([d["file"] for d in result["failed"]], ["broken.docx", "broken.pdf"])
        self.assertTrue(all(item["error"] for item in result["failed"]))
        self.assertEqual(len(self.svc.list_documents()), 2)

    def test_concurrent_questions_are_all_persisted(self):
        self.svc.add_document(
            "缓存讲义", "缓存穿透是指查询一个一定不存在的数据，由于缓存不命中而直接打到数据库。常见对策是布隆过滤器与空值缓存。", "课程资料"
        )
        start = threading.Barrier(16)

        def ask(number):
            if number < 16:
                start.wait(timeout=10)
            try:
                return self.svc.ask("什么是缓存穿透？"), None
            except Exception as exc:
                return None, f"{type(exc).__name__}: {exc}"

        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(ask, range(160)))
        self.assertEqual([error for _, error in results if error], [])
        returned_ids = {record["id"] for record, _ in results}
        self.assertEqual(len(returned_ids), 160)
        page = self.svc.history(limit=200)
        self.assertEqual(page["total"], 160)
        self.assertEqual({record["id"] for record in page["history"]}, returned_ids)
        persisted = KnowledgeStore(self.svc.store.db_path)
        try:
            self.assertEqual(persisted.stats()["question_count"], 160)
        finally:
            persisted.close()

    def test_malformed_llm_responses_fall_back_and_persist(self):
        self.svc.add_document(
            "缓存讲义", "缓存穿透是指查询一个一定不存在的数据，由于缓存不命中而直接打到数据库。常见对策是布隆过滤器与空值缓存。", "课程资料"
        )
        self.svc.llm = LLMClient(base_url="http://fake", api_key="x", model="fake")
        payloads = [
            b"not json",
            b"\xff",
            b'{"choices":[{"message":{"content":null}}]}',
            b'{"choices":[{"message":{"content":42}}]}',
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                with patch("urllib.request.urlopen", return_value=io.BytesIO(payload)):
                    try:
                        record = self.svc.ask("什么是缓存穿透？")
                    except Exception as exc:
                        self.fail(f"模型响应格式错误时应降级：{type(exc).__name__}: {exc}")
                self.assertEqual(record["mode"], "extractive")
                self.assertFalse(record["refused"])
                self.assertTrue(record["citations"])
                self.assertIn("error:", record["diagnostics"]["llm"])
                self.assertEqual(self.svc.get_history(record["id"])["answer"], record["answer"])

    def test_llm_mode_accepts_valid_citations_and_downgrades_invalid(self):
        self.svc.add_document(
            "测试讲义", "# 缓存\n\n缓存穿透是指查询一个一定不存在的数据，由于缓存不命中而直接打到数据库。常见对策是布隆过滤器与空值缓存。", "课程文档"
        )
        self.svc.llm = _FakeLLM("缓存穿透指查询不存在的数据直接打到数据库 [1]。")
        rec = self.svc.ask("什么是缓存穿透？")
        self.assertEqual(rec["mode"], "llm")
        self.assertEqual([c["n"] for c in rec["citations"]], [1])

        self.svc.llm = _FakeLLM("凭空编造的答案 [9]。")
        rec = self.svc.ask("什么是缓存穿透？")
        self.assertEqual(rec["mode"], "extractive")
        self.assertIn("invalid_citations", rec["diagnostics"]["llm"])

        self.svc.llm = _FakeLLM(REFUSAL_PREFIX + "片段未提及。")
        rec = self.svc.ask("什么是缓存穿透？")
        self.assertTrue(rec["refused"])
        self.assertEqual(rec["mode"], "llm")


if __name__ == "__main__":
    unittest.main()
