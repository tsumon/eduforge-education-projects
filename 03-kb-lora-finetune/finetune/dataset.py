"""Deterministic, document-grouped synthetic dataset. No model dependencies."""
from __future__ import annotations
import argparse
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = ("terminology_format", "grounded", "integration", "citation", "refusal")


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def examples_for(doc):
    group, title = doc["group_id"], doc["title"]
    contexts = [{"id": f"{group}#{i+1}", "text": text} for i, text in enumerate(doc["facts"])]
    term_context = {"id": f"{group}#term", "text": f"{doc['term']}：{doc['definition']}"}
    cases = [
        ("terminology_format", f"根据《{title}》，解释术语“{doc['term']}”，按指定 JSON 格式回答。", [term_context], doc["definition"], [term_context["id"]], [doc["term"]] if doc["term"] in doc["definition"] else []),
        ("grounded", f"只依据《{title}》的第一条资料，说明其规则和适用条件。", contexts[:1], doc["facts"][0], [contexts[0]["id"]], [doc["keywords"][0]]),
        ("integration", f"整合《{title}》两段资料，分别说明关键规则，保留条件与限制。", contexts, "；".join(doc["facts"]), [x["id"] for x in contexts], doc["keywords"]),
        ("citation", f"《{title}》提供了哪些规则？给出全部依据编号，不能添加不存在的来源。", contexts, "；".join(doc["facts"]), [x["id"] for x in contexts], doc["keywords"]),
    ]
    rows = []
    for capability, question, ctx, answer, citations, keywords in cases:
        rows.append({"id": f"{group}-{capability}", "group_id": group, "capability": capability, "synthetic": True,
                     "question": question, "contexts": ctx, "reference": {"answer": answer, "citations": citations, "refused": False, "missing_information": ""}, "keywords": keywords})
    for kind in ("no_context", "irrelevant", "conflict"):
        question = f"《{title}》的补测日期具体是哪一天？（{kind}场景）"
        ctx = [] if kind == "no_context" else contexts[:1]
        missing = "请补充该课程已确认的补测日期通知。"
        if kind == "conflict":
            ctx = [{"id": f"{group}#notice-a", "text": f"《{title}》同一版本通知：补测日期为2026年11月1日。"},
                   {"id": f"{group}#notice-b", "text": f"《{title}》同一版本通知：补测日期为2026年11月2日。"}]
            missing = "现有通知日期冲突，请补充有效版本和已确认的补测日期。"
        rows.append({"id": f"{group}-refusal-{kind}", "group_id": group, "capability": "refusal", "scenario": kind, "synthetic": True,
                     "question": question, "contexts": ctx, "reference": {"answer": "资料不足，无法确定。" if kind != "conflict" else "资料相互矛盾，无法确定。", "citations": [], "refused": True, "missing_information": missing}, "keywords": []})
    return rows


def check_splits(splits):
    """Reject reused groups, IDs, questions or verbatim evidence across splits."""
    seen = {key: {} for key in ("group", "id", "question", "source_id", "evidence")}
    for split, rows in splits.items():
        if not rows:
            raise ValueError(f"Empty split: {split}")
        local_ids = set()
        for row in rows:
            if row["id"] in local_ids:
                raise ValueError(f"Duplicate example id: {row['id']}")
            local_ids.add(row["id"])
            values = {"group": [row["group_id"]], "id": [row["id"]],
                      "question": [re.sub(r"\s+", "", row["question"]).casefold()],
                      "source_id": [x["id"] for x in row["contexts"]],
                      "evidence": [fingerprint(x["text"].strip()) for x in row["contexts"] if x["text"].strip()]}
            for kind, keys in values.items():
                for key in keys:
                    if key in seen[kind] and seen[kind][key] != split:
                        raise ValueError(f"Cross-split {kind} leakage: {key}")
                    seen[kind][key] = split
    return {"passed": True, "checks": ["document_group", "example_id", "normalized_question", "source_id", "verbatim_evidence"],
            "limitation": "Does not prove absence of semantic paraphrase leakage; review source families manually."}


def build_dataset(source=None, output=None, seed=42):
    source, output = Path(source or ROOT / "data/raw/documents.json"), Path(output or ROOT / "data/processed")
    docs = json.loads(source.read_text(encoding="utf-8"))
    if len(docs) < 5 or len({d["group_id"] for d in docs}) != len(docs):
        raise ValueError("Need at least five uniquely grouped source documents")
    if not all(d.get("synthetic") is True for d in docs):
        raise ValueError("This builder accepts explicitly synthetic sources only")
    docs = sorted(docs, key=lambda d: d["group_id"])
    random.Random(seed).shuffle(docs)
    holdout = max(1, round(len(docs) * .17))
    groups = {"train": docs[:-2*holdout], "dev": docs[-2*holdout:-holdout], "test": docs[-holdout:]}
    splits = {name: [row for doc in values for row in examples_for(doc)] for name, values in groups.items()}
    leakage = check_splits(splits)
    report = {"seed": seed, "synthetic": True, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "total_examples": sum(map(len, splits.values())), "leakage": leakage, "splits": {}}
    for name, rows in splits.items():
        write_jsonl(output / f"{name}.jsonl", rows)
        report["splits"][name] = {"examples": len(rows), "groups": [d["group_id"] for d in groups[name]],
                                  "capabilities": dict(Counter(r["capability"] for r in rows)), "sha256": hashlib.sha256((output/f"{name}.jsonl").read_bytes()).hexdigest()}
    write_json(output / "split_report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    print(json.dumps(build_dataset(args.source, args.output, args.seed), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
