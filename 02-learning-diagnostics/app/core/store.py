"""报告快照持久化；每个报告保留生成时的评分标准和进度。"""
from __future__ import annotations
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
        self.conn.execute("CREATE TABLE IF NOT EXISTS agent_runs (id TEXT PRIMARY KEY, report_id TEXT NOT NULL, created_at TEXT NOT NULL, body TEXT NOT NULL)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS agent_runs_report ON agent_runs(report_id,created_at)")
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

    def get_with_latest(self, report_id: str):
        """一条 SQLite 语句读取报告与最新运行，避免组合出不同复核时刻的视图。"""
        with self._lock:
            row = self.conn.execute("""SELECT r.body,
                (SELECT a.body FROM agent_runs a WHERE a.report_id=r.id ORDER BY a.rowid DESC LIMIT 1)
                FROM reports r WHERE r.id=?""", (report_id,)).fetchone()
        return (json.loads(row[0]), json.loads(row[1]) if row[1] else None) if row else None

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

    def agent_runs(self, report_id: str, limit=20) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT body FROM agent_runs WHERE report_id=? ORDER BY rowid DESC LIMIT ?", (report_id, limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_agent(self, report_id: str, run: dict):
        with self._lock, self.conn:
            row = self.conn.execute("SELECT body FROM reports WHERE id=?", (report_id,)).fetchone()
            if row is None:
                raise ValueError("报告不存在")
            report = json.loads(row[0])
            self.conn.execute("INSERT INTO agent_runs(id,report_id,created_at,body) VALUES(?,?,?,?)",
                              (run["id"], report_id, run["created_at"], json.dumps(run, ensure_ascii=False, allow_nan=False)))
            report["review"] = run["review"]
            report.setdefault("audit_log", []).append({"action": "agent_run", "created_at": run["created_at"],
                                                       "agent_run_id": run["id"], "status": run["status"]})
            self.conn.execute("UPDATE reports SET body=? WHERE id=?", (json.dumps(report, ensure_ascii=False, allow_nan=False), report_id))

    def update_review(self, report_id: str, review: dict, event: dict, agent_run_id=None) -> dict | None:
        with self._lock, self.conn:
            row = self.conn.execute("SELECT body FROM reports WHERE id=?", (report_id,)).fetchone()
            if not row:
                return None
            latest = self.agent_runs(report_id, 1)
            if latest and latest[0]["id"] != agent_run_id:
                raise ValueError("Agent 方案已更新，请重新打开最新报告后复核")
            if latest:
                run = latest[0]
                review["agent_run_id"] = run["id"]
                run["review"] = review
                for exercise in (run.get("result") or {}).get("exercises", []):
                    exercise["review_status"] = review["status"]
                self.conn.execute("UPDATE agent_runs SET body=? WHERE id=?", (json.dumps(run, ensure_ascii=False, allow_nan=False), run["id"]))
            report = json.loads(row[0])
            report["review"] = review
            report.setdefault("audit_log", []).append(event)
            self.conn.execute("UPDATE reports SET body=? WHERE id=?", (json.dumps(report, ensure_ascii=False, allow_nan=False), report_id))
        return report
