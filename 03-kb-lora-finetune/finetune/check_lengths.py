"""Tokenize all splits with the intended base tokenizer, without loading weights."""
import argparse
from pathlib import Path
from transformers import AutoTokenizer
from .dataset import ROOT, read_jsonl, write_json
from .training import encode_example


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    p.add_argument("--revision", default="main")
    p.add_argument("--max-length", type=int, default=1024)
    p.add_argument("--output", default="reports/qwen_token_lengths.json")
    args = p.parse_args()
    tokenizer = AutoTokenizer.from_pretrained(args.model, revision=args.revision, cache_dir=str(ROOT/".model-cache"), trust_remote_code=False, token=False)
    report = {"model": args.model, "revision": args.revision, "max_length": args.max_length, "weights_loaded": False, "splits": {}}
    for name in ("train", "dev", "test"):
        rows = read_jsonl(ROOT / f"data/processed/{name}.jsonl")
        encoded = [encode_example(row, tokenizer, args.max_length) for row in rows]
        lengths = [len(row["input_ids"]) for row in encoded]
        report["splits"][name] = {"examples": len(lengths), "maximum_tokens": max(lengths), "minimum_tokens": min(lengths),
                                   "all_fit_without_truncation": all(n <= args.max_length for n in lengths)}
    write_json(args.output, report)
    print(report)


if __name__ == "__main__":
    main()
