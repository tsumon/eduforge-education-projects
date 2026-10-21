"""Inspect dependencies/hardware without downloading models or using account keys."""
import argparse
import importlib.metadata
import importlib.util
import json
import platform
import shutil
import sys
from .dataset import ROOT, write_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", default="reports/environment.json")
    p.add_argument("--require-training", action="store_true")
    args = p.parse_args()
    versions = {}
    for name in ("torch", "transformers", "peft", "accelerate", "tokenizers", "safetensors"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    report = {"python": sys.version, "executable": sys.executable, "platform": platform.platform(), "versions": versions,
              "free_disk_gib": round(shutil.disk_usage(ROOT).free/2**30, 2), "cuda_available": False}
    if versions["torch"]:
        import torch
        report["cuda_available"] = torch.cuda.is_available()
        report["cuda_devices"] = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
    report["training_dependencies_ready"] = all(versions.values())
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.require_training and not report["training_dependencies_ready"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
