"""Behavior tests: document isolation, unsafe datasets, and honest scoring."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("finetune.dataset"), "dataset pipeline is not implemented")

    def test_builder_keeps_document_groups_and_all_five_capabilities(self):
        from finetune.dataset import build_dataset, read_jsonl, check_splits
        temp_root = Path(__file__).resolve().parents[1] / "runtime" / "test-tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as tmp:
            report = build_dataset(output=Path(tmp))
            splits = {name: read_jsonl(Path(tmp) / f"{name}.jsonl") for name in ("train", "dev", "test")}
            self.assertTrue(check_splits(splits)["passed"])
            for name, rows in splits.items():
                self.assertGreater(len(rows), 0, name)
                self.assertEqual(set(row["capability"] for row in rows), {"terminology_format", "grounded", "integration", "citation", "refusal"})
                self.assertTrue(all(row["synthetic"] for row in rows))
            self.assertEqual(report["total_examples"], sum(map(len, splits.values())))

    def test_checker_rejects_shared_documents_and_duplicate_questions(self):
        from finetune.dataset import check_splits
        row = {"id": "a", "group_id": "doc-a", "question": "What?", "contexts": [{"id": "doc-a#1", "text": "fact"}]}
        with self.assertRaises(ValueError):
            check_splits({"train": [row], "test": [{**row, "id": "b"}]})
        with self.assertRaises(ValueError):
            check_splits({"train": [row], "test": [{**row, "id": "b", "group_id": "doc-b", "contexts": []}]})

    def test_evaluation_penalizes_fabricated_citations_and_invalid_json(self):
        from finetune.evaluate import score_example
        example = {"capability": "grounded", "reference": {"answer": "使用参数", "citations": ["d#1"], "refused": False, "missing_information": ""}, "contexts": [{"id": "d#1", "text": "使用参数"}], "keywords": ["参数"]}
        good = score_example(example, json.dumps(example["reference"], ensure_ascii=False))
        bad = score_example(example, '{"answer":"使用参数","citations":["invented"],"refused":false,"missing_information":""}')
        self.assertEqual(good["citation_precision"], 1.0)
        self.assertEqual(bad["citation_precision"], 0.0)
        self.assertEqual(score_example(example, "garbled text")["valid_json"], 0.0)

    def test_refusal_requires_no_claims_and_explicit_missing_information(self):
        from finetune.evaluate import score_example
        example = {"capability": "refusal", "reference": {"answer": "资料不足", "citations": [], "refused": True, "missing_information": "评分规则"}, "contexts": [], "keywords": []}
        valid = '{"answer":"资料不足","citations":[],"refused":true,"missing_information":"请补充评分规则"}'
        self.assertEqual(score_example(example, valid)["refusal_correct"], 1.0)
        invalid = '{"answer":"一定及格","citations":[],"refused":false,"missing_information":""}'
        self.assertEqual(score_example(example, invalid)["refusal_correct"], 0.0)

    def test_evaluation_rejects_missing_or_duplicate_predictions(self):
        from finetune.evaluate import evaluate_rows
        example = {"id": "one", "capability": "refusal", "reference": {}, "contexts": [], "keywords": []}
        with self.assertRaises(ValueError):
            evaluate_rows([example], [])
        with self.assertRaises(ValueError):
            evaluate_rows([example], [{"id": "one", "output": "{}"}, {"id": "one", "output": "{}"}])

    def test_evaluation_rejects_non_string_output_clearly(self):
        from finetune.evaluate import evaluate_rows
        example = {"id": "one", "capability": "refusal", "reference": {}, "contexts": [], "keywords": []}
        with self.assertRaisesRegex(ValueError, "output.*string"):
            evaluate_rows([example], [{"id": "one", "output": {"answer": "do not accept objects"}}])

    def test_report_escapes_raw_model_text(self):
        self.assertIsNotNone(importlib.util.find_spec("finetune.render_report"), "report renderer is missing")
        from finetune.render_report import prediction_details
        result = prediction_details([{"id": "one", "output": "<script>alert(1)</script>"}])
        self.assertNotIn("<script>", result)
        self.assertIn("&lt;script&gt;", result)

    def test_subset_metrics_do_not_count_empty_citations_or_answerable_cases_as_success(self):
        from finetune.evaluate import evaluate_rows
        rows = [
            {"id": "answerable", "capability": "citation", "reference": {"answer": "参数", "citations": ["d#1"], "refused": False, "missing_information": ""}, "contexts": [{"id": "d#1", "text": "参数"}], "keywords": ["参数"]},
            {"id": "must-refuse", "capability": "refusal", "reference": {"answer": "资料不足", "citations": [], "refused": True, "missing_information": "日期通知"}, "contexts": [], "keywords": []},
        ]
        pred = [{"id": "answerable", "output": '{"answer":"参数","citations":[],"refused":false,"missing_information":""}'},
                {"id": "must-refuse", "output": '{"answer":"日期是明天","citations":[],"refused":false,"missing_information":""}'}]
        report = evaluate_rows(rows, pred)
        self.assertEqual(report["metrics"]["citation_precision"], .5)
        self.assertEqual(report["metrics"]["refusal_correct"], .5)
        self.assertIn("outcomes", report, "Need separated denominators to avoid misleading empty-set successes")
        self.assertEqual(report["outcomes"]["answerable_only"]["examples"], 1)
        self.assertEqual(report["outcomes"]["answerable_only"]["citation_precision"], 0)
        self.assertEqual(report["outcomes"]["answerable_only"]["citation_recall"], 0)
        self.assertEqual(report["outcomes"]["refusal_only"]["strict_success"], 0)


if __name__ == "__main__":
    unittest.main()
