"""加权掌握度。

学生在某知识点的掌握度：
    ratio_t = Σ得分 / Σ满分（同一考核类型 t 内按分值汇总，不是按题目平均，避免 2 分题和 20 分题同权）
    mastery = Σ_t w_t · ratio_t / Σ_t w_t   （只对该学生该知识点实际有记录的类型求和，权重重新归一）
    → 0—100
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .config import Rubric
from .schema import Record


@dataclass
class TopicScore:
    student_id: str
    topic: str
    mastery: float
    records: list[Record] = field(default_factory=list)
    by_type: dict[str, dict] = field(default_factory=dict)  # type -> {score, max, ratio, n}

    @property
    def record_count(self) -> int:
        return len(self.records)


def weighted_mastery(records: list[Record], rubric: Rubric) -> tuple[float, dict[str, dict]]:
    agg: dict[str, dict] = defaultdict(lambda: {"score": 0.0, "max": 0.0, "n": 0})
    for r in records:
        a = agg[r.assessment_type]
        a["score"] += r.score
        a["max"] += r.max_score
        a["n"] += 1
    num = 0.0
    den = 0.0
    for t, a in agg.items():
        a["ratio"] = a["score"] / a["max"] if a["max"] else 0.0
        w = rubric.weights.get(t, 0.0)
        num += w * a["ratio"]
        den += w
    mastery = (num / den * 100.0) if den > 0 else 0.0
    return round(mastery, 1), dict(agg)


def score_students(records: list[Record], rubric: Rubric) -> dict[str, dict[str, TopicScore]]:
    """→ {student_id: {topic: TopicScore}}"""
    grouped: dict[tuple[str, str], list[Record]] = defaultdict(list)
    for r in records:
        grouped[(r.student_id, r.topic)].append(r)
    out: dict[str, dict[str, TopicScore]] = defaultdict(dict)
    for (sid, topic), recs in grouped.items():
        m, by_type = weighted_mastery(recs, rubric)
        out[sid][topic] = TopicScore(student_id=sid, topic=topic, mastery=m, records=sorted(recs, key=lambda r: (r.date or _MIN_DATE, r.row)), by_type=by_type)
    return dict(out)


def level_of(mastery: float, rubric: Rubric) -> str:
    if mastery < rubric.weak:
        return "foundation"
    if mastery < rubric.challenge:
        return "consolidation"
    return "challenge"


LEVEL_LABELS = {"foundation": "基础巩固", "consolidation": "强化提升", "challenge": "拓展挑战"}

from datetime import date as _date  # noqa: E402

_MIN_DATE = _date(1970, 1, 1)
