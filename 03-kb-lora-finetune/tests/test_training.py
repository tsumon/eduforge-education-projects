"""Actual tokenizer boundary tests; no mocked model or remote download."""
import importlib.util
import tempfile
import unittest
from pathlib import Path


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("finetune.training"), "completion-only training is not implemented")

    def test_prompt_is_masked_and_answer_is_preserved(self):
        from finetune.training import make_tiny_base, encode_example
        root = Path(__file__).resolve().parents[1] / "runtime/test-tmp"
        root.mkdir(parents=True, exist_ok=True)
        row = {"question": "术语是什么？", "contexts": [{"id": "d#1", "text": "术语是一段说明"}], "reference": {"answer": "术语是一段说明", "citations": ["d#1"], "refused": False, "missing_information": ""}}
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            tokenizer, _ = make_tiny_base(Path(tmp), [row], seed=12)
            encoded = encode_example(row, tokenizer, max_length=2048)
            self.assertTrue(all(label == -100 for label in encoded["labels"][:encoded["prompt_length"]]))
            self.assertTrue(all(label >= 0 for label in encoded["labels"][encoded["prompt_length"]:]))
            answer = tokenizer.decode(encoded["input_ids"][encoded["prompt_length"]:], skip_special_tokens=True)
            self.assertIn('"answer": "术语是一段说明"', answer)
            with self.assertRaises(ValueError):
                encode_example(row, tokenizer, max_length=8)

    def test_training_saves_real_adapter_with_unchanged_frozen_base(self):
        from finetune.training import parser, run_training
        root = Path(__file__).resolve().parents[1] / "runtime/test-tmp"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            output = Path(tmp) / "adapter"
            args = parser().parse_args(["--init-tiny-random", "--purpose", "smoke", "--output", str(output),
                                        "--max-steps", "1", "--gradient-accumulation", "1", "--max-length", "2048"])
            manifest = run_training(args)
            self.assertTrue(manifest.get("frozen_base_unchanged"), "Need cryptographic before/after evidence")
            self.assertEqual(manifest["frozen_base_sha256_before"], manifest["frozen_base_sha256_after"])
            self.assertGreater(manifest["adapter_l1_change"], 0)
            self.assertTrue((output / "adapter_model.safetensors").is_file())

    def test_partial_run_is_not_silently_appended_to(self):
        from finetune.training import parser, run_training
        root = Path(__file__).resolve().parents[1] / "runtime/test-tmp"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            output = Path(tmp) / "adapter"
            output.mkdir()
            (output / "training_log.jsonl").write_text('{"step": 1, "loss": 1.0}\n', encoding="utf-8")
            args = parser().parse_args(["--init-tiny-random", "--purpose", "smoke", "--output", str(output),
                                        "--max-steps", "1", "--gradient-accumulation", "1", "--max-length", "2048"])
            with self.assertRaisesRegex(ValueError, "existing training"):
                run_training(args)


if __name__ == "__main__":
    unittest.main()
