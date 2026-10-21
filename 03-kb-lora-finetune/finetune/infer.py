"""Generate baseline OR adapter predictions, with identical held-out prompts."""
import argparse
import json
import time
from pathlib import Path
from .dataset import ROOT, fingerprint, read_jsonl, write_json, write_jsonl
from .prompts import render_prompt


def main():
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, set_seed
    from peft import PeftModel
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", help="Model id or local base; defaults to adapter manifest or Qwen")
    p.add_argument("--revision", default="main")
    p.add_argument("--adapter", type=Path)
    p.add_argument("--data", type=Path, default=ROOT / "data/processed/test.jsonl")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--cache-dir", default=str(ROOT / ".model-cache"))
    p.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    p.add_argument("--max-new-tokens", type=int, default=256)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--cpu-threads", type=int, default=2)
    args = p.parse_args()
    set_seed(args.seed)
    torch.set_num_threads(args.cpu_threads)
    manifest = json.loads((args.adapter / "training_manifest.json").read_text(encoding="utf-8")) if args.adapter else {}
    model_id = args.model or manifest.get("base_model", "Qwen/Qwen2.5-0.5B-Instruct")
    if (ROOT / model_id).exists():
        model_id = str(ROOT / model_id)
    revision = (manifest.get("resolved_commit") or manifest.get("base_revision", args.revision)) if not args.model else args.revision
    tokenizer = AutoTokenizer.from_pretrained(str(args.adapter or model_id), revision=revision, cache_dir=args.cache_dir, trust_remote_code=False, token=False)
    model = AutoModelForCausalLM.from_pretrained(model_id, revision=revision, cache_dir=args.cache_dir, trust_remote_code=False, token=False,
                                                dtype=torch.float32 if args.device == "cpu" else "auto")
    if args.adapter:
        model = PeftModel.from_pretrained(model, args.adapter)
    model.to(args.device).eval()
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    examples = read_jsonl(args.data)
    predictions = []
    started = time.perf_counter()
    for row in examples:
        inputs = tokenizer(render_prompt(row, tokenizer), return_tensors="pt", add_special_tokens=False).to(args.device)
        context_limit = getattr(model.config, "max_position_embeddings", getattr(model.config, "n_positions", 32768))
        if inputs["input_ids"].shape[1] + args.max_new_tokens > context_limit:
            raise ValueError(f"Prompt {row['id']} plus generation exceeds context limit; no silent evidence truncation")
        with torch.no_grad():
            tokens = model.generate(**inputs, max_new_tokens=args.max_new_tokens, do_sample=False,
                                    pad_token_id=tokenizer.pad_token_id, eos_token_id=tokenizer.eos_token_id)
        answer = tokenizer.decode(tokens[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        predictions.append({"id": row["id"], "output": answer})
    write_jsonl(args.output, predictions)
    write_json(args.output.with_suffix(".manifest.json"), {"model": model_id, "adapter": str(args.adapter) if args.adapter else None,
               "revision": revision, "dataset_sha256": fingerprint(examples), "examples": len(examples), "max_new_tokens": args.max_new_tokens,
               "decoding": "greedy", "seed": args.seed, "elapsed_seconds": round(time.perf_counter()-started, 3),
               "purpose": manifest.get("purpose", "baseline"), "references_used_for_generation": False})
    print(f"Saved {len(predictions)} predictions to {args.output}")


if __name__ == "__main__":
    main()
