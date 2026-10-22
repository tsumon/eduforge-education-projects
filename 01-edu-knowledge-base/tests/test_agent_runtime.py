import json

from pydantic import BaseModel, ConfigDict

from app.agent_runtime import AgentRunner, FinalAnswerError, ToolSpec
from langsmith import get_tracing_context


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: str


def call(name, value, ident="call_1"):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(value)}}]}


class ScriptClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def configured(self):
        return True

    def describe(self):
        return {"configured": True, "model": "fixture", "provider": "openai-compatible"}

    def complete(self, messages, tools):
        self.requests.append((list(messages), tools))
        return next(self.responses)


def test_unknown_tool_and_invalid_arguments_can_be_corrected():
    client = ScriptClient([call("shell", {"value": "x"}), call("lookup", {"other": 1}, "c2"),
                           call("lookup", {"value": "real"}, "c3"), {"role": "assistant", "content": "done"}])
    executed = []
    runner = AgentRunner(client, [ToolSpec("lookup", "Read evidence", Args,
                                         lambda value: executed.append(value) or {"evidence": value})],
                         lambda text, events: {"answer": text})
    result = runner.run("Use only tools", "Find evidence")
    assert result["framework"] == "langgraph" and result["status"] == "completed"
    assert executed == ["real"] and result["tool_calls"] == 3
    assert [e["status"] for e in result["events"] if e["kind"] == "tool"] == ["error", "error", "ok"]
    observations = [m for m in client.requests[-1][0] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in observations] == ["call_1", "c2", "c3"]
    assert "unknown_tool" in observations[0]["content"]


def test_rejected_final_returns_to_model_without_exposing_it_as_result():
    def validate(text, events):
        if text != "grounded":
            raise FinalAnswerError("read_sources_first")
        return {"answer": text}

    client = ScriptClient([{"role": "assistant", "content": "invented"},
                           {"role": "assistant", "content": "grounded"}])
    result = AgentRunner(client, [], validate).run("system", "question")
    assert result["status"] == "completed" and result["result"] == {"answer": "grounded"}
    assert result["events"][0]["reason"] == "read_sources_first"
    assert any("read_sources_first" in m["content"] for m in client.requests[-1][0] if m["role"] == "user")


def test_budget_stops_repeated_tool_calls_and_never_returns_an_unvalidated_answer():
    client = ScriptClient([call("lookup", {"value": "a"}, f"c{i}") for i in range(6)])
    executed = []
    runner = AgentRunner(client, [ToolSpec("lookup", "Read", Args, lambda value: executed.append(value) or {})],
                         lambda text, events: {}, max_steps=8, max_tool_calls=2)
    result = runner.run("system", "question")
    assert result["status"] == "limited" and result["result"] is None
    assert len(executed) == 2 and len(client.requests) == 3
    assert result["steps"] == len(client.requests)


def test_unconfigured_client_does_not_execute_tools():
    client = ScriptClient([])
    client.configured = lambda: False
    result = AgentRunner(client, [], lambda text, events: {}).run("system", "question")
    assert result["status"] == "not_configured" and not client.requests


def test_inherited_langsmith_tracing_is_disabled_inside_graph_nodes(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    client = ScriptClient([])

    def complete(messages, tools):
        assert get_tracing_context()["enabled"] is False
        return {"role": "assistant", "content": "done"}

    client.complete = complete
    result = AgentRunner(client, [], lambda text, events: {"answer": text}).run("private system", "private goal")
    assert result["status"] == "completed"
