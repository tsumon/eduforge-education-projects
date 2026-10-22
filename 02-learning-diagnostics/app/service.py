"""CSV → 确定性统计工具 → LangGraph Agent → 可复核报告。"""
from __future__ import annotations

import csv
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .core.config import load_rubric, load_syllabus, load_exercise_bank, Rubric, Syllabus
from .core.diagnosis import build_report, MAX_STUDENTS, MAX_TOPICS, MAX_MATRIX_CELLS
from .core.schema import REQUIRED, OPTIONAL, MAX_ROWS, MAX_POINTS, TYPE_LABELS, parse_csv
from .core.scoring import LEVEL_LABELS
from .core.store import ReportStore
from .agent_runtime import ToolChatClient

ROOT = Path(__file__).resolve().parent.parent
MAX_BYTES = 8 * 1024 * 1024
MAX_CONFIG_BYTES = 256 * 1024
DEFAULT_GOAL = "根据本次成绩、输入错因和授课进度，找出需要核对的薄弱点，提出具体补讲步骤并生成分层练习。"


def _configuration(raw: bytes, label: str) -> dict:
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError(f"{label} 超过 256KB 上限")
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{label} 不是有效的 UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是 JSON 对象")
    return value


class DiagnosticsService:
    def __init__(self, data_dir: Path | str, *, agent_client=None, agent_enabled=True, agent_max_steps=8):
        self.store = ReportStore(Path(data_dir) / "diagnostics.sqlite3")
        self.agent_client = agent_client or ToolChatClient()
        self.agent_enabled, self.agent_max_steps = agent_enabled, agent_max_steps

    def agent_info(self):
        return {"enabled": self.agent_enabled, "configured": self.agent_client.configured(), "framework": "langgraph",
                "max_steps": self.agent_max_steps, "model": self.agent_client.describe()}

    def schema(self) -> dict:
        labels = {"student_id": "学号", "name": "姓名", "topic": "知识点", "assessment_type": "考核类型", "item": "题目",
                  "score": "得分", "max_score": "满分", "date": "日期", "error_tag": "错因", "note": "备注"}
        descriptions = {"student_id": "用于关联同一学生；必须非空", "name": "姓名为空时显示学号", "topic": "与进度表 topic 完全一致",
                        "assessment_type": "作业/homework、随堂测/quiz、课堂互动/interaction", "item": "用于去重；为空时使用 -",
                        "score": "有限数字，0≤得分≤满分", "max_score": f"有限数字，0<满分≤{MAX_POINTS}",
                        "date": "YYYY-MM-DD；格式错误会告警并置空", "error_tag": "输入者标注的错因；缺失不推断原因", "note": "输入者提供的原题或观察备注"}
        return {"columns": [{"key": key, "label": labels[key], "required": key in REQUIRED, "description": descriptions[key]} for key in REQUIRED + OPTIONAL],
                "assessment_types": TYPE_LABELS, "level_labels": LEVEL_LABELS, "agent": self.agent_info(),
                "rubric": load_rubric().to_dict(), "syllabus": load_syllabus().to_dict(),
                "limits": {"csv_bytes": MAX_BYTES, "config_bytes": MAX_CONFIG_BYTES, "max_rows": MAX_ROWS, "max_points": MAX_POINTS,
                           "max_students": MAX_STUDENTS, "max_topics": MAX_TOPICS, "max_student_topic_cells": MAX_MATRIX_CELLS},
                "methodology": "规则统计：同类型按分值汇总后加权；只对实际有记录的类型归一。缺失不算零分，未授课不判薄弱；错因仅引用输入标签。"}

    def sample(self, kind: str = "csv") -> tuple[str, str, str]:
        if kind == "csv":
            return "classroom_synthetic.csv", (ROOT / "data/samples/classroom.csv").read_text(encoding="utf-8"), "text/csv"
        if kind in ("syllabus", "rubric"):
            return f"{kind}.json", (ROOT / f"data/config/{kind}.json").read_text(encoding="utf-8"), "application/json"
        raise ValueError("示例类型必须是 csv、syllabus 或 rubric")

    def upload(self, filename: str, raw: bytes, syllabus_raw: bytes | None = None,
               rubric_raw: bytes | None = None, class_name: str = "", is_demo: bool = False, goal: str = DEFAULT_GOAL) -> dict:
        goal = self.validate_goal(goal)
        name = PurePosixPath((filename or "").replace("\\", "/")).name
        if not name or PurePosixPath(name).suffix.lower() != ".csv":
            raise ValueError("仅支持 .csv 文件")
        if not raw or len(raw) > MAX_BYTES:
            raise ValueError("CSV 不能为空且不得超过 8MB")
        syllabus = Syllabus.from_dict(_configuration(syllabus_raw, "课程进度")) if syllabus_raw is not None else load_syllabus()
        rubric = Rubric.from_dict(_configuration(rubric_raw, "评分标准")) if rubric_raw is not None else load_rubric()
        class_name = class_name.strip()
        if len(class_name) > 100:
            raise ValueError("班级名称不能超过 100 字")
        if class_name:
            syllabus.class_name = class_name
        try:
            parsed = parse_csv(raw)
        except csv.Error as exc:
            raise ValueError(f"CSV 格式无效或单元格过长：{exc}") from exc
        report = build_report(parsed, rubric, syllabus, load_exercise_bank())
        report.update(id=f"report_{uuid.uuid4().hex[:12]}", created_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                      source_name=name, is_demo=is_demo)
        report["review"] = {"status": "pending", "reviewer": "", "note": "", "reviewed_at": None}
        report["audit_log"] = [{"action": "created", "created_at": report["created_at"], "source_name": name, "is_demo": is_demo}]
        report["warnings"].append("诊断与练习仅为教学建议，须由教师核对原始成绩后人工复核；系统不自动处罚或作正式能力评价。")
        if is_demo:
            report["warnings"].insert(0, "当前报告来自合成演示数据：姓名、学号、成绩和错因均非真实学生资料。")
        self.store.save(report)
        if self.agent_enabled and self.agent_client.configured():
            self.run_agent(report["id"], goal)
        return self.get_report(report["id"])

    def demo(self, goal: str = DEFAULT_GOAL) -> dict:
        filename, text, _ = self.sample()
        return self.upload(filename, text.encode("utf-8"), is_demo=True, goal=goal)

    def reports(self, limit: int = 100) -> dict:
        return {"reports": self.store.list(limit)}

    def get_report(self, report_id: str) -> dict | None:
        snapshot = self.store.get_with_latest(report_id)
        if snapshot is None:
            return None
        report, latest = snapshot
        report["agent"] = latest if latest else {**self.agent_info(), "status": "not_run" if self.agent_enabled and self.agent_client.configured() else ("not_configured" if self.agent_enabled else "disabled"),
            "id": None, "result": None, "events": [], "steps": 0, "tool_calls": 0,
            "review": {"status": "pending"}, "error": None}
        report["mode"] = "agent" if report["agent"]["status"] == "completed" else "statistics_preview"
        return report

    @staticmethod
    def validate_goal(goal):
        if not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 500:
            raise ValueError("诊断目标需为 1—500 字")
        return goal.strip()

    def run_agent(self, report_id: str, goal: str = DEFAULT_GOAL) -> dict | None:
        goal = self.validate_goal(goal)
        report = self.store.get(report_id)
        if report is None:
            return None
        if not self.agent_enabled:
            raise ValueError("Agent 已关闭，请设置 EDU_AGENT_ENABLED=true")
        from .diagnostic_agent import DiagnosticAgent
        run = DiagnosticAgent(report, self.agent_client, self.agent_max_steps).run(goal)
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        run.update(id=f"agent_{uuid.uuid4().hex[:12]}", report_id=report_id, goal=goal, created_at=now,
                   review={"status": "pending", "reviewer": "", "note": "", "reviewed_at": None})
        self.store.save_agent(report_id, run)
        return run

    def agent_runs(self, report_id: str) -> list[dict] | None:
        return self.store.agent_runs(report_id) if self.store.get(report_id) else None

    def review(self, report_id: str, reviewer: str, note: str = "", reviewed: bool = True, agent_run_id: str | None = None) -> dict | None:
        if not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 80:
            raise ValueError("复核人需填写 1—80 字姓名或标识")
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError("复核备注不能超过 500 字")
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        review = {"status": "reviewed" if reviewed else "pending", "reviewer": reviewer.strip(),
                  "note": note.strip(), "reviewed_at": now if reviewed else None}
        event = {"action": "reviewed" if reviewed else "review_reset", "created_at": now,
                 "reviewer": reviewer.strip(), "note": note.strip()}
        updated = self.store.update_review(report_id, review, event, agent_run_id=agent_run_id)
        return self.get_report(report_id) if updated else None

    def export_markdown(self, report_id: str) -> str | None:
        report = self.get_report(report_id)
        if not report:
            return None
        def value(number):
            return "数据不足" if number is None else f"{number:g}"
        summary = report["summary"]
        lines = [f"# {report['class_name']} 学情诊断报告", "", f"- 课程：{report['course']}", f"- 时间：{report['created_at']}",
                 f"- 来源：{report['source_name']}（{'合成演示' if report['is_demo'] else '用户上传'}）",
                 f"- 学生 {summary['student_count']} 人；有效记录 {summary['record_count']} 条；平均掌握度 {value(summary['average_mastery'])}。", "", "## 数据说明", ""]
        review = report.get("review", {})
        lines.insert(6, f"- 人工复核：{'已复核' if review.get('status') == 'reviewed' else '待复核'}；复核人：{review.get('reviewer') or '未填写'}；复核时间：{review.get('reviewed_at') or '—'}。")
        lines.extend(f"- {warning}" for warning in report["warnings"])
        agent = report["agent"]
        lines += ["", "## Agent 诊断建议", "", f"- 框架：LangGraph；状态：{agent['status']}；运行：{agent.get('id') or '尚未运行'}。",
                  "- 模型建议须教师复核；以下统计工具结果与模型方案分别保留。", ""]
        if agent.get("result"):
            plan = agent["result"]
            lines += [plan["summary"], "", "### 发现与依据", ""]
            for item in plan["findings"]:
                lines.append(f"- {item['topic']}：{item['statement']}（{', '.join(item['evidence_ids'])}）")
            lines += ["", "### Agent 补讲方案", ""]
            for item in plan["reteaching"]:
                lines += [f"#### {item['topic']}", item["reason"], f"依据：{', '.join(item['evidence_ids'])}"] + [f"- {action}" for action in item["actions"]]
            lines += ["", "### Agent 生成的新练习", ""]
            for item in plan["exercises"]:
                lines += [f"#### {item['topic']} · {LEVEL_LABELS[item['level']]}", item["question"], f"提示：{item['hint']}",
                          f"参考答案：{item['answer']}", f"依据：{', '.join(item['evidence_ids'])}；复核：{item['review_status']}。", ""]
            lines += ["### 尚需核对", ""] + [f"- {message}" for message in plan["missing_data"]]
            lines += ["", "### Agent 引用的工具证据", "", "```json", json.dumps(plan["evidence"], ensure_ascii=False, indent=2), "```", ""]
        else:
            lines.append("当前仅有基础统计预览；请配置支持工具调用的回答 API 后运行 Agent。" if agent["status"] in {"not_configured", "not_run", "disabled"} else "本次 Agent 失败或超限，未发布未核验的生成内容。")
        lines += ["", "### 工具调用记录", "", "```json", json.dumps(agent["events"], ensure_ascii=False, indent=2), "```", ""]
        lines += ["", "## 班级知识点", ""]
        for topic in report["topics"]:
            lines.append(f"- {topic['topic']}：{value(topic['mastery'])}；状态 {topic['status']}；可评估 {topic['assessed_count']} 人，薄弱 {topic['weak_count']} 人。")
            lines.extend(f"  - {evidence}" for evidence in topic["evidence"])
        lines += ["", "## 补讲清单", ""]
        if not report["reteaching"]:
            lines.append("当前无达到补讲触发条件的知识点；数据不足不等于已掌握。")
        for item in report["reteaching"]:
            lines += [f"### {item['topic']}（{item['priority']}）", item["reason"], item["suggestion"], ""]
        lines += ["", "## 个人诊断与分层练习", ""]
        for student in report["students"]:
            lines += [f"### {student['name']}（{student['student_id']}）", f"掌握度：{value(student['mastery'])}；练习层级：{LEVEL_LABELS.get(student['level'], '数据不足，暂不分层')}。", "薄弱知识点：" + ("、".join(student["weak_topics"]) or "未检出；请结合数据覆盖范围理解")]
            lines.extend(f"- {evidence}" for evidence in student["evidence"])
            for exercise in student["exercises"]:
                lines += ["", f"**{exercise['topic']} · {LEVEL_LABELS[exercise['level']]}**", exercise["question"],
                          f"提示：{exercise['hint']}", f"参考答案：{exercise['answer']}"]
        lines += ["", "## 操作记录", "", "```json", json.dumps(report.get("audit_log", []), ensure_ascii=False, indent=2), "```", ""]
        lines += ["", "## 方法与配置快照", report["methodology"]["description"], report["methodology"]["formula"], "", "```json",
                  json.dumps(report["methodology"], ensure_ascii=False, indent=2), "```", ""]
        return "\n".join(lines)
