"""Transformers causal-LM + PEFT LoRA, with completion-only loss and provenance."""
from __future__ import annotations
import argparse
import importlib.metadata
import hashlib
import json
import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from .dataset import ROOT, check_splits, fingerprint, read_jsonl, write_json
from .prompts import render_prompt, completion


def make_tiny_base(path, examples, seed=42):
    """Offline RANDOM GPT-2 architecture, exclusively a plumbing smoke fixture."""
    import torch
    from tokenizers import Tokenizer, models, trainers, pre_tokenizers, decoders
    from transformers import GPT2Config, GPT2LMHeadModel, PreTrainedTokenizerFast
    torch.manual_seed(seed)
    backend = Tokenizer(models.BPE(unk_token="<unk>"))
    backend.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    backend.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(vocab_size=512, special_tokens=["<pad>", "<unk>", "<s>", "</s>"],
                                 initial_alphabet=pre_tokenizers.ByteLevel.alphabet(), show_progress=False)
    # Fit tokenizer on TRAIN ONLY; no dev/test answers are used.
    texts = [completion(row) + "\n" + row["question"] + "\n" + "\n".join(c["text"] for c in row["contexts"]) for row in examples]
    from .prompts import SYSTEM
    backend.train_from_iterator(texts + [SYSTEM], trainer)
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, unk_token="<unk>", pad_token="<pad>", bos_token="<s>", eos_token="</s>", model_max_length=2048)
    config = GPT2Config(vocab_size=len(tokenizer), n_embd=32, n_layer=2, n_head=4, n_positions=2048,
                        bos_token_id=tokenizer.bos_token_id, eos_token_id=tokenizer.eos_token_id,
                        pad_token_id=tokenizer.pad_token_id, resid_pdrop=0.0, embd_pdrop=0.0, attn_pdrop=0.0)
    model = GPT2LMHeadModel(config)
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    tokenizer.save_pretrained(path)
    model.save_pretrained(path, safe_serialization=True)
    model.config._name_or_path = str(path.resolve())
    return tokenizer, model


def encode_example(example, tokenizer, max_length=1024):
    prompt = tokenizer.encode(render_prompt(example, tokenizer), add_special_tokens=False)
    target = tokenizer.encode(completion(example), add_special_tokens=False) + [tokenizer.eos_token_id]
    if len(prompt) + len(target) > max_length:
        raise ValueError(f"Example {example.get('id', '<unknown>')} has {len(prompt)+len(target)} tokens > max_length={max_length}; increase the limit or curate the source. No silent truncation is allowed.")
    return {"input_ids": prompt + target, "labels": [-100] * len(prompt) + target, "prompt_length": len(prompt)}


def collate(rows, pad_token_id, device):
    import torch
    size = max(len(row["input_ids"]) for row in rows)
    return {"input_ids": torch.tensor([r["input_ids"] + [pad_token_id]*(size-len(r["input_ids"])) for r in rows], device=device),
            "attention_mask": torch.tensor([[1]*len(r["input_ids"]) + [0]*(size-len(r["input_ids"])) for r in rows], device=device),
            "labels": torch.tensor([r["labels"] + [-100]*(size-len(r["labels"])) for r in rows], device=device)}


def validation_loss(model, encoded, pad_token_id, device, limit=0):
    import torch
    model.eval()
    total, count = 0.0, 0
    with torch.no_grad():
        for row in (encoded[:limit] if limit else encoded):
            batch = collate([row], pad_token_id, device)
            n = int((batch["labels"][:, 1:] != -100).sum().item())
            value = float(model(**batch).loss.item())
            if not math.isfinite(value):
                raise ValueError("Non-finite validation loss")
            total += value * n
            count += n
    return total / count


def frozen_fingerprint(model):
    """Hash the actual frozen tensors before/after optimization, not just names."""
    import torch
    digest = hashlib.sha256()
    for name, tensor in sorted(model.named_parameters()):
        if not tensor.requires_grad:
            digest.update(f"{name}:{tuple(tensor.shape)}:{tensor.dtype}".encode("utf-8"))
            digest.update(tensor.detach().cpu().contiguous().view(torch.uint8).numpy().tobytes())
    return digest.hexdigest()


