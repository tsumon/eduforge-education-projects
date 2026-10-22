"""API 配置从环境变量或本机 .env 读取，密钥不进入 repr/API 响应。"""
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class RAGSettings:
    profile: str = "rag"
    llm_provider: str = "openai"
    embedding_base_url: str = ""
    embedding_api_key: str = field(default="", repr=False)
    embedding_model: str = ""
    embedding_dimension: int = 1536
    embedding_version: str = "1"
    embedding_query_prefix: str = ""
    llm_base_url: str = ""
    llm_api_key: str = field(default="", repr=False)
    llm_model: str = ""
    timeout: float = 30.0
    semantic_min_score: float = 0.50
    agent_enabled: bool = True
    agent_max_steps: int = 8

    def __post_init__(self):
        if not 1 <= self.agent_max_steps <= 20:
            raise ValueError("EDU_AGENT_MAX_STEPS 须为 1..20")
        if self.profile not in {"rag", "lexical"} or self.llm_provider not in {"openai", "none"}:
            raise ValueError("profile 必须为 rag/lexical，回答模型类型必须为 openai/none")
        if not 1 <= self.embedding_dimension <= 65536:
            raise ValueError("EDU_EMBEDDING_DIMENSIONS 须为 1..65536")
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 300 or not 0 <= self.semantic_min_score <= 1:
            raise ValueError("接口超时须为 0..300 秒，语义相似度须为 0..1")
        for address in (self.embedding_base_url, self.llm_base_url):
            if address:
                parsed = urlsplit(address)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment or parsed.username:
                    raise ValueError("模型 BASE_URL 须为 HTTP(S) 基础地址，不含用户信息、查询参数或片段")

    def validate_configured(self):
        if self.profile == "rag" and (not self.embedding_base_url or not self.embedding_model):
            raise RuntimeError("完整RAG需配置 EDU_EMBEDDING_BASE_URL、EDU_EMBEDDING_MODEL、EDU_EMBEDDING_DIMENSIONS；参见 .env.example")
        if self.llm_provider == "openai" and (not self.llm_base_url or not self.llm_model):
            raise RuntimeError("回答API需配置 EDU_LLM_BASE_URL 和 EDU_LLM_MODEL；参见 .env.example")

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
        profile = os.environ.get("EDU_RAG_PROFILE", "rag")
        return cls(profile=profile, llm_provider=os.environ.get("EDU_LLM_PROVIDER", "openai" if profile == "rag" else "none"),
                   embedding_base_url=os.environ.get("EDU_EMBEDDING_BASE_URL", ""),
                   embedding_api_key=os.environ.get("EDU_EMBEDDING_API_KEY", ""),
                   embedding_model=os.environ.get("EDU_EMBEDDING_MODEL", ""),
                   embedding_dimension=int(os.environ.get("EDU_EMBEDDING_DIMENSIONS", "1536")),
                   embedding_version=os.environ.get("EDU_EMBEDDING_VERSION", "1"),
                   embedding_query_prefix=os.environ.get("EDU_EMBEDDING_QUERY_PREFIX", ""),
                   llm_base_url=os.environ.get("EDU_LLM_BASE_URL", ""), llm_api_key=os.environ.get("EDU_LLM_API_KEY", ""),
                   llm_model=os.environ.get("EDU_LLM_MODEL", ""), timeout=float(os.environ.get("EDU_API_TIMEOUT", "30")),
                   semantic_min_score=float(os.environ.get("EDU_SEMANTIC_MIN_SCORE", "0.50")),
                   agent_enabled=os.environ.get("EDU_AGENT_ENABLED", "true").lower() in {"true", "1", "yes"},
                   agent_max_steps=int(os.environ.get("EDU_AGENT_MAX_STEPS", "8")))
