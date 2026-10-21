"""业务门面：组合 core 各模块，向 API 层返回纯 dict。不依赖 FastAPI，可直接单测。"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict
from pathlib import Path

from .core.answer import (
    REFUSAL_PREFIX,
    AnswerPolicy,
    Evidence,
    compose_extractive,
    compose_refusal,
    excerpt_for,
    format_source,
    is_refusal,
    select_evidence,
)
from .core.bm25 import BM25Index
from .core.importers import ImportError_, parse_upload
from .core.llm import LLMClient
from .core.store import CATEGORIES, KnowledgeStore, now_iso
from .core.tokenizer import content_tokens, segments, tokenize

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"
_CITE_RE = re.compile(r"\[(\d+)\]")


class KnowledgeService:
    def __init__(self, data_dir: Path | str, policy: AnswerPolicy | None = None, llm: LLMClient | None = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = KnowledgeStore(self.data_dir / "knowledge.sqlite3")
        self.policy = policy or AnswerPolicy()
        self.llm = llm or LLMClient()
        self._lock = threading.RLock()
        self._chunks: list[dict] = []
        self._index = BM25Index.build([])
        self._index_built_at = ""
        self.rebuild_index()

    # ---------- 索引 ----------
    def rebuild_index(self) -> None:
        with self._lock:
            self._chunks = self.store.all_chunks()
            docs = [tokenize(f"{c['heading']}\n{c['text']}" if c["heading"] else c["text"]) for c in self._chunks]
            self._index = BM25Index.build(docs)
            self._index_built_at = now_iso()

    # ---------- 文档 ----------
    def stats(self) -> dict:
        s = self.store.stats()
        s["llm"] = self.llm.describe()
        s["policy"] = asdict(self.policy)
        s["categories_all"] = list(CATEGORIES)
        s["index"] = {"chunks": len(self._index), "rebuilt_at": self._index_built_at}
        s["data_dir"] = str(self.data_dir)
        return s

    def list_documents(self) -> list[dict]:
        return self.store.list_documents()

    def get_document(self, doc_id: str) -> dict | None:
        doc = self.store.get_document(doc_id)
        if not doc:
            return None
        doc["chunks"] = self.store.document_chunks(doc_id)
        doc["chunk_count"] = len(doc["chunks"])
        doc["char_count"] = len(doc["content"])
        return doc

    def add_document(self, title: str, content: str, category: str, source_name: str = "") -> dict:
        with self._lock:
            doc = self.store.add_document(title, content, category, source_name)
            self.rebuild_index()
        return doc

    def import_files(self, files: list[tuple[str, bytes]], category: str = "课程资料") -> dict:
        """files: [(filename, bytes)]。逐个处理，失败的文件记录原因，不影响其余文件。"""
        imported, failed = [], []
        for name, raw in files:
            try:
                title, text = parse_upload(name, raw)
                doc = self.add_document(title, text, category, source_name=name)
                imported.append(doc)
            except (ImportError_, ValueError) as e:
                failed.append({"file": name, "error": str(e)})
        return {"imported": imported, "failed": failed}

    def delete_document(self, doc_id: str) -> bool:
        with self._lock:
            ok = self.store.delete_document(doc_id)
            if ok:
                self.rebuild_index()
        return ok

    def load_samples(self, force: bool = False) -> dict:
        """导入内置合成演示资料；已有同名资料则跳过。"""
        if not SAMPLES_DIR.exists():
            return {"imported": [], "skipped": [], "note": "samples 目录不存在"}
        existing = {d["source_name"] for d in self.store.list_documents()}
        imported, skipped = [], []
        for p in sorted(SAMPLES_DIR.glob("*.md")):
            if p.name in existing and not force:
                skipped.append(p.name)
                continue
            text = p.read_text(encoding="utf-8")
            meta = _sample_meta(text)
            text = _META_RE.sub("", text).strip()
            title, body = parse_upload(p.name, text.encode("utf-8"))
            doc = self.add_document(meta.get("title", title), body, meta.get("category", "课程资料"), source_name=p.name)
            imported.append(doc)
        return {"imported": imported, "skipped": skipped}

    def sample_questions(self) -> list[dict]:
        p = SAMPLES_DIR / "questions.json"
        if not p.exists():
            return []
        return json.loads(p.read_text(encoding="utf-8"))

    # ---------- 检索与回答 ----------
    def retrieve(self, question: str, top_k: int = 5) -> list[Evidence]:
        q_tokens = tokenize(question)
        q_content = content_tokens(question)
        q_segs = segments(question)
        with self._lock:
            hits = self._index.search(q_tokens, top_k=top_k)
            out = []
            for i, score in hits:
                c = self._chunks[i]
                covered = self._index.covered_segments(q_segs, i, self.policy.segment_hit_fraction)
                out.append(
                    Evidence(
                        chunk_id=c["id"], document_id=c["document_id"], title=c["title"], heading=c["heading"],
                        text=c["text"], score=score,
                        coverage=self._index.coverage(q_content, i),
                        segment_coverage=(len(covered) / len(q_segs)) if q_segs else 0.0,
                        covered_segments=covered,
                    )
                )
        return out

    def ask(self, question: str) -> dict:
        question = (question or "").strip()
        if not question:
            raise ValueError("问题不能为空")
        if len(question) > 500:
            raise ValueError("问题过长（上限 500 字）")
        q_content = content_tokens(question)
        diagnostics: dict = {"question_terms": q_content, "question_segments": segments(question)}
        if len(q_content) < self.policy.min_question_terms:
            answer = REFUSAL_PREFIX + "问题中没有可检索的实词，请补充具体术语或关键词。"
            diagnostics["reason"] = "no_question_terms"
            rec = self.store.add_history(question, answer, "extractive", True, [], diagnostics)
            return rec
        if len(self._index) == 0:
            answer = REFUSAL_PREFIX + "知识库为空，请先导入资料或载入演示资料。"
            diagnostics["reason"] = "empty_index"
            return self.store.add_history(question, answer, "extractive", True, [], diagnostics)

        ranked = self.retrieve(question)
        evidence, diag = select_evidence(ranked, self.policy)
        diagnostics.update(diag)
        if not evidence:
            answer = compose_refusal(question, ranked, diag, self.policy)
            return self.store.add_history(question, answer, "extractive", True, [], diagnostics)

        citations = [
            {"n": n, "document_id": e.document_id, "title": e.title, "heading": e.heading,
             "chunk_id": e.chunk_id, "text": e.text, "excerpt": excerpt_for(question, e, self.policy),
             "score": round(e.score, 3), "coverage": round(e.coverage, 3),
             "segment_coverage": round(e.segment_coverage, 3)}
            for n, e in enumerate(evidence, 1)
        ]
        mode = "extractive"
        answer = compose_extractive(question, evidence, self.policy)
        if self.llm.configured():
            try:
                llm_text = self.llm.answer(question, [(c["n"], c["title"], c["text"]) for c in citations])
                used = {int(n) for n in _CITE_RE.findall(llm_text)}
                valid = set(range(1, len(citations) + 1))
                if is_refusal(llm_text):
                    diagnostics["llm"] = "refused"
                    return self.store.add_history(question, llm_text, "llm", True, [], diagnostics)
                if used and used <= valid:
                    answer, mode = llm_text, "llm"
                    citations = [c for c in citations if c["n"] in used]
                    diagnostics["llm"] = "accepted"
                else:
                    diagnostics["llm"] = f"invalid_citations:{sorted(used)}，已降级为抽取式"
            except RuntimeError as e:
                diagnostics["llm"] = f"error:{e}，已降级为抽取式"
        return self.store.add_history(question, answer, mode, False, citations, diagnostics)

    # ---------- 历史、反馈与导出 ----------
    def history(self, limit: int = 50, offset: int = 0, refused: bool | None = None, q: str = "") -> dict:
        items, total = self.store.list_history(limit=limit, offset=offset, refused=refused, q=q)
        return {"history": items, "total": total, "limit": limit, "offset": offset}

    def get_history(self, hid: str) -> dict | None:
        return self.store.get_history(hid)

    def set_feedback(self, hid: str, helpful: bool, note: str = "") -> dict | None:
        if not self.store.set_feedback(hid, helpful, note):
            return None
        return self.store.get_history(hid)

    def export_jsonl(self) -> str:
        """全部问答导出为 JSONL（供 03-kb-lora-finetune 构造训练样本）。"""
        lines = []
        for rec in self.store.iter_history():
            lines.append(json.dumps({
                "id": rec["id"], "question": rec["question"], "answer": rec["answer"], "mode": rec["mode"],
                "refused": rec["refused"],
                "citations": [{"document_id": c["document_id"], "chunk_id": c["chunk_id"], "title": c["title"],
                               "heading": c.get("heading", ""), "excerpt": c.get("excerpt", ""), "text": c["text"]}
                              for c in rec["citations"]],
                "feedback": rec.get("feedback"), "created_at": rec["created_at"],
            }, ensure_ascii=False))
        return "\n".join(lines) + ("\n" if lines else "")

    def export_markdown(self, hid: str) -> str | None:
        rec = self.store.get_history(hid)
        if not rec:
            return None
        lines = [
            f"# 问答记录 {rec['id']}",
            "",
            f"- 时间：{rec['created_at']}",
            f"- 模式：{rec['mode']}（extractive=资料原句抽取；llm=模型基于引用片段生成）",
            f"- 是否拒答：{'是' if rec['refused'] else '否'}",
            f"- 反馈：{'有帮助' if rec['feedback'] and rec['feedback']['helpful'] else ('没帮助' if rec['feedback'] else '未评价')}"
            + (f"（{rec['feedback']['note']}）" if rec.get("feedback") and rec["feedback"].get("note") else ""),
            "",
            "## 问题",
            "",
            rec["question"],
            "",
            "## 回答",
            "",
            rec["answer"],
            "",
        ]
        if rec["citations"]:
            lines += ["## 引用资料", ""]
            for c in rec["citations"]:
                where = format_source(c["title"], c.get("heading", ""))
                lines += [f"### [{c['n']}] {where}", "", f"> {c['text'].replace(chr(10), chr(10) + '> ')}", "",
                          f"摘录：{c.get('excerpt', '')}", "",
                          f"匹配分 {c['score']}，覆盖率 {c['coverage']}", ""]
        d = rec.get("diagnostics") or {}
        if d:
            lines += ["## 判定依据", "", "```json", json.dumps(d, ensure_ascii=False, indent=2), "```", ""]
        return "\n".join(lines)


_META_RE = re.compile(r"<!--\s*meta:\s*(\{.*?\})\s*-->", re.S)


def _sample_meta(text: str) -> dict:
    """样例文件头部的 `<!-- meta: {...} -->` 注释，用于指定标题与分类。"""
    m = _META_RE.search(text)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
