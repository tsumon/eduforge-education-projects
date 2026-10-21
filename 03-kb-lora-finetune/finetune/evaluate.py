"""Independent reference-based proxy evaluation. Does not claim semantic faithfulness."""
from __future__ import annotations
import argparse
import json
import re
from collections import Counter
from pathlib import Path
from .dataset import fingerprint, read_jsonl, write_json


def score_example(example, output):
    if not isinstance(output, str):
        raise ValueError("predictions.output must be a string containing raw model text")
    reference = example["reference"]
    try:
        text = output.strip()
        if text.startswith("```json") and text.endswith("```"):
            text = text[7:-3].strip()
        predicted = json.loads(text)
        valid = isinstance(predicted, dict) and isinstance(predicted.get("answer"), str) and isinstance(predicted.get("citations"), list) and all(isinstance(x, str) for x in predicted["citations"]) and type(predicted.get("refused")) is bool and isinstance(predicted.get("missing_information"), str)
    except (ValueError, TypeError):
        valid = False
        predicted = {}
    if not valid:
        return dict.fromkeys(("valid_json", "citation_precision", "citation_recall", "keyword_recall", "refusal_correct", "answer_char_f1"), 0.0)
    supplied, expected = set(predicted["citations"]), set(reference.get("citations", []))
    allowed = {ctx["id"] for ctx in example["contexts"]}
    precision = len(supplied & expected & allowed) / len(supplied) if supplied else float(not expected)
    recall = len(supplied & expected & allowed) / len(expected) if expected else float(not supplied)
    wanted_refusal = reference.get("refused", False)
    refusal_correct = predicted["refused"] == wanted_refusal
    if wanted_refusal:
        refusal_correct = refusal_correct and bool(predicted["missing_information"].strip()) and not supplied and any(token in predicted["answer"] for token in ("不足", "无法", "矛盾", "不能确定"))
    answer = predicted["answer"]
    keys = example.get("keywords", [])
    keyword_recall = sum(k.casefold() in answer.casefold() for k in keys) / len(keys) if keys else float(refusal_correct)
    a = Counter(re.sub(r"\s+", "", answer).casefold())
    b = Counter(re.sub(r"\s+", "", reference.get("answer", "")).casefold())
    overlap = sum((a & b).values())
    f1 = 2 * overlap / (sum(a.values()) + sum(b.values())) if a or b else 1.0
    return {"valid_json": 1.0, "citation_precision": precision, "citation_recall": recall,
            "keyword_recall": keyword_recall, "refusal_correct": float(refusal_correct), "answer_char_f1": f1}


def evaluate_rows(examples, predictions):
    expected = {row["id"] for row in examples}
    if len(expected) != len(examples):
        raise ValueError("Duplicate test ids")
    if not examples:
        raise ValueError("Evaluation data must not be empty")
    if any(not isinstance(row.get("output"), str) for row in predictions):
        raise ValueError("predictions.output must be a string containing raw model text")
    ids = [row["id"] for row in predictions]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError("Predictions must match every test id exactly once; missing/extra/duplicate ids are forbidden")
    pred = {row["id"]: row for row in predictions}
    detail = [{"id": row["id"], "capability": row["capability"], **score_example(row, pred[row["id"]]["output"])} for row in examples]
    metric_names = list(detail[0].keys())[2:]
    aggregate = lambda rows: {name: round(sum(row[name] for row in rows)/len(rows), 6) for name in metric_names}
    answerable = [stats for row, stats in zip(examples, detail) if not row["reference"].get("refused", False)]
    refusals = [stats for row, stats in zip(examples, detail) if row["reference"].get("refused", False)]
    mean = lambda rows, key: round(sum(row[key] for row in rows)/len(rows), 6) if rows else None
    outcomes = {"answerable_only": {"examples": len(answerable), "citation_precision": mean(answerable, "citation_precision"),
                                    "citation_recall": mean(answerable, "citation_recall")},
                "refusal_only": {"examples": len(refusals), "strict_success": mean(refusals, "refusal_correct")}}
    return {"examples": len(detail), "dataset_sha256": fingerprint(examples), "metrics": aggregate(detail),
            "outcomes": outcomes,
            "by_capability": {cap: aggregate([r for r in detail if r["capability"] == cap]) for cap in sorted({r["capability"] for r in detail})},
            "details": detail, "limitations": ["JSON, citation IDs, refusal signals, keyword recall and character F1 are deterministic proxies.",
            "Correct citations do not prove that every answer claim follows from evidence; human review is required.",
            "Document-group held-out splits share templates; questions include explicit synthetic scenario markers.",
            "The pilot test outputs were reviewed before the fixed 100-step run; this is not an unseen blind test.",
            "Macro citation metrics include empty-set matches; macro refusal consistency includes answerable cases. Use outcomes subset denominators."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/processed/test.jsonl"))
    parser.add_argument("--pred", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, help="Optional baseline prediction JSONL, evaluated on exactly the same data")
    parser.add_argument("--output", type=Path, default=Path("reports/evaluation.json"))
    args = parser.parse_args()
    examples = read_jsonl(args.data)
    result = evaluate_rows(examples, read_jsonl(args.pred))
    if args.baseline:
        baseline = evaluate_rows(examples, read_jsonl(args.baseline))
        result = {"adapter": result, "baseline": baseline, "delta": {k: round(result["metrics"][k]-baseline["metrics"][k], 6) for k in result["metrics"]}}
    write_json(args.output, result)
    summary = {"adapter_outcomes": result["adapter"]["outcomes"], "baseline_outcomes": result["baseline"]["outcomes"], "macro_delta": result["delta"]} if "delta" in result else {"outcomes": result["outcomes"], "macro_metrics": result["metrics"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
