import json
import copy

import pytest

from app.agent_runtime import ToolChatClient
from app.service import DiagnosticsService
from app.agent_runtime import FinalAnswerError
from app.diagnostic_agent import DiagnosticAgent


def call(name, args, ident="call_1"):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


class ScriptClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []

    def configured(self):
        return True

    def describe(self):
        return {"configured": True, "model": "fixture", "provider": "openai-compatible"}

    def complete(self, messages, tools):
        self.messages.append(list(messages))
        return next(self.responses)


def plan(topic="变量与类型", refs=None):
    refs = refs or ["E3"]
    return {"summary": "建议围绕已授课内容复盘失分证据。", "summary_evidence_ids": ["E1"],
            "findings": [{"topic": topic, "statement": "该知识点存在失分记录。", "evidence_ids": refs}],
            "reteaching": [{"topic": topic, "reason": "围绕实际失分记录进行补讲。", "evidence_ids": refs,
                            "actions": ["示范字符串转整数的步骤，再让学生解释类型变化。"]}],
            "exercises": [{"topic": topic, "level": "foundation", "student_ids": [],
                           "question": "写出 int('21') + 4 的结果及类型。", "hint": "先转换类型。",
                           "answer": "25，int。", "evidence_ids": refs}], "missing_data": ["建议须教师核对。"]}


def flow(final=None):
    return [call("get_class_summary", {}), call("get_rubric_and_progress", {}, "progress"),
            call("inspect_topic", {"topic": "变量与类型"}, "topic"),
            call("get_exercise_examples", {"topic": "变量与类型", "level": "foundation"}, "examples"),
            {"role": "assistant", "content": json.dumps(final or plan(), ensure_ascii=False)}]


def test_agent_generates_plan_using_statistics_tools_and_persists_it(tmp_path):
    client = ScriptClient(flow())
    svc = DiagnosticsService(tmp_path, agent_client=client)
    try:
        report = svc.demo()
        agent = report["agent"]
        assert agent["framework"] == "langgraph" and agent["status"] == "completed"
        assert agent["result"]["exercises"][0]["origin"] == "model_generated"
        assert agent["review"]["status"] == "pending"
        assert agent["result"]["exercises"][0]["question"] != report["students"][0]["exercises"][0]["question"]
        assert report["summary"] == svc.store.get(report["id"])["summary"]
        assert svc.get_report(report["id"])["agent"] == agent
        assert "Agent 诊断建议" in svc.export_markdown(report["id"])
        assert agent["id"] == svc.agent_runs(report["id"])[0]["id"]
        assert all(m["role"] != "system" or "API_KEY" not in m["content"] for m in client.messages[-1])
    finally:
        svc.store.close()


@pytest.mark.parametrize("bad", [plan(refs=["invented"]), plan(topic="未授课的知识点"),
                                  {**plan(), "summary": "平均掌握度为98765。"}])
def test_invalid_evidence_future_topics_and_invented_scores_are_repaired(tmp_path, bad):
    responses = flow(bad) + [{"role": "assistant", "content": json.dumps(plan(), ensure_ascii=False)}]
    svc = DiagnosticsService(tmp_path, agent_client=ScriptClient(responses))
    try:
        agent = svc.demo()["agent"]
        assert agent["status"] == "completed"
        assert any(e["kind"] == "validation" and e["status"] == "error" for e in agent["events"])
        assert "98765" not in json.dumps(agent["result"])
    finally:
        svc.store.close()


def test_each_run_is_report_scoped_and_new_plan_resets_review(tmp_path):
    svc = DiagnosticsService(tmp_path, agent_client=ScriptClient(flow() + flow()))
    try:
        report = svc.demo()
        reviewed = svc.review(report["id"], "教师", agent_run_id=report["agent"]["id"])
        assert reviewed["agent"]["review"]["status"] == "reviewed"
        run = svc.run_agent(report["id"], "重新生成基础练习")
        latest = svc.get_report(report["id"])
        assert run["id"] != report["agent"]["id"]
        assert latest["agent"]["review"]["status"] == "pending" and latest["review"]["status"] == "pending"
        with pytest.raises(ValueError):
            svc.review(report["id"], "教师", agent_run_id=report["agent"]["id"])
        assert svc.run_agent("another_report", "诊断") is None
    finally:
        svc.store.close()


def test_no_configuration_is_explicit_statistics_preview(tmp_path):
    svc = DiagnosticsService(tmp_path, agent_client=ToolChatClient())
    try:
        report = svc.demo()
        assert report["agent"]["status"] == "not_configured"
        assert report["agent"]["result"] is None
        assert svc.agent_runs(report["id"]) == []
        assert svc.schema()["agent"]["configured"] is False
    finally:
        svc.store.close()