def run_training(args):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, set_seed
    from peft import LoraConfig, TaskType, get_peft_model
    if args.max_steps <= 0 or args.batch_size <= 0 or args.gradient_accumulation <= 0:
        raise ValueError("Training steps, batch size and accumulation must be positive")
    set_seed(args.seed)
    torch.set_num_threads(args.cpu_threads)
    output = Path(args.output).resolve()
    if any((output / name).exists() for name in ("adapter_model.safetensors", "training_log.jsonl", "training_manifest.json")):
        raise ValueError(f"Refusing to overwrite or append to existing training evidence: {output}; choose a new --output")
    output.mkdir(parents=True, exist_ok=True)
    splits = {s: read_jsonl(Path(args.data_dir) / f"{s}.jsonl") for s in ("train", "dev", "test")}
    check_splits(splits)
    if args.init_tiny_random:
        if args.purpose != "smoke":
            raise ValueError("Random tiny models are allowed only with --purpose smoke")
        base_path = output.parent / "smoke-base"
        if (base_path / "model.safetensors").exists():
            raise ValueError("Random smoke base already exists; use a new output parent to preserve reproducibility")
        tokenizer, model = make_tiny_base(base_path, splits["train"], args.seed)
        base_model = str(base_path.relative_to(ROOT)) if base_path.is_relative_to(ROOT) else str(base_path.resolve())
    else:
        base_model = args.model
        tokenizer = AutoTokenizer.from_pretrained(base_model, revision=args.revision, cache_dir=args.cache_dir, trust_remote_code=False, token=False)
        model = AutoModelForCausalLM.from_pretrained(base_model, revision=args.revision, cache_dir=args.cache_dir,
                                                    trust_remote_code=False, token=False, dtype=torch.float32 if args.device == "cpu" else "auto")
    if tokenizer.eos_token_id is None:
        raise ValueError("Tokenizer must provide an EOS token")
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    model.config.pad_token_id = tokenizer.pad_token_id
    context_limit = getattr(model.config, "max_position_embeddings", getattr(model.config, "n_positions", args.max_length))
    if args.max_length > context_limit:
        raise ValueError(f"max_length exceeds model context limit {context_limit}")
    encoded_train = [encode_example(row, tokenizer, args.max_length) for row in splits["train"]]
    encoded_dev = [encode_example(row, tokenizer, args.max_length) for row in splits["dev"]]
    lora = LoraConfig(task_type=TaskType.CAUSAL_LM, r=args.rank, lora_alpha=2*args.rank,
                      lora_dropout=0.0 if args.purpose == "smoke" else .05, target_modules="all-linear", bias="none",
                      fan_in_fan_out=model.config.model_type == "gpt2")
    model = get_peft_model(model, lora).to(args.device)
    model.config.use_cache = False
    trainable = {name: p for name, p in model.named_parameters() if p.requires_grad}
    if not trainable or any("lora_" not in name for name in trainable):
        raise ValueError("Only LoRA adapter tensors may be trainable")
    before = {name: p.detach().cpu().clone() for name, p in trainable.items()}
    frozen_before = frozen_fingerprint(model)
    optimizer = torch.optim.AdamW(trainable.values(), lr=args.learning_rate, weight_decay=0.0)
    initial_dev = validation_loss(model, encoded_dev, tokenizer.pad_token_id, args.device, args.eval_limit)
    rng = random.Random(args.seed)
    indices = list(range(len(encoded_train)))
    position = len(indices)
    started = time.perf_counter()
    history = []
    for step in range(1, args.max_steps + 1):
        model.train()
        optimizer.zero_grad()
        step_loss = 0.0
        for _ in range(args.gradient_accumulation):
            chosen = []
            for _ in range(args.batch_size):
                if position >= len(indices):
                    rng.shuffle(indices)
                    position = 0
                chosen.append(encoded_train[indices[position]])
                position += 1
            batch = collate(chosen, tokenizer.pad_token_id, args.device)
            loss = model(**batch).loss
            if not torch.isfinite(loss):
                raise ValueError("Non-finite training loss")
            (loss/args.gradient_accumulation).backward()
            step_loss += float(loss.detach())/args.gradient_accumulation
        torch.nn.utils.clip_grad_norm_(trainable.values(), 1.0)
        optimizer.step()
        row = {"step": step, "loss": round(step_loss, 6), "elapsed_seconds": round(time.perf_counter()-started, 3)}
        history.append(row)
        print(json.dumps(row), flush=True)
        with (output / "training_log.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row)+"\n")
    final_dev = validation_loss(model, encoded_dev, tokenizer.pad_token_id, args.device, args.eval_limit)
    delta = sum(float((p.detach().cpu()-before[name]).abs().sum()) for name, p in trainable.items())
    if delta <= 0:
        raise ValueError("Adapter weights did not change")
    frozen_after = frozen_fingerprint(model)
    if frozen_before != frozen_after:
        raise ValueError("Frozen base weights changed during LoRA training")
    model.save_pretrained(output, safe_serialization=True)
    tokenizer.save_pretrained(output)
    versions = {package: importlib.metadata.version(package) for package in ("torch", "transformers", "peft", "tokenizers", "safetensors")}
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "purpose": args.purpose,
                "base_model": base_model, "base_revision": args.revision, "resolved_commit": getattr(model.config, "_commit_hash", None),
                "random_base": args.init_tiny_random or "tiny-random" in base_model.lower(), "synthetic_data": True, "seed": args.seed,
                "train_sha256": fingerprint(splits["train"]), "dev_sha256": fingerprint(splits["dev"]),
                "test_used_for_training": False, "train_examples": len(encoded_train), "dev_examples": len(encoded_dev),
                "optimizer_steps": args.max_steps, "batch_size": args.batch_size, "gradient_accumulation": args.gradient_accumulation,
                "learning_rate": args.learning_rate, "max_length": args.max_length,
                "trainable_parameters": sum(p.numel() for p in trainable.values()), "total_parameters": sum(p.numel() for p in model.parameters()),
                "adapter_l1_change": delta, "initial_dev_loss": initial_dev, "final_dev_loss": final_dev,
                "frozen_base_sha256_before": frozen_before, "frozen_base_sha256_after": frozen_after,
                "frozen_base_unchanged": frozen_before == frozen_after,
                "dev_examples_evaluated": min(args.eval_limit or len(encoded_dev), len(encoded_dev)),
                "elapsed_training_seconds": round(time.perf_counter()-started, 3), "device": args.device, "versions": versions,
                "quality_status": ("Pipeline smoke only; not an educational quality validation." if args.purpose == "smoke" else
                                   "Short pretrained-base pilot; not full training or educational quality acceptance." if args.purpose == "pilot" else
                                   "Requires independent held-out generation evaluation and human review.")}
    write_json(output / "training_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path)
    p.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    p.add_argument("--revision", default="main", help="Use a pinned commit for reproducible formal training")
    p.add_argument("--data-dir", type=Path, default=ROOT / "data/processed")
    p.add_argument("--output", type=Path, default=ROOT / "outputs/formal/qwen-lora")
    p.add_argument("--cache-dir", default=str(ROOT / ".model-cache"))
    p.add_argument("--purpose", choices=("smoke", "pilot", "formal"), default="formal")
    p.add_argument("--init-tiny-random", action="store_true")
    p.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    p.add_argument("--max-steps", type=int, default=100)
    p.add_argument("--max-length", type=int, default=1024)
    p.add_argument("--batch-size", type=int, default=1)
    p.add_argument("--gradient-accumulation", type=int, default=4)
    p.add_argument("--learning-rate", type=float, default=2e-4)
    p.add_argument("--rank", type=int, default=8)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--cpu-threads", type=int, default=2)
    p.add_argument("--eval-limit", type=int, default=0, help="0 evaluates all dev examples")
    return p


def main():
    p = parser()
    preliminary, _ = p.parse_known_args()
    if preliminary.config:
        config = json.loads(preliminary.config.read_text(encoding="utf-8"))
        unknown = set(config) - {action.dest for action in p._actions}
        if unknown:
            p.error(f"Unknown config keys: {sorted(unknown)}")
        p.set_defaults(**config)
    run_training(p.parse_args())


if __name__ == "__main__":
    main()
