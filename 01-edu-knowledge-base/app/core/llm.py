"""可选的大模型生成模式（OpenAI 兼容 /chat/completions）。

未配置环境变量时 `LLMClient.configured()` 为 False，系统只走抽取式。生成结果仍必须：
- 只引用提供的 [n] 片段；
- 资料不足时以 REFUSAL_PREFIX 开头拒答。
服务层会校验引用编号合法，非法则降级为抽取式并在 diagnostics 记录。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .answer import REFUSAL_PREFIX

SYSTEM_PROMPT = (
    "你是课程助教。只能依据给定的编号资料片段回答，每个结论后用 [n] 标注来源编号；"
    "不得使用片段之外的知识。若片段不足以回答，必须以「" + REFUSAL_PREFIX + "」开头说明缺少什么资料。"
    "用简体中文、不超过 200 字。"
)


class LLMClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None, timeout: float = 30.0):
        self.base_url = (base_url or os.environ.get("EDU_LLM_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("EDU_LLM_API_KEY", "")
        self.model = model or os.environ.get("EDU_LLM_MODEL", "")
        self.timeout = timeout

    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    def describe(self) -> dict:
        return {"configured": self.configured(), "model": self.model if self.configured() else None}

    def answer(self, question: str, passages: list[tuple[int, str, str]]) -> str:
        """passages: [(n, title, text)]。返回模型文本；网络/格式错误抛 RuntimeError。"""
        if not self.configured():
            raise RuntimeError("LLM 未配置")
        ctx = "\n\n".join(f"[{n}]《{title}》\n{text}" for n, title, text in passages)
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"资料片段：\n{ctx}\n\n问题：{question}"},
            ],
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(f"LLM 请求失败：{e}") from e
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise RuntimeError("LLM 返回格式异常") from e
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            raise RuntimeError("LLM 返回格式异常") from e
