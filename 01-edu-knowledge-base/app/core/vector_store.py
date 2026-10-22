"""持久化 Qdrant 索引。SQLite 分片为事实来源；向量按内容和模型签名增量对账。"""
from __future__ import annotations

import hashlib
import math
import threading
import uuid
from pathlib import Path

from qdrant_client import QdrantClient, models


class VectorIndex:
    collection = "knowledge_chunks"

    def __init__(self, path: Path | str, embedder):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self._lock = threading.RLock()
        self.client = QdrantClient(path=str(self.path), force_disable_check_same_thread=True)
        self._closed = False

    def close(self):
        with self._lock:
            if not self._closed:
                self.client.close()
                self._closed = True

    @staticmethod
    def _point_id(chunk_id):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "eduforge/chunk/" + chunk_id))

    def _payload(self, chunk):
        text = f"{chunk['title']}\n{chunk['heading']}\n{chunk['text']}"
        return text, {
            "chunk_id": chunk["id"],
            "document_id": chunk["document_id"],
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "embedding": self.embedder.fingerprint,
        }

    def _records(self):
        if not self.client.collection_exists(self.collection):
            return {}
        records, offset = {}, None
        while True:
            page, offset = self.client.scroll(self.collection, limit=256, offset=offset, with_vectors=False)
            records.update({str(p.id): p.payload for p in page})
            if offset is None:
                return records

    def sync(self, chunks: list[dict], force: bool = False) -> dict:
        with self._lock:
            exists = self.client.collection_exists(self.collection)
            dimension_changed = exists and self.client.get_collection(self.collection).config.params.vectors.size != self.embedder.dimension
            current = self._records() if not dimension_changed else {}
            desired = {}
            for chunk in chunks:
                text, payload = self._payload(chunk)
                desired[self._point_id(chunk["id"])] = (text, payload)
            changed = [(pid, text, payload) for pid, (text, payload) in desired.items() if force or current.get(pid) != payload]
            # 完成所有模型计算并验证维度后才修改 Qdrant，避免编码失败先清空旧索引。
            vectors = self.embedder.encode_documents([text for _, text, _ in changed]) if changed else []
            if len(vectors) != len(changed) or any(
                len(v) != self.embedder.dimension or not all(math.isfinite(float(x)) for x in v) or not any(v)
                for v in vectors
            ):
                raise RuntimeError("Embedding 返回数量、维度或数值异常")
            if dimension_changed:
                self.client.delete_collection(self.collection)
                exists = False
            if not exists:
                self.client.create_collection(self.collection, vectors_config=models.VectorParams(
                    size=self.embedder.dimension, distance=models.Distance.COSINE,
                ))
            points = [models.PointStruct(id=pid, vector=v, payload=payload) for (pid, _, payload), v in zip(changed, vectors)]
            for start in range(0, len(points), 64):
                self.client.upsert(self.collection, points=points[start:start + 64], wait=True)
            obsolete = set(current) - set(desired)
            if obsolete:
                self.client.delete(self.collection, points_selector=models.PointIdsList(points=sorted(obsolete)), wait=True)
            return {"encoded": len(changed), "deleted": len(obsolete), "chunks": len(desired)}

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        with self._lock:
            if not self.client.collection_exists(self.collection) or not self.client.count(self.collection).count:
                return []
            vector = self.embedder.encode_query(query)
            if len(vector) != self.embedder.dimension or not all(math.isfinite(float(v)) for v in vector):
                raise RuntimeError("查询 Embedding 维度或数值异常")
            points = self.client.query_points(self.collection, query=vector, limit=top_k, with_payload=True).points
            return [(p.payload["chunk_id"], float(p.score)) for p in points]

    def describe(self) -> dict:
        with self._lock:
            count = self.client.count(self.collection).count if self.client.collection_exists(self.collection) else 0
            return {"engine": "Qdrant", "storage": "local", "chunks": count, "embedding": self.embedder.describe()}
