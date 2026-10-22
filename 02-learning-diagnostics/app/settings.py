"""模型只通过 API 接入；密钥不进入 repr 或接口返回。"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from .agent_runtime import ToolChatClient


@dataclass
class AgentSettings:
    enabled: bool = True
    max_steps: int = 8
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""
    timeout: float = 30.0

    def __post_init__(self):
        if not 1 <= self.max_steps <= 20:
            raise ValueError("EDU_AGENT_MAX_STEPS 须为 1..20")
        self.client()  # 复用 URL 和超时校验；不会发起请求。

    def client(self):
        return ToolChatClient(base_url=self.base_url, api_key=self.api_key, model=self.model, timeout=self.timeout)

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
        return cls(enabled=os.environ.get("EDU_AGENT_ENABLED", "true").lower() in {"true", "1", "yes"},
                   max_steps=int(os.environ.get("EDU_AGENT_MAX_STEPS", "8")), base_url=os.environ.get("EDU_LLM_BASE_URL", ""),
                   api_key=os.environ.get("EDU_LLM_API_KEY", ""), model=os.environ.get("EDU_LLM_MODEL", ""),
                   timeout=float(os.environ.get("EDU_API_TIMEOUT", "30")))
