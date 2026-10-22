"""真实 HTTP 协议 + LangGraph + Embedding + Qdrant + SQLite；提供商为本地脚本。"""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import RAGSettings
from tests.test_agent_runtime import call
from tests.test_rag_http import provider


def settings(base):
    return RAGSettings(embedding_base_url=base, embedding_model="fixture-embedding", embedding_dimension=3,
                       llm_base_url=base, llm_api_key="fixture-chat-only", llm_model="fixture-tools")


def test_default_agent_uses_http_tools_and_can_rewrite_a_failed_search(provider, tmp_path):
    base, calls, state = provider

    def respond(body):
        observations = [json.loads(message["content"]) for message in body["messages"] if message["role"] == "tool"]
        if not observations:
            return call("search_knowledge", {"query": "二叉树"})
        if len(observations) == 1:
            assert observations[-1]["data"]["sources"] == []
            return call("search_knowledge", {"query": "Python成绩考核"}, "search_again")
        if len(observations) == 2:
            source_id = observations[-1]["data"]["sources"][0]["source_id"]
            return call("read_evidence", {"source_ids": [source_id]}, "read")
        return {"role": "assistant", "content": "平时成绩占30%，上机实验占20%，期末考试占50%。[1]"}

    state["chat_handler"] = respond
    with TestClient(create_app(tmp_path, settings=settings(base))) as client:
        client.post("/api/knowledge/documents", json={"title": "Python考核", "content": "课程总评由三部分组成：平时成绩占30%；上机实验占20%；期末考试占50%。"}).raise_for_status()
        result = client.post("/api/knowledge/ask", json={"question": "Python成绩怎么算？"}).json()
        assert result["mode"] == "agent" and not result["refused"]
        run = result["diagnostics"]["agent"]
        assert run["framework"] == "langgraph" and run["tool_calls"] == 3
        assert client.get("/api/knowledge/history").json()["history"][0] == result
        assert client.get("/api/knowledge/stats").json()["agent"]["enabled"] is True
        assert base not in json.dumps(result) and "fixture-chat-only" not in json.dumps(result)
        assert "LangGraph" in client.get(f"/api/knowledge/export/{result['id']}").text
    chats = [request for request in calls if request["path"].endswith("chat/completions")]
    assert len(chats) == 4
    assert chats[0]["authorization"] == "Bearer fixture-chat-only"
    assert {t["function"]["name"] for t in chats[0]["body"]["tools"]} == {"search_knowledge", "read_evidence", "check_answer"}
    assert any(request["path"].endswith("embeddings") for request in calls)


@pytest.mark.parametrize("message", [{"role": "assistant", "content": 123},
    {"role": "assistant", "tool_calls": [{"id": "x", "type": "function", "function": {"name": "read_evidence", "arguments": {"source_ids": [1]}}}]},
    {"role": "system", "content": "provider-injected-text"}])
def test_invalid_provider_protocol_is_a_persisted_failure(provider, tmp_path, message):
    base, _, state = provider
    state["chat_handler"] = lambda body: message
    with TestClient(create_app(tmp_path, settings=settings(base))) as client:
        result = client.post("/api/knowledge/ask", json={"question": "解释Python考核"}).json()
        assert result["refused"] and result["citations"] == []
        assert result["diagnostics"]["agent"]["status"] == "failed"
        assert "provider-injected-text" not in json.dumps(result)