def test_arbitrary_report_id_or_write_arguments_are_not_accepted_by_tools(tmp_path):
    svc = DiagnosticsService(tmp_path, agent_client=ScriptClient([
        call("inspect_student", {"student_id": "not_real", "report_id": "other"}),
        call("delete_report", {"report_id": "other"}, "delete"), *flow()]))
    try:
        report = svc.demo()
        events = report["agent"]["events"]
        assert report["agent"]["status"] == "completed"
        assert events[0]["status"] == "error" and events[1]["status"] == "error"
        assert svc.get_report(report["id"])
    finally:
        svc.store.close()


def test_personal_claim_requires_observed_evidence_for_that_student_and_topic(tmp_path):
    svc = DiagnosticsService(tmp_path)
    try:
        report = svc.demo()
        agent = DiagnosticAgent(report, ScriptClient([]))
        agent.summary()
        agent.progress()
        topic = agent.inspect_topic("条件分支")["evidence_id"]
        sid = report["students"][0]["student_id"]
        student = agent.inspect_student(sid, topic="变量与类型")["evidence_id"]
        output = plan(topic="条件分支", refs=[topic, student])
        output["findings"][0]["student_id"] = sid
        with pytest.raises(FinalAnswerError, match="inspect_student"):
            agent.validate(json.dumps(output, ensure_ascii=False))
    finally:
        svc.store.close()


def test_no_assessed_data_produces_missing_data_advice_without_exercises(tmp_path):
    final = {"summary": "尚未授课，暂不能据此判定薄弱知识点。", "summary_evidence_ids": ["E2"],
             "findings": [], "reteaching": [], "exercises": [], "missing_data": ["核对教学进度后再补充已授课考核。"]}
    responses = [call("get_class_summary", {}), call("get_rubric_and_progress", {}, "progress"),
                 {"role": "assistant", "content": json.dumps(final, ensure_ascii=False)}]
    svc = DiagnosticsService(tmp_path, agent_client=ScriptClient(responses))
    try:
        syllabus = svc.schema()["syllabus"]
        syllabus["taught_through_week"] = 0
        report = svc.upload("class.csv", svc.sample()[1].encode(), syllabus_raw=json.dumps(syllabus).encode())
        assert report["agent"]["status"] == "completed"
        assert report["agent"]["result"]["exercises"] == []
        assert report["summary"]["average_mastery"] is None
    finally:
        svc.store.close()


def test_not_taught_topic_cannot_receive_a_diagnostic_finding_even_with_real_evidence(tmp_path):
    svc = DiagnosticsService(tmp_path)
    try:
        syllabus = svc.schema()["syllabus"]
        syllabus["taught_through_week"] = 0
        report = svc.upload("class.csv", svc.sample()[1].encode(), syllabus_raw=json.dumps(syllabus).encode())
        agent = DiagnosticAgent(report, ScriptClient([]))
        agent.summary()
        agent.progress()
        ident = agent.inspect_topic("变量与类型")["evidence_id"]
        final = {"summary": "需要核对证据。", "summary_evidence_ids": ["E1"],
                 "findings": [{"topic": "变量与类型", "statement": "全班已确认掌握薄弱。", "evidence_ids": [ident]}],
                 "reteaching": [], "exercises": [], "missing_data": []}
        with pytest.raises(FinalAnswerError, match="finding_requires_assessed"):
            agent.validate(json.dumps(final, ensure_ascii=False))
    finally:
        svc.store.close()


def test_report_and_latest_agent_review_are_read_as_one_snapshot(tmp_path, monkeypatch):
    svc = DiagnosticsService(tmp_path, agent_client=ScriptClient(flow()))
    try:
        first = svc.demo()
        reviewed = svc.review(first["id"], "教师", agent_run_id=first["agent"]["id"])
        next_run = copy.deepcopy(first["agent"])
        next_run.update(id="agent_concurrent_fixture", review={"status": "pending", "reviewer": "", "note": "", "reviewed_at": None})
        original_get = svc.store.get
        inserted = []

        def save_between_legacy_reads(report_id):
            report = original_get(report_id)
            if not inserted:
                inserted.append(True)
                svc.store.save_agent(report_id, next_run)
            return report

        # 旧实现的两次门面读取之间插入一次新运行；原子视图应避免这一读取路径。
        monkeypatch.setattr(svc.store, "get", save_between_legacy_reads)
        current = svc.get_report(first["id"])
        assert current["review"] == current["agent"]["review"]
        assert (current["agent"]["id"], current["review"]["status"]) in {
            (reviewed["agent"]["id"], "reviewed"), (next_run["id"], "pending")}
    finally:
        svc.store.close()
