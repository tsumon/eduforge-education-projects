"""BM25（Okapi）内存索引 + 覆盖率计算。语料规模面向课程级（数千分片），全量重建代价可忽略。"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class BM25Index:
    k1: float = 1.5
    b: float = 0.75
    docs: list[list[str]] = field(default_factory=list)
    doc_len: list[int] = field(default_factory=list)
    tf: list[Counter] = field(default_factory=list)
    df: Counter = field(default_factory=Counter)
    avgdl: float = 0.0

    @classmethod
    def build(cls, tokenized_docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> "BM25Index":
        idx = cls(k1=k1, b=b)
        idx.docs = list(tokenized_docs)
        idx.doc_len = [len(d) for d in idx.docs]
        idx.tf = [Counter(d) for d in idx.docs]
        for c in idx.tf:
            idx.df.update(c.keys())
        n = len(idx.docs)
        idx.avgdl = (sum(idx.doc_len) / n) if n else 0.0
        return idx

    def __len__(self) -> int:
        return len(self.docs)

    def idf(self, term: str) -> float:
        n = len(self.docs)
        df = self.df.get(term, 0)
        # BM25+ 风格下限 0，避免高频词变负
        return max(0.0, math.log((n - df + 0.5) / (df + 0.5) + 1.0))

    def score(self, query: list[str], i: int) -> float:
        if not self.docs:
            return 0.0
        tf = self.tf[i]
        dl = self.doc_len[i]
        s = 0.0
        for term in set(query):
            f = tf.get(term, 0)
            if not f:
                continue
            idf = self.idf(term)
            denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1.0))
            s += idf * f * (self.k1 + 1) / denom
        return s

    def search(self, query: list[str], top_k: int = 5) -> list[tuple[int, float]]:
        if not query or not self.docs:
            return []
        # 只遍历含至少一个 query 词的文档
        cand = set()
        qset = set(query)
        for i, tf in enumerate(self.tf):
            if any(t in tf for t in qset):
                cand.add(i)
        scored = [(i, self.score(query, i)) for i in cand]
        scored = [(i, s) for i, s in scored if s > 0]
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:top_k]

    def covered_segments(self, segs: list[list[str]], i: int, hit_fraction: float = 0.34) -> tuple[int, ...]:
        """第 i 篇覆盖了问题的哪些词组（下标）。一个词组若有 ≥ hit_fraction 的 token 出现在文中即算覆盖。"""
        if not segs or not self.docs:
            return ()
        tf = self.tf[i]
        out = []
        for k, grams in enumerate(segs):
            hits = sum(1 for g in grams if g in tf)
            if hits and hits / len(grams) >= hit_fraction:
                out.append(k)
        return tuple(out)

    def segment_coverage(self, segs: list[list[str]], i: int, hit_fraction: float = 0.34) -> float:
        """问题词组被第 i 篇覆盖的比例，0—1。"""
        if not segs:
            return 0.0
        return len(self.covered_segments(segs, i, hit_fraction)) / len(segs)

    ASCII_BOOST = 2.0  # 英文标识符/术语是精确匹配的实体，权重加倍

    def coverage(self, content_terms: list[str], i: int) -> float:
        """问题内容词（idf 加权）被第 i 篇覆盖的比例，0—1。

        语料中不存在的词同样计入分母（取语料最大 idf），因此问题里出现资料从未提过的词会压低覆盖率。
        """
        if not content_terms or not self.docs:
            return 0.0
        tf = self.tf[i]
        unseen_w = math.log(len(self.docs) + 1.0)
        total = 0.0
        hit = 0.0
        for t in content_terms:
            w = self.idf(t) if t in self.df else unseen_w
            if t.isascii() and len(t) >= 3:
                w *= self.ASCII_BOOST
            total += w
            if t in tf:
                hit += w
        return hit / total if total else 0.0
