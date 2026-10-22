"""真实 HTTP 协议集成测试：本机假提供商 + 真 Qdrant，不下载/加载任何模型。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app.core.embeddings import EmbeddingClient
from app.main import create_app
from app.settings import RAGSettings


@pytest.fixture
def provider():
    calls = []
    state = {"bad_embeddings": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append({"path": self.path, "body": body, "authorization": self.headers.get("Authorization")})
            if self.path == "/v1/embeddings":
                data = []
                for i, text in enumerate(body["input"]):
                    vector = [0.0, 1.0, 0.0] if "二叉树" in text else [1.0, 0.0, 0.0]
                    data.append({"object": "embedding", "index": i, "embedding": vector})
                result = {"object": "list", "model": body["model"], "data": list(reversed(data)), "usage": {"prompt_tokens": 5, "total_tokens": 5}}
                if state["bad_embeddings"] is not None:
                    result = state["bad_embeddings"]
            elif self.path == "/v1/chat/completions":
                result = {"id": "test-chat", "object": "chat.completion", "model": body["model"],
                          "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "平时成绩占30%，上机实验占20%，期末考试占50% [1]。"}}],
                          "usage": {"prompt_tokens": 12, "completion_tokens": 10, "total_tokens": 22}}
            else:
                self.send_error(404)
                return
            raw = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", calls, state
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_embeddings_http_orders_results_and_normalizes_vectors(provider):
    """提供商可打乱 index 顺序；映射错位会使检索引用错误资料。"""
    base, calls, state = provider
    client = EmbeddingClient(base_url=base, api_key="embedding-test-only", model="test-embedding", dimension=3)
    state["bad_embeddings"] = {"data": [{"index": 1, "embedding": [0.0, 4.0, 0.0]}, {"index": 0, "embedding": [3.0, 0.0, 0.0]}]}
    assert client.encode_documents(["考核", "二叉树"]) == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert calls[0]["path"] == "/v1/embeddings"
    assert calls[0]["authorization"] == "Bearer embedding-test-only"
    assert calls[0]["body"]["input"] == ["考核", "二叉树"]
    assert calls[0]["body"]["model"] == "test-embedding"
    assert base not in str(client.describe())
    assert "embedding-test-only" not in str(client.describe())


@pytest.mark.parametrize("payload", [
    {"data": []},
    {"data": [{"index": 0, "embedding": [1.0, 0.0]}]},
    {"data": [{"index": 0, "embedding": [0.0, 0.0, 0.0]}]},
    {"data": [{"index": 0, "embedding": [float("nan"), 0.0, 1.0]}]},
    {"data": [{"index": 0, "embedding": [True, 0.0, 1.0]}]},
    {"data": [{"index": 1, "embedding": [1.0, 0.0, 0.0]}]},
])
def test_malformed_embeddings_are_not_written_as_valid_vectors(provider, payload):
    base, _, state = provider
    state["bad_embeddings"] = payload
    client = EmbeddingClient(base_url=base, model="test-embedding", dimension=3)
    with pytest.raises(RuntimeError):
        client.encode_documents(["考核规则"])


def test_rag_uses_both_http_apis_and_persistent_vector_database(provider, tmp_path):
    """运行完整 HTTP→Embedding API→Qdrant→回答 API 链路，而非直接替换 service.answer。"""
    base, calls, _ = provider
    settings = RAGSettings(embedding_base_url=base, embedding_api_key="embedding-test-only", embedding_model="test-embedding",
                           embedding_dimension=3, llm_base_url=base, llm_api_key="chat-test-only", llm_model="test-chat")
    with TestClient(create_app(tmp_path, settings=settings)) as client:
        response = client.post("/api/knowledge/documents", json={"title": "Python考核", "content": "课程总评由三部分组成：平时成绩占30%；上机实验占20%；期末考试占50%。"})
        assert response.status_code == 201
        doc_id = response.json()["id"]
        result = client.post("/api/knowledge/ask", json={"question": "Python成绩怎么算？"}).json()
        assert result["mode"] == "llm" and result["refused"] is False
        assert all(value in result["answer"] for value in ("30%", "20%", "50%"))
        assert result["citations"][0]["document_id"] == doc_id
        stats = client.get("/api/knowledge/stats").json()
        assert stats["retrieval"]["vector_store"]["engine"] == "Qdrant"
        assert stats["retrieval"]["vector_store"]["chunks"] == 1
        assert stats["llm"]["provider"] == "openai-compatible"
        assert base not in json.dumps(stats)
        assert "chat-test-only" not in json.dumps(stats)
        assert client.delete(f"/api/knowledge/documents/{doc_id}").status_code == 200
        assert client.post("/api/knowledge/ask", json={"question": "Python成绩怎么算？"}).json()["refused"] is True
    assert {call["path"] for call in calls} == {"/v1/embeddings", "/v1/chat/completions"}
    chat = next(call for call in calls if call["path"].endswith("chat/completions"))
    assert chat["authorization"] == "Bearer chat-test-only"
    assert "课程总评由三部分组成" in chat["body"]["messages"][1]["content"]


def test_rag_requires_explicit_api_configuration_and_never_downloads_models(tmp_path):
    with pytest.raises(RuntimeError, match="EDU_EMBEDDING"):
        create_app(tmp_path, settings=RAGSettings())
