"""证据选择、拒答判定与抽取式回答拼装。

所有阈值集中在 `AnswerPolicy`，并随每次回答写入 diagnostics，保证"为什么答/为什么拒"可追溯。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, replace

from .tokenizer import content_tokens, split_sentences, tokenize

REFUSAL_PREFIX = "现有资料不足以回答该问题。"


@dataclass(frozen=True)
class AnswerPolicy:
    min_coverage: float = 0.34          # 问题内容词（idf 加权）被最佳分片覆盖的最低比例
    min_segment_coverage: float = 0.5   # 问题词组被最佳分片覆盖的最低比例（词组 = 按虚词切开的连续串/英文词）
    segment_hit_fraction: float = 0.34  # 词组内 ≥ 该比例的字对出现即视为该词组被覆盖
    min_score: float = 1.5              # 最佳分片 BM25 最低分（绝对阈）
    relative_floor: float = 0.45        # 其余分片得分 ≥ 最佳分 × 该比例 才纳入证据
    max_citations: int = 3
    max_sentences_per_chunk: int = 2
    min_question_terms: int = 1         # 问题至少要有 1 个内容词
    semantic_min_score: float = 0.50    # 余弦召回门槛，需按所接 Embedding 模型校准


@dataclass
class Evidence:
    chunk_id: str
    document_id: str
    title: str
    heading: str
    text: str
    score: float
    coverage: float
    segment_coverage: float = 1.0
    covered_segments: tuple[int, ...] = ()
    semantic_score: float | None = None
    lexical_score: float = 0.0


def select_evidence(ranked: list[Evidence], policy: AnswerPolicy) -> tuple[list[Evidence], dict]:
    """从 BM25 候选中选出证据，并返回判定依据。

    两步：
    1. 词法重排：在得分不低于 最佳分×relative_floor 的候选内，按 (词组覆盖率, BM25) 重排。
       BM25 偏爱高频重复，重排让"把问题各部分都提到"的分片排到前面。
    2. 门控：重排后的首位必须同时满足词组覆盖率、内容词覆盖率、绝对分三个阈值，否则拒答。
    3. 次要证据：只保留覆盖了主证据全部词组的分片（防止"同一门课的其他段落"混进引用）。
    """
    diag = {
        "policy": asdict(policy),
        "candidates": [
            {"chunk_id": e.chunk_id, "title": e.title, "score": round(e.score, 3),
             "coverage": round(e.coverage, 3), "segment_coverage": round(e.segment_coverage, 3)}
            for e in ranked[:5]
        ],
    }
    if not ranked:
        diag["reason"] = "no_candidates"
        return [], diag
    best_score = ranked[0].score
    floor = best_score * policy.relative_floor
    pool = [e for e in ranked if e.score >= floor]
    pool.sort(key=lambda e: (-round(e.segment_coverage, 2), -e.score))
    top = pool[0]
    diag["top_score"] = round(top.score, 3)
    diag["top_coverage"] = round(top.coverage, 3)
    diag["top_segment_coverage"] = round(top.segment_coverage, 3)
    diag["reranked_top"] = top.chunk_id
    if top.segment_coverage < policy.min_segment_coverage:
        diag["reason"] = "segment_coverage_below_threshold"
        return [], diag
    if top.coverage < policy.min_coverage:
        diag["reason"] = "coverage_below_threshold"
        return [], diag
    if top.score < policy.min_score:
        diag["reason"] = "score_below_threshold"
        return [], diag
    # 次要证据必须覆盖主证据覆盖到的每个词组，否则只是"提到了同一门课"，不算证据
    must = set(top.covered_segments)
    chosen = [top] + [e for e in pool[1:] if set(e.covered_segments) >= must]
    chosen = chosen[: policy.max_citations]
    diag["reason"] = "answered"
    diag["selected"] = [e.chunk_id for e in chosen]
    return chosen, diag


def pick_sentences(question: str, text: str, limit: int) -> list[str]:
    """从分片中挑与问题最相关的若干句，保持原文顺序；没有任何重叠时取前 limit 句。"""
    q_terms = set(content_tokens(question))
    sents = split_sentences(text)
    if not sents:
        return [text.strip()] if text.strip() else []
    scored = []
    for i, s in enumerate(sents):
        s_terms = set(tokenize(s))
        overlap = len(q_terms & s_terms)
        scored.append((overlap, -i, i, s))
    scored.sort(reverse=True)
    picked = [t for t in scored[:limit] if t[0] > 0]
    if not picked:
        picked = scored[:limit]
    picked.sort(key=lambda t: t[2])
    return [t[3] for t in picked]


def select_hybrid_evidence(ranked: list[Evidence], policy: AnswerPolicy) -> tuple[list[Evidence], dict]:
    """语义模式按余弦阈值门控；BM25 融合分只决定候选次序。"""
    diag = {"policy": asdict(policy), "candidates": [
        {"chunk_id": e.chunk_id, "title": e.title, "score": round(e.score, 6),
         "semantic_score": round(e.semantic_score, 4) if e.semantic_score is not None else None,
         "lexical_score": round(e.lexical_score, 3), "coverage": round(e.coverage, 3)}
        for e in ranked[:10]
    ]}
    pool = [e for e in ranked if e.semantic_score is not None and e.semantic_score >= policy.semantic_min_score]
    if not pool:
        diag["reason"] = "semantic_score_below_threshold" if ranked else "no_candidates"
        return [], diag
    lexical = sorted([replace(e, score=e.lexical_score) for e in ranked if e.lexical_score > 0], key=lambda e: -e.score)
    exact, _ = select_evidence(lexical, policy)
    by_id = {e.chunk_id: e for e in pool}
    chosen = [by_id[e.chunk_id] for e in exact if e.chunk_id in by_id]
    if chosen:
        diag["evidence_basis"] = "lexical_and_semantic"
    else:
        pool.sort(key=lambda e: (-e.semantic_score, -e.score))
        chosen = [e for e in pool if e.semantic_score >= pool[0].semantic_score - 0.03][:policy.max_citations]
        diag["evidence_basis"] = "semantic"
    diag.update(reason="answered", selected=[e.chunk_id for e in chosen], top_semantic_score=round(chosen[0].semantic_score, 4))
    return chosen, diag


def format_source(title: str, heading: str = "") -> str:
    """《标题》·章节；标题自带书名号时不重复加；章节与标题相同则省略。"""
    t = title if title.startswith("《") else f"《{title}》"
    if heading and heading != title:
        return f"{t}·{heading}"
    return t


def excerpt_for(question: str, e: Evidence, policy: AnswerPolicy) -> str:
    """证据分片中与问题最相关的 1—2 句（供回答正文与前端高亮共用）。"""
    sents = pick_sentences(question, e.text, policy.max_sentences_per_chunk)
    return "".join(sents) if sents else e.text


def compose_extractive(question: str, evidence: list[Evidence], policy: AnswerPolicy) -> str:
    """把证据句拼成带 [n] 标注的回答。出处放在引用列表里，不在正文重复。"""
    lines = [f"{n}. {excerpt_for(question, e, policy)} [{n}]" for n, e in enumerate(evidence, 1)]
    lines.append("")
    lines.append("以上为资料原句抽取（模式 extractive），未经模型改写；[n] 对应引用列表。")
    return "\n".join(lines)


def compose_refusal(question: str, ranked: list[Evidence], diag: dict, policy: AnswerPolicy) -> str:
    reason = diag.get("reason")
    if reason == "semantic_score_below_threshold":
        detail = f"语义检索没有找到相似度达到 {policy.semantic_min_score:.0%} 门槛的资料片段。"
    elif reason == "no_candidates" or not ranked:
        detail = "知识库中没有任何分片与问题的关键词匹配。"
    else:
        top = next((e for e in ranked if e.chunk_id == diag.get("reranked_top")), ranked[0])
        src = format_source(top.title, top.heading)
        if reason == "segment_coverage_below_threshold":
            detail = (
                f"最接近的资料是{src}，但问题中只有 {top.segment_coverage:.0%} 的词组能在其中找到"
                f"（阈值 {policy.min_segment_coverage:.0%}），说明问题涉及的对象在资料中没有出现。"
            )
        else:
            detail = (
                f"最接近的资料是{src}，但对问题关键词的覆盖率仅 {top.coverage:.0%}"
                f"（阈值 {policy.min_coverage:.0%}），匹配分 {top.score:.2f}（阈值 {policy.min_score}）。"
            )
    return (
        f"{REFUSAL_PREFIX}{detail}"
        "为避免编造，本系统不作回答。建议：导入与该问题直接相关的课程资料后重试，或换用资料中出现的术语提问。"
    )


def is_refusal(answer: str) -> bool:
    return (answer or "").startswith(REFUSAL_PREFIX)
