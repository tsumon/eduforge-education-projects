"""RAG 边界测试：用真实持久化 Qdrant，固定向量仅替代耗时模型计算。"""
import pytest

from app.core.answer import AnswerPolicy, Evidence, compose_extractive
from app.core.chunker import chunk_text
from app.core.llm import LLMClient
from app.core.vector_store import VectorIndex
from app.service import KnowledgeService


class TinyEmbedder:
    dimension = 3
    fingerprint = "tiny-revision-1"

    def __init__(self):
        self.encoded = []
        self.broken = False

    def encode_documents(self, texts):
        if self.broken:
            raise RuntimeError("embedding unavailable")
        self.encoded.extend(texts)
        return [[0.0, 1.0, 0.0] if "二叉树" in text else [1.0, 0.0, 0.0] for text in texts]

    def encode_query(self, text):
        if self.broken:
            raise RuntimeError("query embedding unavailable")
        return [0.0, 1.0, 0.0] if "树" in text else [1.0, 0.0, 0.0]

    def describe(self):
        return {"model": "test-only", "dimension": self.dimension, "revision": self.fingerprint}


def chunk(cid, text, title="讲义"):
    return {"id": cid, "document_id": "doc_test", "title": title, "heading": "正文", "text": text}


@pytest.fixture
def vector_index(tmp_path):
    embedder = TinyEmbedder()
    index = VectorIndex(tmp_path / "vectors", embedder)
    yield index, embedder
    index.close()


def test_semantic_lookup_returns_persisted_source_id(vector_index):
    """忽略编码器返回值或丢失 ID，会把同义查询指向错误原文。"""
    index, _ = vector_index
    index.sync([chunk("a", "课程考核规则"), chunk("b", "二叉树遍历")])
    assert index.search("树状结构", top_k=1) == [("b", 1.0)]


def test_restart_reuses_embeddings_and_delete_removes_stale_points(tmp_path):
    """重启不应重算所有向量，删除后不可继续返回旧分片。"""
    embedder = TinyEmbedder()
    index = VectorIndex(tmp_path / "vectors", embedder)
    rows = [chunk("a", "课程考核规则"), chunk("b", "二叉树遍历")]
    index.sync(rows)
    index.close()
    reopened = VectorIndex(tmp_path / "vectors", embedder)
    try:
        embedder.encoded.clear()
        reopened.sync(rows)
        assert embedder.encoded == []
        reopened.sync(rows[:1])
        assert [cid for cid, _ in reopened.search("树状结构")] == ["a"]
        assert reopened.describe()["chunks"] == 1
        reopened.sync([])
        assert reopened.search("考核") == []
    finally:
        reopened.close()


def test_content_and_model_changes_invalidate_stored_vectors(vector_index):
    """相同 ID 修改正文或模型 revision 后，旧向量不可复用。"""
    index, embedder = vector_index
    index.sync([chunk("a", "课程考核规则")])
    embedder.encoded.clear()
    index.sync([chunk("a", "二叉树遍历")])
    assert len(embedder.encoded) == 1
    embedder.encoded.clear()
    embedder.fingerprint = "tiny-revision-2"
    index.sync([chunk("a", "二叉树遍历")])
    assert len(embedder.encoded) == 1
    assert index.search("树状结构", top_k=1) == [("a", 1.0)]


def test_embedding_failure_preserves_existing_points(vector_index):
    """新增编码失败前不得先清空已完成的索引。"""
    index, embedder = vector_index
    index.sync([chunk("a", "课程考核规则")])
    embedder.broken = True
    with pytest.raises(RuntimeError, match="embedding unavailable"):
        index.sync([chunk("a", "课程考核规则"), chunk("b", "二叉树遍历")])
    embedder.broken = False
    assert index.search("考核", top_k=1) == [("a", 1.0)]


