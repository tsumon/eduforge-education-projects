import argparse
import json
from pathlib import Path
from .dataset import ROOT, check_splits, read_jsonl

def main():
    parser = argparse.ArgumentParser(description="Reject cross-split document/evidence/question leakage")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/processed")
    args = parser.parse_args()
    print(json.dumps(check_splits({s: read_jsonl(args.data_dir / f"{s}.jsonl") for s in ("train", "dev", "test")}), indent=2))

if __name__ == "__main__":
    main()
