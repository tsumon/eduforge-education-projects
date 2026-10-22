"""OpenAI 兼容 Embedding API 适配器；没有本地模型或模型下载行为。"""
from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.request


class EmbeddingClient:
    def __init__(self, base_url: str, model: str, dimension: int, api_key: str = "", *,
                 version: str = "1", timeout: float = 30.0, batch_size: int = 16, query_prefix: str = ""):
        if not 1 <= dimension <= 65536 or not 1 <= batch_size <= 256:
            raise ValueError("Embedding 维度或批量大小无效")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dimension = dimension
        self.version = version
        self.timeout = timeout
        self.batch_size = batch_size
        self.query_prefix = query_prefix
        # 地址只进入不可逆签名，不出现在 stats、引用或导出中；换接口/模型/维度后重建。
        self.fingerprint = hashlib.sha256(json.dumps([self.base_url, model, dimension, version, query_prefix,
                                                     "l2-v1"], ensure_ascii=False).encode("utf-8")).hexdigest()

    def configured(self):
        return bool(self.base_url and self.model)

    def _request(self, texts):
        if not self.configured():
            raise RuntimeError("未配置 EDU_EMBEDDING_BASE_URL / EDU_EMBEDDING_MODEL")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.base_url + "/embeddings",
            data=json.dumps({"model": self.model, "input": texts, "encoding_format": "float"}, ensure_ascii=False).encode("utf-8"),
            headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            rows = payload["data"]
            if not isinstance(rows, list) or len(rows) != len(texts):
                raise ValueError("wrong result count")
            ordered = [None] * len(texts)
            for row in rows:
                index, vector = row["index"], row["embedding"]
                if type(index) is not int or not 0 <= index < len(texts) or ordered[index] is not None:
                    raise ValueError("invalid/duplicate index")
                if not isinstance(vector, list) or len(vector) != self.dimension:
                    raise ValueError("dimension mismatch")
                if any(type(value) not in {int, float} or not math.isfinite(value) for value in vector):
                    raise ValueError("non-finite/non-numeric embedding")
                norm = math.sqrt(sum(value * value for value in vector))
                if not norm or not math.isfinite(norm):
                    raise ValueError("zero/overflow embedding")
                ordered[index] = [value / norm for value in vector]
            return ordered
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise RuntimeError("Embedding 接口请求失败，请检查地址、凭证、配额和超时设置") from exc
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("Embedding 接口响应异常，请检查返回数量、index、向量维度和数值") from exc

    def encode_documents(self, texts):
        output = []
        for start in range(0, len(texts), self.batch_size):
            output.extend(self._request(texts[start:start + self.batch_size]))
        return output

    def encode_query(self, text):
        return self._request([self.query_prefix + text])[0]

    def describe(self):
        return {"configured": self.configured(), "provider": "openai-compatible", "model": self.model or None,
                "dimension": self.dimension, "revision": self.version, "normalization": "L2"}
