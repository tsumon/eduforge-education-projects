"""报告快照持久化；每个报告保留生成时的评分标准和进度。"""
import json
import sqlite3
import threading
from pathlib import Path


class ReportStore:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, body TEXT NOT NULL)")
        self.conn.commit()

    def close(self):
        with self._lock:
            self.conn.close()

    def save(self, report: dict):
        with self._lock, self.conn:
            self.conn.execute("INSERT INTO reports(id,created_at,body) VALUES(?,?,?)",
                              (report["id"], report["created_at"], json.dumps(report, ensure_ascii=False, allow_nan=False)))

    def get(self, report_id: str) -> dict | None:
        with self._lock:
            row = self.conn.execute("SELECT body FROM reports WHERE id=?", (report_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT body FROM reports ORDER BY created_at DESC,rowid DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for row in rows:
            report = json.loads(row[0])
            result.append({key: report[key] for key in ("id", "class_name", "course", "created_at", "source_name", "is_demo")} |
                          {"student_count": report["summary"]["student_count"], "average_mastery": report["summary"]["average_mastery"],
                           "review_status": report.get("review", {}).get("status", "pending")})
        return result

    def update_review(self, report_id: str, review: dict, event: dict) -> dict | None:
        with self._lock, self.conn:
            row = self.conn.execute("SELECT body FROM reports WHERE id=?", (report_id,)).fetchone()
            if not row:
                return None
            report = json.loads(row[0])
            report["review"] = review
            report.setdefault("audit_log", []).append(event)
            self.conn.execute("UPDATE reports SET body=? WHERE id=?", (json.dumps(report, ensure_ascii=False, allow_nan=False), report_id))
        return report
