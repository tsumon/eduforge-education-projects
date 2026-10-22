"""有界 LangGraph 工具调用循环。此文件在两个独立交付项目中保持相同。"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, TypedDict
from urllib.parse import urlsplit

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import BaseModel, ValidationError


class FinalAnswerError(ValueError):
    """领域核验失败；内容必须是固定、安全的错误说明。"""


class ToolInputError(ValueError):
    """工具可向模型解释的受控输入错误。"""


def checked_observation(value):
    """有状态工具须在提交可引用状态前调用，与运行器使用同一观察预算。"""
    if len(json.dumps({"ok": True, "data": value}, ensure_ascii=False, allow_nan=False)) > 20_000:
        raise ToolInputError("observation_too_large_use_narrower_arguments")
    return value


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    arguments: type[BaseModel]
    execute: Callable

    def schema(self):
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                "parameters": self.arguments.model_json_schema()}}


class ToolChatClient:
    """仅请求配置的 /chat/completions，要求提供商支持 tools/tool_calls。"""
    def __init__(self, *, base_url="", api_key="", model="", timeout=30.0):
        if base_url:
            url = urlsplit(base_url)
            if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.query or url.fragment:
                raise ValueError("EDU_LLM_BASE_URL 必须为不含认证信息与查询参数的 HTTP(S) 基础地址")
        if not 0 < timeout <= 300:
            raise ValueError("API 超时须为 0..300 秒")
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model
        self.timeout = timeout

    def configured(self):
        return bool(self.base_url and self.model)

    def describe(self):
        return {"configured": self.configured(), "model": self.model if self.configured() else None,
                "provider": "openai-compatible" if self.configured() else "none"}

    def complete(self, messages, tools):
        body = {"model": self.model, "messages": messages, "tools": tools, "tool_choice": "auto",
                "temperature": 0, "max_tokens": 4096}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = urllib.request.Request(self.base_url + "/chat/completions", method="POST", headers=headers,
                                         data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("response_too_large")
            choice = json.loads(raw)["choices"][0]
            if choice.get("finish_reason") in {"length", "content_filter"}:
                raise ValueError("incomplete_response")
            return clean_message(choice["message"])
        except (urllib.error.URLError, OSError, TimeoutError, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            # 不回传包含 URL、请求头或提供商正文的异常。
            raise RuntimeError("model_api_failed_or_invalid_tool_protocol") from exc


def clean_message(message):
    if not isinstance(message, dict) or message.get("role", "assistant") != "assistant":
        raise ValueError("invalid_assistant_message")
    content, calls = message.get("content"), message.get("tool_calls", [])
    if content is not None and (not isinstance(content, str) or len(content) > 12_000):
        raise ValueError("invalid_content")
    if not isinstance(calls, list) or len(calls) > 4 or (not calls and not content):
        raise ValueError("invalid_tool_calls")
    result, cleaned, ids = {"role": "assistant", "content": content}, [], set()
    for call in calls:
        function = call.get("function", {})
        name, args, ident = function.get("name"), function.get("arguments"), call.get("id")
        if (call.get("type") != "function" or not isinstance(ident, str) or not 1 <= len(ident) <= 120
                or ident in ids or not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}", name)
                or not isinstance(args, str) or len(args) > 8_000):
            raise ValueError("invalid_tool_call")
        ids.add(ident)
        cleaned.append({"id": ident, "type": "function", "function": {"name": name, "arguments": args}})
    if cleaned:
        result["tool_calls"] = cleaned
    return result


class RunState(TypedDict, total=False):
    messages: list
    steps: int
    tool_calls: int
    events: list
    next: str
    final_text: str
    result: dict | None
    status: str
    error: str | None


class AgentRunner:
    def __init__(self, client, tools, validate, *, max_steps=8, max_tool_calls=12):
        if not 1 <= max_steps <= 20 or not 1 <= max_tool_calls <= 40:
            raise ValueError("Agent 步数或调用数超出范围")
        self.client, self.validate = client, validate
        self.tools = {tool.name: tool for tool in tools}
        self.max_steps, self.max_tool_calls = max_steps, max_tool_calls
        graph = StateGraph(RunState)
        graph.add_node("model", self._model)
        graph.add_node("tools", self._tools)
        graph.add_node("validate", self._validate)
        graph.add_edge(START, "model")
        graph.add_conditional_edges("model", lambda state: state["next"], {"tools": "tools", "validate": "validate", "end": END})
        graph.add_conditional_edges("tools", lambda state: "end" if state["status"] == "limited" else "model", {"model": "model", "end": END})
        graph.add_conditional_edges("validate", lambda state: state["next"], {"model": "model", "end": END})
        self.graph = graph.compile()

    def _model(self, state):
        if state["steps"] >= self.max_steps:
            return {"status": "limited", "error": state["error"] or "step_limit", "next": "end"}
        try:
            message = clean_message(self.client.complete(state["messages"], [t.schema() for t in self.tools.values()]))
        except Exception:
            return {"steps": state["steps"] + 1, "status": "failed", "error": "model_api_failed_or_invalid_tool_protocol", "next": "end"}
        return {"steps": state["steps"] + 1, "messages": state["messages"] + [message],
                "final_text": message.get("content") or "", "next": "tools" if message.get("tool_calls") else "validate"}

    def _tools(self, state):
        calls = state["messages"][-1]["tool_calls"]
        if state["tool_calls"] + len(calls) > self.max_tool_calls:
            return {"status": "limited", "error": "tool_limit"}
        observations, events = [], list(state["events"])
        for call in calls:
            name, arguments = call["function"]["name"], None
            tool = self.tools.get(name)
            try:
                if tool is None:
                    raise ToolInputError("unknown_tool")
                arguments = tool.arguments.model_validate_json(call["function"]["arguments"]).model_dump()
                value = tool.execute(**arguments)
                checked_observation(value)
                observation, status = {"ok": True, "data": value}, "ok"
            except ValidationError:
                observation, status = {"ok": False, "error": "invalid_arguments_check_tool_schema"}, "error"
            except ToolInputError as exc:
                observation, status = {"ok": False, "error": str(exc)}, "error"
            except Exception:
                observation, status = {"ok": False, "error": "tool_unavailable"}, "error"
            events.append({"kind": "tool", "step": state["steps"], "tool": name, "call_id": call["id"],
                           "arguments": arguments, "status": status, "observation": observation})
            observations.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(observation, ensure_ascii=False, allow_nan=False)})
        return {"messages": state["messages"] + observations, "events": events,
                "tool_calls": state["tool_calls"] + len(calls)}

    def _validate(self, state):
        try:
            result = self.validate(state["final_text"], state["events"])
        except FinalAnswerError as exc:
            event = {"kind": "validation", "step": state["steps"], "status": "error", "reason": str(exc)}
            return {"events": state["events"] + [event], "next": "model", "messages": state["messages"] + [
                {"role": "user", "content": "输出未通过核验：" + str(exc) + "。请调用工具补足依据或修正最终输出；不得编造。"}]}
        except Exception:
            return {"status": "failed", "error": "validation_failed", "next": "end"}
        return {"result": result, "status": "completed", "error": None, "next": "end",
                "events": state["events"] + [{"kind": "validation", "step": state["steps"], "status": "ok"}]}

    def run(self, system, goal):
        state = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": goal}],
                 "steps": 0, "tool_calls": 0, "events": [], "result": None, "status": "running", "error": None}
        if self.client.configured():
            # 成绩、课程片段只发往明确配置的模型接口；不继承外部 LangSmith 自动遥测。
            with tracing_context(enabled=False):
                state = self.graph.invoke(state, {"recursion_limit": self.max_steps * 3 + 6})
        else:
            state.update(status="not_configured", error="configure_EDU_LLM_BASE_URL_and_EDU_LLM_MODEL")
        return {"framework": "langgraph", "status": state["status"], "model": self.client.describe(),
                "steps": state["steps"], "tool_calls": state["tool_calls"], "events": state["events"],
                "result": state["result"], "error": state["error"]}
