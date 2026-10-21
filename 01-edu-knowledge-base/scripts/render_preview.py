"""生成离线预览页：把 static/ 内联成单个 HTML，并用真实 service 产出的数据模拟 API。

用途：没有浏览器+服务器环境时做视觉检查；不替代真实运行。
    python scripts/render_preview.py --out dist/preview.html [--view ask|docs|history|settings] [--refused]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.llm import LLMClient  # noqa: E402
from app.service import KnowledgeService  # noqa: E402


def build_fixture() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        svc = KnowledgeService(Path(tmp), llm=LLMClient(base_url="", model=""))
        svc.load_samples()
        asked = [svc.ask(q["question"]) for q in svc.sample_questions()]
        svc.set_feedback(asked[0]["id"], True)
        svc.set_feedback(asked[1]["id"], False, "少了一条")
        docs = svc.list_documents()
        fixture = {
            "stats": svc.stats(),
            "documents": {"documents": docs, "categories": svc.stats()["categories_all"]},
            "document_details": {d["id"]: svc.get_document(d["id"]) for d in docs},
            "sample": {"questions": svc.sample_questions()},
            "history_all": svc.history(limit=200)["history"],
            "asked": asked,
        }
        svc.store.close()
        return fixture


MOCK_JS = r"""
window.__FIXTURE__ = %s;
(function () {
  const fx = window.__FIXTURE__;
  let askIdx = %d;
  const ok = body => Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  window.fetch = function (url, opts) {
    const u = new URL(String(url), location.href);
    const path = u.pathname.replace(/^\/api\/knowledge/, "");
    if (path === "/stats") return ok(fx.stats);
    if (path === "/documents") return ok(fx.documents);
    if (path.startsWith("/documents/")) return ok(fx.document_details[decodeURIComponent(path.slice(11))]);
    if (path === "/sample") return ok(fx.sample);
    if (path === "/ask") { const r = fx.asked[askIdx %% fx.asked.length]; askIdx++; return ok(r); }
    if (path === "/history") {
      const p = u.searchParams; let rows = fx.history_all;
      if (p.get("refused") === "true") rows = rows.filter(h => h.refused);
      if (p.get("refused") === "false") rows = rows.filter(h => !h.refused);
      if (p.get("q")) rows = rows.filter(h => h.question.includes(p.get("q")));
      const off = +p.get("offset") || 0, lim = +p.get("limit") || 20;
      return ok({ history: rows.slice(off, off + lim), total: rows.length, limit: lim, offset: off });
    }
    if (/\/history\/.+\/feedback$/.test(path)) { const r = fx.asked[0]; return ok(Object.assign({}, r, { feedback: { helpful: JSON.parse(opts.body).helpful, created_at: r.created_at } })); }
    return ok({ detail: "预览模式不支持该操作：" + path });
  };
  window.addEventListener("load", () => setTimeout(() => {
    if (location.hash === "#ask" || !location.hash) {
      const q = document.querySelector("#question"); q.value = fx.asked[askIdx %% fx.asked.length].question;
      document.querySelector("#form-ask").dispatchEvent(new Event("submit", { cancelable: true }));
    }
  }, 50));
})();
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist" / "preview.html"))
    ap.add_argument("--view", default="ask", choices=["ask", "docs", "history", "settings"])
    ap.add_argument("--refused", action="store_true", help="首屏展示拒答样例")
    args = ap.parse_args()

    static = ROOT / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    css = (static / "style.css").read_text(encoding="utf-8")
    js = (static / "app.js").read_text(encoding="utf-8")
    fixture = build_fixture()
    start = next((i for i, r in enumerate(fixture["asked"]) if r["refused"]), 0) if args.refused else 0
    mock = MOCK_JS % (json.dumps(fixture, ensure_ascii=False), start)
    html = html.replace('<link rel="stylesheet" href="/style.css">', f"<style>\n{css}\n</style>")
    html = html.replace('<script src="/app.js"></script>',
                        f"<script>\nlocation.hash = '#{args.view}';\n{mock}\n</script>\n<script>\n{js}\n</script>")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"已生成 {out}")


if __name__ == "__main__":
    main()
