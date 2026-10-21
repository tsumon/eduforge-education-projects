"""Render actual saved run artifacts as a local HTML report and optional PNG chart."""
import argparse
import hashlib
import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from .dataset import ROOT, read_jsonl, write_json


def prediction_details(rows):
    return "".join(f"<details><summary>{html.escape(str(row['id']))}</summary><pre>{html.escape(str(row['output']))}</pre></details>" for row in rows)


def save_chart(log, manifest, target):
    try:
        os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "runtime/matplotlib"))
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    fig, ax = plt.subplots(figsize=(10, 3.7), dpi=160)
    ax.plot([r["step"] for r in log], [r["loss"] for r in log], marker="o", label="Training minibatch completion loss", color="#15645e")
    ax.plot([0, manifest["optimizer_steps"]], [manifest["initial_dev_loss"], manifest["final_dev_loss"]], marker="s", label="Held-out dev completion loss", color="#b55f28", linestyle="--")
    ax.set(xlabel="Optimizer step", ylabel="Cross-entropy loss", title="Actual LoRA training log (different training minibatches)")
    ax.set_ylim(bottom=0, top=max(max(r["loss"] for r in log), manifest["initial_dev_loss"])*1.15)
    ax.grid(alpha=.18)
    ax.legend(loc="lower left", fontsize=9)
    fig.tight_layout()
    fig.savefig(target)
    plt.close(fig)
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", type=Path, default=Path("outputs/verified-smoke"))
    p.add_argument("--output", type=Path, default=Path("reports/training-report.html"))
    args = p.parse_args()
    run = args.run_dir.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    adapter_dir = run / "adapter" if (run / "adapter/training_manifest.json").exists() else run
    manifest = json.loads((adapter_dir / "training_manifest.json").read_text(encoding="utf-8"))
    log = read_jsonl(adapter_dir / "training_log.jsonl")
    evaluation = json.loads((run / "evaluation.json").read_text(encoding="utf-8")) if (run/"evaluation.json").exists() else None
    chart_names = {"training-report": "模型微调_百步训练损失曲线.png", "pilot-report": "模型微调_八步试训损失曲线.png", "smoke-report": "模型微调_冒烟训练损失曲线.png"}
    chart = ROOT / chart_names.get(args.output.stem, args.output.stem + "_训练损失曲线.png")
    chart_href = Path(os.path.relpath(chart, args.output.parent)).as_posix()
    chart_html = f'<img src="{html.escape(chart_href, quote=True)}" alt="实际训练loss曲线，从零起始纵轴">' if save_chart(log, manifest, chart) else '<p>当前环境未安装 matplotlib；完整逐步 loss 数据保留在日志。</p>'
    metric_names = {"valid_json": "JSON 格式符合率", "citation_precision": "引用匹配宏平均（含空集）", "citation_recall": "引用召回宏平均（含空集）", "keyword_recall": "关键词宏平均代理", "refusal_correct": "拒答字段/措辞一致宏平均（含非拒答）", "answer_char_f1": "答案字符 F1"}
    metrics = ""
    if evaluation:
        for key, label in metric_names.items():
            metrics += f'<tr><td>{label}</td><td>{evaluation["baseline"]["metrics"][key]:.4f}</td><td>{evaluation["adapter"]["metrics"][key]:.4f}</td><td>{evaluation["delta"][key]:+.4f}</td></tr>'
    else:
        metrics = '<tr><td colspan="4">独立生成评估尚未运行。</td></tr>'
    outcome_rows = ""
    if evaluation:
        for label, subset, key in [("可回答样例 · 引用精确率", "answerable_only", "citation_precision"),
                                    ("可回答样例 · 引用召回率", "answerable_only", "citation_recall"),
                                    ("必须拒答样例 · 严格成功率", "refusal_only", "strict_success")]:
            baseline = evaluation["baseline"]["outcomes"][subset]
            adapter = evaluation["adapter"]["outcomes"][subset]
            outcome_rows += f'<tr><td>{label}（n={adapter["examples"]}）</td><td>{baseline[key]:.4f}</td><td>{adapter[key]:.4f}</td></tr>'
    capabilities = ""
    if evaluation:
        labels = {"terminology_format": "术语与格式", "grounded": "检索忠实", "integration": "多片段整合", "citation": "来源引用", "refusal": "资料不足拒答"}
        for key, label in labels.items():
            stats = evaluation["adapter"]["by_capability"][key]
            capabilities += f'<tr><td>{label}</td><td>{stats["valid_json"]:.4f}</td><td>{stats["citation_recall"]:.4f}</td><td>{stats["refusal_correct"]:.4f}</td><td>{stats["answer_char_f1"]:.4f}</td></tr>'
    links = []
    integrity = {}
    for file in sorted(run.rglob("*")):
        if file.is_file() and file.name != "artifact_integrity.json" and file.suffix in (".json", ".jsonl", ".safetensors"):
            rel = file.relative_to(run).as_posix()
            integrity[rel] = {"bytes": file.stat().st_size, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
            if file.suffix != ".safetensors":
                href = Path(os.path.relpath(file, args.output.parent)).as_posix()
                links.append(f'<a href="{html.escape(href)}">{html.escape(rel)}</a>')
    write_json(run/"artifact_integrity.json", integrity)
    baseline_rows = read_jsonl(run/"baseline.jsonl") if (run/"baseline.jsonl").exists() else []
    adapter_rows = read_jsonl(run/"adapter.jsonl") if (run/"adapter.jsonl").exists() else []
    model_kind = ("随机微型模型 · 流程冒烟" if manifest["random_base"] else "预训练中文基座 · 短程试训" if manifest["purpose"] == "pilot" else f"预训练中文基座 · {manifest['optimizer_steps']} 步监督训练")
    caution = ("随机模型没有可用中文知识库问答能力，评估失败原样保留。" if manifest["random_base"] else
               "8 步 pilot 不等同完整训练或教育质量验收，需独立评估和人工复核。" if manifest["purpose"] == "pilot" else
               "固定配置训练轮次已运行。数据量很小，且开发者已查看同一测试集的 pilot 结果；本报告不能作为盲测或教育质量验收。")
    doc = f'''<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>EduForge · LoRA 运行证据</title>
<style>*{{box-sizing:border-box}}body{{margin:0;background:#f5f5ef;color:#183632;font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif}}main{{max-width:1120px;margin:auto;padding:44px 28px}}header{{border-bottom:2px solid #183632;padding-bottom:25px}}.eyebrow{{font-size:13px;letter-spacing:.16em;color:#15645e;font-weight:700}}h1{{font-size:42px;line-height:1.25;margin:12px 0}}h2{{font-size:23px;margin-top:0}}p{{margin:10px 0}}.tag{{display:inline-block;padding:5px 12px;background:#e0eae4;border-radius:4px;font-size:14px}}.notice{{margin:24px 0;background:#fff0d8;border-left:4px solid #b55f28;padding:18px 22px}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin:28px 0}}.card,section{{background:#fff;border:1px solid #d9dfd5;padding:23px}}.card strong{{display:block;font-size:29px;font-weight:650}}.muted{{color:#64746d}}section{{margin:20px 0}}table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}td,th{{border-bottom:1px solid #e3e7df;text-align:left;padding:10px 8px}}img{{width:100%;height:auto}}code,pre{{font:13px/1.6 ui-monospace,Consolas,monospace;overflow-wrap:anywhere;white-space:pre-wrap}}.artifacts{{display:flex;gap:12px;flex-wrap:wrap}}a{{color:#15645e}}details{{border-top:1px solid #e3e7df;padding:12px 0}}summary{{cursor:pointer}}footer{{font-size:13px;color:#64746d;margin:25px 0}}@media(max-width:700px){{.cards{{grid-template-columns:1fr}}h1{{font-size:32px}}main{{padding:24px 16px}}table{{font-size:13px}}}}</style>
<main><header><div class="eyebrow">EDUFORGE / TRAINING EVIDENCE / PROJECT 03</div><h1>知识库 LoRA · 实际运行记录</h1><span class="tag">{model_kind}</span><p class="muted">{html.escape(manifest['base_model'])}</p></header>
<div class="notice"><strong>训练流程完成不等于能力达标。</strong> 本报告只展示保存的实际运行证据。{caution}</div>
<div class="cards"><div class="card"><span class="muted">真实优化步数</span><strong>{manifest['optimizer_steps']} steps</strong><span>CPU · 仅 LoRA 参数</span></div><div class="card"><span class="muted">可训练参数</span><strong>{manifest['trainable_parameters']:,}</strong><span>总参数 {manifest['total_parameters']:,}</span></div><div class="card"><span class="muted">基础权重校验</span><strong>{'前后一致' if manifest.get('frozen_base_unchanged') else '未保存证据'}</strong><span>SHA256 比对</span></div></div>
<section><h2>01 / 训练与数据边界</h2><table><tr><td>数据</td><td>12 份合成教育来源，文档组划分 56 / 14 / 14；五类能力</td></tr><tr><td>基座版本</td><td><code>{html.escape(str(manifest.get('resolved_commit')))}</code></td></tr><tr><td>Adapter 权重 L1 实际变化</td><td>{manifest['adapter_l1_change']:.8f}</td></tr><tr><td>Dev token 加权 loss</td><td>{manifest['initial_dev_loss']:.8f} → {manifest['final_dev_loss']:.8f}</td></tr><tr><td>训练是否使用 test</td><td>{manifest['test_used_for_training']}</td></tr><tr><td>冻结权重 SHA256</td><td><code>{html.escape(str(manifest.get('frozen_base_sha256_after')))}</code></td></tr></table></section>
<section><h2>02 / 逐步 Loss · 原始日志绘图</h2>{chart_html}<p class="muted">各训练点使用不同 minibatch；loss 变化不能直接推导知识库能力提升。</p></section>
<section><h2>03 / 相同测试集的独立生成评估</h2><h3>分别使用有效分母的主要结果</h3><table><tr><th>样本子集 / 指标</th><th>Baseline</th><th>Adapter</th></tr>{outcome_rows}</table><p class="muted">Greedy 解码，14 个 document-group held-out 样例。各集合共享题型模板，拒答问题明文带情境标记；它不代表无标记问题的真实泛化。无效 JSON 记为 0 分。</p><h3>宏平均诊断项 · 含无需引用与非拒答样例</h3><table><tr><th>规则代理指标</th><th>Baseline</th><th>Adapter</th><th>差值</th></tr>{metrics}</table><h3>Adapter 分能力结果</h3><table><tr><th>能力</th><th>JSON</th><th>引用召回</th><th>拒答字段符合</th><th>字符 F1</th></tr>{capabilities}</table><p class="muted">宏平均不能当作可回答样例的引用率或必须拒答样例的成功率。固定拒答措辞规则也可能漏判合理表达，指标定义见 docs/metrics.md。</p></section>
<section><h2>04 / 可追溯运行制品</h2><div class="artifacts">{' '.join(links)}</div><p>真实 adapter 权重、训练清单和日志保存在本运行目录，所有证据摘要保存在 <code>artifact_integrity.json</code>。</p></section>
<section><h2>05 / 原始模型输出</h2><p>以下保留真实输出，包括乱码、无效 JSON 与错误答案。</p><h3>Baseline</h3>{prediction_details(baseline_rows)}<h3>Adapter</h3>{prediction_details(adapter_rows)}</section>
<footer>报告从本地 JSON/JSONL 制品生成 · {datetime.now(timezone.utc).isoformat()} · 未使用付费 API 或标准答案注入。</footer></main></html>'''
    args.output.write_text(doc, encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
