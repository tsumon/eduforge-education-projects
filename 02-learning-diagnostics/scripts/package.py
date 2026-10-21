"""按交付命名打包项目；排除运行资料、依赖和缓存。"""
import argparse
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = {"runtime", ".venv", "venv", "env", "dist", "node_modules", "site-packages", "__pycache__",
            ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", ".git", ".taste-skill", ".idea", ".vscode"}
LOCAL_FILES = {".envrc", ".python-version", ".coverage", ".ds_store", "thumbs.db", "desktop.ini",
               "credentials.json", "secrets.json", "config.local.json", "settings.local.json"}


def excluded(relative: Path) -> bool:
    parts = [part.lower() for part in relative.parts]
    filename = parts[-1]
    return (any(part in EXCLUDED or part.startswith("runtime-") or part == ".env" or part.startswith(".env.") for part in parts)
            or filename in LOCAL_FILES or filename.endswith((".pyc", ".pyo", ".zip", ".log", ".local"))
            or ".sqlite3" in filename or bool(re.search(r"\.(?:sqlite|db)(?:$|[-.])", filename)))


def main():
    parser = argparse.ArgumentParser(description="生成 组名_姓名_学情诊断_教育.zip")
    parser.add_argument("--group", help="组名；未指定 --archive-name 时必填")
    parser.add_argument("--name", help="姓名；未指定 --archive-name 时必填")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "dist", help="ZIP输出目录，默认项目dist目录")
    parser.add_argument("--archive-name", help="自定义ZIP文件名（可省略.zip），指定后无需组名/姓名")
    args = parser.parse_args()
    def clean(text):
        result = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
        if not result:
            parser.error("组名与姓名不能是空白或路径字符")
        return result[:80]
    if args.archive_name is not None:
        archive_name = args.archive_name.strip()
        if not archive_name or archive_name in (".", "..") or re.search(r'[<>:"/\\|?*\x00-\x1f]', archive_name) or archive_name.rstrip(" .") != archive_name:
            parser.error("archive-name必须是安全的文件名，不能包含路径或保留字符")
        if not archive_name.lower().endswith(".zip"):
            archive_name += ".zip"
        reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)}
        if archive_name.split(".")[0].upper() in reserved:
            parser.error("archive-name不能使用Windows设备保留名")
    else:
        if not args.group or not args.name:
            parser.error("请提供 --group 和 --name，或使用 --archive-name")
        archive_name = f"{clean(args.group)}_{clean(args.name)}_学情诊断_教育.zip"
    target = args.out_dir.expanduser().resolve() / archive_name
    if target.exists():
        parser.error(f"ZIP已存在，拒绝覆盖：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    nested_output = target.parent if target.parent != ROOT and target.parent.is_relative_to(ROOT) else None
    try:
        archive = zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED)
    except FileExistsError:
        parser.error(f"ZIP已存在，拒绝覆盖：{target}")
    with archive:
        for path in sorted(ROOT.rglob("*")):
            relative = path.relative_to(ROOT)
            if not path.is_file() or path.is_symlink() or excluded(relative):
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(ROOT) or resolved == target or (nested_output and resolved.is_relative_to(nested_output)):
                continue
            archive.write(path, Path(target.stem) / relative)
    print(target)


if __name__ == "__main__":
    main()
