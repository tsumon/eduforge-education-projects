import json

from app.service import KnowledgeService
from app.core.answer import AnswerPolicy
from tests.test_agent_runtime import ScriptClient, call


def text(value):
    return {"role": "assistant", "content": value}


def make_service(tmp_path, responses, max_steps=8):
    # 小型单文档语料的 BM25 绝对分偏低；检索阈值的回归单独由 core/RAG 测试覆盖。
    service = KnowledgeService(tmp_path, policy=AnswerPolicy(min_score=0), agent_client=ScriptClient(responses), agent_max_steps=max_steps)
    service.add_document("Python考核", "# Python成绩考核\n课程总评由三部分组成：平时成绩占30%；上机实验占20%；期末考试占50%。", "课程资料")
    return service


def test_agent_searches_rewrites_reads_and_persists_grounded_answer(tmp_path):
    responses = [call("search_knowledge", {"query": "不存在的主题"}),
                 call("search_knowledge", {"query": "Python成绩考核"}, "s2"),
                 call("read_evidence", {"source_ids": [1]}, "r1"),
                 text("平时成绩占30%，上机实验占20%，期末考试占50%。[1]")]
    svc = make_service(tmp_path, responses)
    try:
        result = svc.ask("Python成绩怎么算？")
        assert result["mode"] == "agent" and not result["refused"]
        agent = result["diagnostics"]["agent"]
        assert agent["framework"] == "langgraph" and agent["status"] == "completed"
        assert [e["tool"] for e in agent["events"] if e["kind"] == "tool"] == ["search_knowledge", "search_knowledge", "read_evidence"]
        assert svc.store.get_history(result["id"])["diagnostics"] == result["diagnostics"]
        assert svc.stats()["agent"]["enabled"] is True
    finally:
        svc.close()


def test_model_must_read_real_evidence_and_repair_invented_citations(tmp_path):
    svc = make_service(tmp_path, [text("无依据的答案[99]"), call("search_knowledge", {"query": "Python成绩"}),
        call("read_evidence", {"source_ids": [99]}, "bad_read"),
        call("read_evidence", {"source_ids": [1]}, "read"), text("平时成绩占99%。[1]"),
        text("平时成绩占30%，上机实验占20%，期末考试占50%。[1]")])
    try:
        result = svc.ask("Python成绩怎么算？")
        assert not result["refused"]
        assert "99%" not in result["answer"]
        errors = [e for e in result["diagnostics"]["agent"]["events"] if e["status"] == "error"]
        assert len(errors) == 3
    finally:
        svc.close()


def test_agent_exhaustion_is_recorded_without_falling_back_to_rule_answer(tmp_path):
    svc = make_service(tmp_path, [text("编造[1]")] * 3, max_steps=3)
    try:
        result = svc.ask("Python成绩怎么算？")
        assert result["mode"] == "agent" and result["refused"]
        assert result["citations"] == []
        assert result["diagnostics"]["agent"]["status"] == "limited"
    finally:
        svc.close()


def test_sources_deleted_during_model_run_are_not_cited(tmp_path):
    svc = make_service(tmp_path, [call("search_knowledge", {"query": "Python成绩"}),
        call("read_evidence", {"source_ids": [1]}, "read"), text("平时成绩占30%。[1]")], max_steps=3)
    original = svc.agent_client.complete

    def complete(messages, tools):
        result = original(messages, tools)
        if result.get("content"):
            svc.delete_document(svc.list_documents()[0]["id"])
        return result

    svc.agent_client.complete = complete
    try:
        result = svc.ask("Python成绩怎么算？")
        assert result["refused"] and not result["citations"]
        assert any(e.get("reason") == "sources_changed_search_again" for e in result["diagnostics"]["agent"]["events"])
    finally:
        svc.close()


def test_failed_oversized_read_does_not_make_evidence_citable(tmp_path):
    svc = make_service(tmp_path, [call("search_knowledge", {"query": "Python成绩"}),
        call("read_evidence", {"source_ids": [1]}, "read"), text("平时成绩占30%。[1]")], max_steps=3)
    try:
        # 模拟旧数据库中的超长分片；工具必须完整返回原文，不能失败后仍登记为已读。
        oversized = "Python成绩：平时成绩占30%；上机实验占20%；期末考试占50%。" * 600
        with svc.store.conn:
            svc.store.conn.execute("UPDATE chunks SET text=?", (oversized,))
        svc.rebuild_index()
        result = svc.ask("Python成绩怎么算？")
        read = next(e for e in result["diagnostics"]["agent"]["events"] if e.get("tool") == "read_evidence")
        assert read["status"] == "error"
        assert result["refused"] and result["citations"] == []
    finally:
        svc.close()


def test_long_headings_are_bounded_in_observations_before_registering_sources(tmp_path):
    svc = make_service(tmp_path, [call("search_knowledge", {"query": "Python成绩"}),
        call("read_evidence", {"source_ids": [1]}, "read"), text("平时成绩占30%。[1]")], max_steps=3)
    try:
        with svc.store.conn:
            svc.store.conn.execute("UPDATE chunks SET heading=?", ("Python成绩考核" + "课程标题" * 6000,))
        svc.rebuild_index()
        result = svc.ask("Python成绩怎么算？")
        assert not result["refused"]
        events = result["diagnostics"]["agent"]["events"]
        assert all(e["status"] == "ok" for e in events if e["kind"] == "tool")
        assert all(len(json.dumps(e.get("observation", {}), ensure_ascii=False)) <= 20_000 for e in events)
    finally:
        svc.close()