def test_extractive_fallback_keeps_entire_semicolon_separated_grade_rule():
    """回归：以前只抽前两个分号分句，遗漏期末 50%。"""
    evidence = Evidence("a", "doc", "Python", "考核", "课程总评由三部分组成：平时成绩占30%；上机实验占20%；期末考试占50%。", 10, 1)
    text = compose_extractive("Python 成绩怎么算", [evidence], AnswerPolicy())
    assert all(part in text for part in ("30%", "20%", "50%"))


class ScriptedLLM(LLMClient):
    def __init__(self, reply):
        super().__init__(base_url="http://unused", model="test-boundary")
        self.reply = reply

    def answer(self, question, passages):
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_semantic_evidence_bypasses_keyword_coverage_gate(tmp_path, vector_index):
    """高语义相似度的同义问法，不应再被旧关键词覆盖率拒绝。"""
    index, _ = vector_index
    svc = KnowledgeService(tmp_path / "db", vector_index=index,
                           llm=ScriptedLLM("平时30%，实验20%，期末50% [1]。"))
    try:
        doc = svc.add_document("考核", "课程总评：平时成绩占30%；上机实验占20%；期末考试占50%。", "课程资料")
        result = svc.ask("掌握情况通过哪些渠道评定？")
        assert result["refused"] is False
        assert result["mode"] == "llm"
        assert result["diagnostics"]["retrieval"]["mode"] == "hybrid"
        assert result["citations"][0]["document_id"] == doc["id"]
        assert result["citations"][0]["semantic_score"] == 1.0
    finally:
        svc.close()


@pytest.mark.parametrize("reply, reason", [
    ("期末考试占99% [1]。", "unsupported_numbers"),
    ("期末考试占50% [8]。", "invalid_citations"),
    ("期末考试占50%。", "invalid_citations"),
    ("期末考试占50% [1]。\n还需要额外交费。", "uncited_paragraph"),
    (RuntimeError("credential-must-not-appear"), "error:"),
])
def test_ungrounded_or_failed_generation_is_explicitly_degraded(tmp_path, reply, reason):
    """模型失败、漏标引用或编造数字，必须降级且不得冒充模型成功。"""
    svc = KnowledgeService(tmp_path, llm=ScriptedLLM(reply))
    try:
        svc.add_document("考核", "课程期末考试占50%，形式为闭卷笔试，时长120分钟。请遵守课堂考核要求。", "课程资料")
        result = svc.ask("期末考试占多少？")
        assert result["mode"] == "extractive"
        assert "50%" in result["answer"]
        assert result["diagnostics"]["degraded"] is True
        assert reason in result["diagnostics"]["llm"]
        assert "credential-must-not-appear" not in str(result)
    finally:
        svc.close()


def test_failed_query_embedding_does_not_masquerade_as_lexical_success(tmp_path, vector_index):
    index, embedder = vector_index
    svc = KnowledgeService(tmp_path / "db", vector_index=index)
    try:
        svc.add_document("考核", "平时成绩占30%；上机实验占20%；期末考试占50%。", "课程资料")
        embedder.broken = True
        with pytest.raises(RuntimeError):
            svc.ask("成绩怎么算")
        assert svc.history()["total"] == 0
    finally:
        svc.close()


def test_unpunctuated_long_text_remains_fully_chunked():
    """无标点长文也要分片，不能把尾部事实静默截出模型输入。"""
    text = "课程资料内容" * 300 + "最后的评分规则是期末占50%。"
    rows = chunk_text(text)
    assert len(rows) >= 4
    assert all(len(row.text) <= 480 for row in rows)
    assert "最后的评分规则是期末占50%。" in rows[-1].text


