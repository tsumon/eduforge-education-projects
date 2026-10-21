"""SQLite 持久化：文档、分片、问答历史。单文件、无 ORM。"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .chunker import chunk_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    source_name TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ord INTEGER NOT NULL,
    heading TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id, ord);
CREATE TABLE IF NOT EXISTS history (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    mode TEXT NOT NULL,
    refused INTEGER NOT NULL,
    citations TEXT NOT NULL,
    diagnostics TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    history_id TEXT PRIMARY KEY REFERENCES history(id) ON DELETE CASCADE,
    helpful INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC);
"""

CATEGORIES = ("课程资料", "课程文档", "项目文档", "其他")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class KnowledgeStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # 同一连接由 FastAPI 工作线程共享，事务及读取必须完整串行执行。
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self):
        with self._lock:
            self.conn.close()

    # ---- 文档 ----
    def add_document(self, title: str, content: str, category: str, source_name: str = "") -> dict:
        title = (title or "").strip()
        content = (content or "").replace("\r\n", "\n").strip()
        if not title:
            raise ValueError("标题不能为空")
        if len(content) < 20:
            raise ValueError("正文过短（少于 20 字），无法建立有效分片")
        if category not in CATEGORIES:
            raise ValueError(f"分类必须是 {'/'.join(CATEGORIES)} 之一")
        chunks = chunk_text(content)
        if not chunks:
            raise ValueError("正文无法切分为有效分片")
        doc_id = new_id("doc")
        created = now_iso()
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO documents(id,title,category,source_name,content,created_at) VALUES(?,?,?,?,?,?)",
                (doc_id, title, category, source_name or "手工录入", content, created),
            )
            self.conn.executemany(
                "INSERT INTO chunks(id,document_id,ord,heading,text) VALUES(?,?,?,?,?)",
                [(new_id("chk"), doc_id, c.ord, c.heading, c.text) for c in chunks],
            )
        return {
            "id": doc_id,
            "title": title,
            "category": category,
            "source_name": source_name or "手工录入",
            "chunk_count": len(chunks),
            "created_at": created,
        }

    def list_documents(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT d.id,d.title,d.category,d.source_name,d.created_at,
                          (SELECT COUNT(*) FROM chunks c WHERE c.document_id=d.id) AS chunk_count
                   FROM documents d ORDER BY d.created_at DESC, d.title"""
            ).fetchall()
        return [dict(r) for r in rows]

    def get_document(self, doc_id: str) -> dict | None:
        with self._lock:
            r = self.conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        return dict(r) if r else None

    def document_chunks(self, doc_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, ord, heading, text FROM chunks WHERE document_id=? ORDER BY ord", (doc_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_document(self, doc_id: str) -> bool:
        with self._lock, self.conn:
            cur = self.conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        return cur.rowcount > 0

    def all_chunks(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT c.id, c.document_id, c.ord, c.heading, c.text, d.title, d.category
                   FROM chunks c JOIN documents d ON d.id=c.document_id
                   ORDER BY d.created_at, d.title, c.ord"""
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- 历史 ----
    def add_history(self, question: str, answer: str, mode: str, refused: bool, citations: list[dict], diagnostics: dict) -> dict:
        hid = new_id("qa")
        created = now_iso()
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO history(id,question,answer,mode,refused,citations,diagnostics,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (hid, question, answer, mode, 1 if refused else 0,
                 json.dumps(citations, ensure_ascii=False), json.dumps(diagnostics, ensure_ascii=False), created),
            )
        return {"id": hid, "question": question, "answer": answer, "mode": mode, "refused": refused,
                "citations": citations, "diagnostics": diagnostics, "created_at": created, "feedback": None}

    _HISTORY_SELECT = """
        SELECT h.*, f.helpful AS fb_helpful, f.note AS fb_note, f.created_at AS fb_created_at
        FROM history h LEFT JOIN feedback f ON f.history_id = h.id
    """

    def list_history(self, limit: int = 50, offset: int = 0, refused: bool | None = None, q: str = "") -> tuple[list[dict], int]:
        """分页列表 → (records, total)。refused 过滤已回答/拒答，q 为问题关键字。"""
        where, params = [], []
        if refused is not None:
            where.append("h.refused = ?")
            params.append(1 if refused else 0)
        if q:
            where.append("h.question LIKE ?")
            params.append(f"%{q}%")
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with self._lock:
            total = self.conn.execute(f"SELECT COUNT(*) FROM history h{clause}", params).fetchone()[0]
            rows = self.conn.execute(
                f"{self._HISTORY_SELECT}{clause} ORDER BY h.created_at DESC, h.rowid DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return [self._row_to_history(r) for r in rows], total

    def iter_history(self):
        with self._lock:
            rows = self.conn.execute(f"{self._HISTORY_SELECT} ORDER BY h.created_at, h.rowid").fetchall()
        for r in rows:
            yield self._row_to_history(r)

    def get_history(self, hid: str) -> dict | None:
        with self._lock:
            r = self.conn.execute(f"{self._HISTORY_SELECT} WHERE h.id=?", (hid,)).fetchone()
        return self._row_to_history(r) if r else None

    def set_feedback(self, hid: str, helpful: bool, note: str = "") -> bool:
        with self._lock, self.conn:
            if not self.conn.execute("SELECT 1 FROM history WHERE id=?", (hid,)).fetchone():
                return False
            self.conn.execute(
                "INSERT INTO feedback(history_id,helpful,note,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(history_id) DO UPDATE SET helpful=excluded.helpful, note=excluded.note, created_at=excluded.created_at",
                (hid, 1 if helpful else 0, (note or "")[:500], now_iso()),
            )
        return True

    @staticmethod
    def _row_to_history(r) -> dict:
        d = dict(r)
        d["refused"] = bool(d["refused"])
        d["citations"] = json.loads(d["citations"])
        d["diagnostics"] = json.loads(d.get("diagnostics") or "{}")
        fb_helpful = d.pop("fb_helpful", None)
        fb_note = d.pop("fb_note", None)
        fb_created = d.pop("fb_created_at", None)
        d["feedback"] = None if fb_helpful is None else {"helpful": bool(fb_helpful), "note": fb_note or "", "created_at": fb_created}
        return d

    # ---- 统计 ----
    def stats(self) -> dict:
        c = self.conn
        with self._lock:
            doc_count = c.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunk_count = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            q_count = c.execute("SELECT COUNT(*) FROM history").fetchone()[0]
            refused = c.execute("SELECT COUNT(*) FROM history WHERE refused=1").fetchone()[0]
            cats = c.execute("SELECT category, COUNT(*) n FROM documents GROUP BY category").fetchall()
            fb = c.execute("SELECT SUM(helpful), COUNT(*) FROM feedback").fetchone()
            last_q = c.execute("SELECT MAX(created_at) FROM history").fetchone()[0]
        return {
            "document_count": doc_count,
            "chunk_count": chunk_count,
            "question_count": q_count,
            "refused_count": refused,
            "helpful_count": int(fb[0] or 0),
            "feedback_count": int(fb[1] or 0),
            "last_question_at": last_q,
            "categories": {r["category"]: r["n"] for r in cats},
        }
