"""Package the full project, or a code/report-only handoff without datasets/weights."""
import argparse
import html
import posixpath
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".venv", ".uv-cache", ".model-cache", "__pycache__", "runtime", "dist", ".git", "smoke-base"}
EVIDENCE_NAMES = {
    "training_manifest.json", "training_log.jsonl", "evaluation.json", "adapter-evaluation.json",
    "baseline.jsonl", "baseline.manifest.json", "baseline_reuse.json", "adapter.jsonl",
    "adapter.manifest.json", "actual_run_config.json", "adapter_config.json", "artifact_integrity.json",
}
CODE_SUFFIXES = {".py", ".ps1", ".mjs", ".js", ".sh", ".json", ".md", ".txt", ".toml", ".yaml", ".yml"}
REPORT_SUFFIXES = {".md", ".txt", ".log", ".json", ".jsonl", ".html", ".png", ".jpg", ".jpeg", ".svg", ".pdf"}

PACKAGE_NOTE = """## 本包为仅代码和报告版

按用户要求，本 ZIP 不含原始/划分数据集、模型权重、分词器文件、虚拟环境或依赖缓存；本机完整工程未被删除或改动。历史训练结果、截图和曲线保留，必要的真实运行清单、日志、评估与原始预测副本位于 `reports/evidence/`。原始制品完整性清单可能列出已省略权重的摘要，不表示这些权重已打包。

**运行训练、构建数据或完整测试前，必须先补齐输入。** 原始合成资料 `data/raw/documents.json` 也按要求省略；生成脚本保留，但不能在没有来源资料的情况下重建同一数据集。请从本机完整工程恢复该输入文件，或自行准备符合 `finetune/dataset.py` 格式的合成来源（group_id、title、synthetic=true、term、definition、facts、keywords），再执行：

```powershell
python -m finetune.build_dataset --source data/raw/documents.json
python -m finetune.check_leakage
# 按下文安装依赖后，再运行测试、训练及重新生成预测。
```

新输入不保证复现报告中的既有分数；历史证据仅说明原完整工程的实际运行。报告中的历史 `outputs/` 路径属于完整工程，包内 HTML 已改用 `reports/evidence/` 的证据副本；模型/分词器链接已取消并标明未打包。

---

"""


def code_report_contents():
    """Build ZIP-only copies; never rewrite the original reports or sources."""
    contents = {}
    for directory in ("finetune", "scripts", "tests", "configs", "docs", "reports", "screenshots"):
        for path in sorted((ROOT / directory).rglob("*")):
            relative = path.relative_to(ROOT)
            suffixes = REPORT_SUFFIXES if directory in {"docs", "reports", "screenshots"} else CODE_SUFFIXES
            if relative.as_posix() == "reports/package-check.json":
                continue  # This is the old full-package check, not this code/report ZIP.
            if path.is_file() and path.suffix.lower() in suffixes and not any(part in EXCLUDED for part in relative.parts):
                contents[relative.as_posix()] = path.read_bytes()
    for path in sorted(ROOT.iterdir()):
        root_image = path.name.startswith("模型微调_") and path.suffix.lower() == ".png"
        if path.is_file() and (root_image or path.name in {"README.md", "MODEL_CARD.md"} or (path.name.startswith("requirements") and path.suffix == ".txt")):
            contents[path.name] = path.read_bytes()

    evidence = {}
    for path in sorted((ROOT / "outputs").rglob("*")):
        if path.is_file() and path.name in EVIDENCE_NAMES:
            relative = "reports/evidence/" + path.relative_to(ROOT / "outputs").as_posix()
            contents[relative] = path.read_bytes()
            evidence[path.resolve()] = relative

    for relative in list(contents):
        if not relative.endswith(".html"):
            continue
        text = contents[relative].decode("utf-8")

        def rewrite_link(match):
            href, label = html.unescape(match.group(2)), match.group(3)
            url = urlsplit(href)
            if url.scheme or url.netloc or not url.path:
                return match.group(0)
            original = (ROOT / Path(relative).parent / unquote(url.path)).resolve()
            if not original.is_relative_to((ROOT / "outputs").resolve()):
                return match.group(0)
            if original not in evidence:
                return f'<span title="仅代码报告包未包含">{label}（未打包：模型/分词器制品）</span>'
            destination = posixpath.relpath(evidence[original], posixpath.dirname(relative))
            return f'<a href="{html.escape(destination, quote=True)}">{label}</a>'

        # These local generated reports use simple <a href="..."> links.
        text = re.sub(r'<a\s+href=([\"\x27])(.*?)\1>(.*?)</a>', rewrite_link, text, flags=re.I | re.S)
        text = text.replace("<main>", '<main><p class="notice"><strong>仅代码和报告版：</strong>数据集、模型权重和分词器文件未打包；下列可打开证据为原工程真实运行记录的副本。</p>', 1)
        text = text.replace("真实 adapter 权重、训练清单和日志保存在本运行目录，所有证据摘要保存在 <code>artifact_integrity.json</code>。", "本包仅保留清单、日志、评估和原始预测等报告证据；adapter 权重、分词器和数据集未打包。原完整工程的制品摘要可在证据目录查看。")
        contents[relative] = text.encode("utf-8")
    if "README.md" in contents:
        original_readme = contents["README.md"].decode("utf-8")
        heading, separator, body = original_readme.partition("\n")
        contents["README.md"] = (heading + "\n\n" + PACKAGE_NOTE + body.lstrip("\n")).encode("utf-8")
    return contents


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--group")
    p.add_argument("--name")
    p.add_argument("--code-report-only", action="store_true", help="仅源码、文档、报告、截图和非训练数据的运行证据")
    p.add_argument("--out-dir", type=Path, default=ROOT / "dist")
    p.add_argument("--archive-name", help="ZIP 文件名；提供时不要求组名和姓名")
    args = p.parse_args()
    if args.archive_name:
        values = [args.archive_name]
        filename = args.archive_name if args.archive_name.lower().endswith(".zip") else args.archive_name + ".zip"
    else:
        if not args.group or not args.name:
            p.error("请提供 --archive-name，或同时提供 --group 与 --name")
        values = [args.group, args.name]
        filename = f"{args.group}_{args.name}_知识库模型微调_教育.zip"
    for value in values:
        if not value.strip() or any(ch in value for ch in '<>:"/\\|?*') or value in (".", ".."):
            p.error("名称不能为空，也不能包含文件路径或 Windows 非法字符")
    target = args.out_dir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        p.error(f"文件已存在，请先保留旧包或改名：{target}")
    with ZipFile(target, "x", ZIP_DEFLATED) as archive:
        if args.code_report_only:
            for relative, data in sorted(code_report_contents().items()):
                archive.writestr(target.stem + "/" + relative, data)
        else:
            for path in sorted(ROOT.rglob("*")):
                relative = path.relative_to(ROOT)
                is_base_weight = path.name == "model.safetensors" or (path.name.startswith("model-") and path.suffix == ".safetensors")
                if path.is_file() and not any(part in EXCLUDED for part in relative.parts) and path.suffix not in {".pyc", ".zip"} and path.name != ".env" and not is_base_weight:
                    archive.write(path, Path(target.stem) / relative)
    print(target)


if __name__ == "__main__":
    main()