def test_api_exposes_index_failure_and_rebuild_recovers(tmp_path, vector_index):
    """索引写入失败要返回 503；修复后重建能恢复已保存资料，关闭后可重开 Qdrant。"""
    from fastapi.testclient import TestClient
    from app.main import create_app

    index, embedder = vector_index
    svc = KnowledgeService(tmp_path / "db", vector_index=index, llm=LLMClient(base_url="", model=""))
    app = create_app(service=svc)
    with TestClient(app) as client:
        embedder.broken = True
        response = client.post("/api/knowledge/documents", json={
            "title": "考核", "content": "课程总评：平时成绩占30%；上机实验占20%；期末考试占50%。"})
        assert response.status_code == 503
        assert client.get("/api/knowledge/stats").json()["retrieval"]["ready"] is False
        assert client.post("/api/knowledge/ask", json={"question": "成绩怎么算"}).status_code == 503
        embedder.broken = False
        assert client.post("/api/knowledge/index/rebuild").status_code == 200
        stats = client.get("/api/knowledge/stats").json()
        assert stats["retrieval"]["ready"] is True
        assert stats["retrieval"]["vector_store"]["chunks"] == stats["chunk_count"] == 1
        assert client.post("/api/knowledge/ask", json={"question": "成绩怎么算"}).status_code == 200
    reopened = VectorIndex(index.path, embedder)
    reopened.close()


def test_unknown_english_subject_is_not_replaced_with_known_subject(tmp_path, vector_index):
    index, _ = vector_index
    svc = KnowledgeService(tmp_path / "db", vector_index=index, llm=ScriptedLLM("Java递归深度是1000 [1]。"))
    try:
        svc.add_document("Python讲义", "Python解释器默认递归深度为1000层，超过深度会抛出异常。", "课程资料")
        result = svc.ask("Java递归深度限制是多少？")
        assert result["refused"] is True
        assert result["citations"] == []
    finally:
        svc.close()


def test_failed_generation_with_only_semantic_evidence_refuses_instead_of_extracting(tmp_path, vector_index):
    index, _ = vector_index
    svc = KnowledgeService(tmp_path / "db", vector_index=index, llm=ScriptedLLM("不能证明的回答"))
    try:
        svc.add_document("考核", "课程总评：平时成绩占30%；上机实验占20%；期末考试占50%。", "课程资料")
        result = svc.ask("掌握情况通过哪些渠道评定？")
        assert result["refused"] is True
        assert result["citations"] == []
        assert result["diagnostics"]["degraded"] is True
    finally:
        svc.close()


@pytest.mark.parametrize("name", [".model-cache/model.safetensors", "runtime-rag-check/qdrant/meta.json",
                                  "runtime-review/knowledge.sqlite3", ".env.production", ".uv-cache/file.json"])
def test_delivery_excludes_models_runtime_and_secret_files(name):
    from scripts.package import ROOT, should_skip
    assert should_skip(ROOT / name) is True


def test_subject_present_in_document_title_can_be_answered(tmp_path, vector_index):
    index, _ = vector_index
    svc = KnowledgeService(tmp_path / "db", vector_index=index, llm=ScriptedLLM("接口用于定义契约，可以支持多重实现 [1]。"))
    try:
        svc.add_document("Java课程讲义", "接口用于定义契约，可以支持多重实现。抽象类可提供部分实现。", "课程资料")
        result = svc.ask("Java接口支持多重实现吗？")
        assert result["refused"] is False
        assert result["mode"] == "llm"
    finally:
        svc.close()


def test_openai_timeout_is_degraded_without_leaking_error(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise TimeoutError("credential-must-not-appear")
    monkeypatch.setattr("urllib.request.urlopen", timeout)
    svc = KnowledgeService(tmp_path, llm=LLMClient(base_url="http://unused/v1", model="test-only"))
    try:
        svc.add_document("考核", "期末考试占50%，形式为闭卷笔试，时长120分钟。请遵守课堂考核要求。", "课程资料")
        result = svc.ask("期末考试占多少？")
        assert result["mode"] == "extractive"
        assert result["diagnostics"]["degraded"] is True
        assert "credential-must-not-appear" not in str(result)
    finally:
        svc.close()
