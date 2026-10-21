"""打包提交：python scripts/package.py --group 第三组 --name 张三
→ dist/第三组_张三_教育知识库_教育.zip

排除依赖与运行时产物；组名、姓名必须由参数提供，不做任何默认值。
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
PROJECT_LABEL = "教育知识库"
EXCLUDE_DIRS = {".venv", "venv", "runtime", "runtime-review", ".taste-skill", "dist", "__pycache__", ".pytest_cache", ".git", "node_modules", ".idea", ".vscode"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".sqlite3", ".sqlite3-journal", ".log"}
EXCLUDE_FILES = {".env", ".DS_Store", "Thumbs.db"}


def should_skip(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIXES or path.name in EXCLUDE_FILES:
        return True
    return False


def main():
    ap = argparse.ArgumentParser(description=f"打包 {PROJECT_LABEL}")
    ap.add_argument("--group", required=True, help="组名")
    ap.add_argument("--name", required=True, help="姓名")
    ap.add_argument("--out-dir", default=str(ROOT / "dist"))
    args = ap.parse_args()
    group, name = args.group.strip(), args.name.strip()
    if not group or not name:
        sys.exit("组名与姓名不能为空")
    for bad in '\\/:*?"<>|':
        if bad in group or bad in name:
            sys.exit(f"组名/姓名不能包含字符 {bad}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{group}_{name}_{PROJECT_LABEL}_教育.zip"
    top = f"{group}_{name}_{PROJECT_LABEL}_教育"
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(ROOT.rglob("*")):
            if p.is_dir() or should_skip(p):
                continue
            zf.write(p, f"{top}/{p.relative_to(ROOT).as_posix()}")
            count += 1
    print(f"已打包 {count} 个文件 → {zip_path}")


if __name__ == "__main__":
    main()
