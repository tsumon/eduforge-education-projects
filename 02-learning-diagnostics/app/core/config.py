"""评分标准（rubric）、课程进度（syllabus）、题库（exercise bank）的加载与校验。"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "config"

DEFAULT_RUBRIC = {
    "weights": {"homework": 0.4, "quiz": 0.4, "interaction": 0.2},
    "thresholds": {"weak": 60, "challenge": 80},
    "min_records_per_topic": 2,
    "class_priority": {"high_weak_rate": 0.4, "high_mastery": 60, "medium_weak_rate": 0.2, "medium_mastery": 70},
    "max_exercises_per_student": 4,
}


@dataclass
class Rubric:
    weights: dict[str, float]
    weak: float
    challenge: float
    min_records_per_topic: int
    class_priority: dict[str, float]
    max_exercises_per_student: int

    @classmethod
    def from_dict(cls, d: dict) -> "Rubric":
        if not isinstance(d, dict):
            raise ValueError("评分标准必须是 JSON 对象")
        merged = {**DEFAULT_RUBRIC, **(d or {})}
        if not isinstance(merged["weights"], dict) or set(merged["weights"]) != {"homework", "quiz", "interaction"}:
            raise ValueError("weights 必须包含 homework、quiz、interaction 三种考核类型")
        w = {k: _number(v, f"权重 {k}") for k, v in merged["weights"].items()}
        if any(v < 0 for v in w.values()) or abs(sum(w.values()) - 1.0) > 1e-6:
            raise ValueError("评分标准 weights 必须为非负且和为 1")
        th = merged["thresholds"]
        if not isinstance(th, dict) or not {"weak", "challenge"} <= th.keys():
            raise ValueError("thresholds 需包含 weak 和 challenge")
        weak, challenge = _number(th["weak"], "weak"), _number(th["challenge"], "challenge")
        if not (0 < weak < challenge <= 100):
            raise ValueError("阈值需满足 0 < weak < challenge ≤ 100")
        priority = merged["class_priority"]
        if not isinstance(priority, dict) or set(priority) != set(DEFAULT_RUBRIC["class_priority"]):
            raise ValueError("class_priority 需包含高、中优先级的薄弱比例和掌握度阈值")
        priority = {k: _number(v, k) for k, v in priority.items()}
        if not (0 <= priority["medium_weak_rate"] <= priority["high_weak_rate"] <= 1 and
                0 <= priority["high_mastery"] <= priority["medium_mastery"] <= 100):
            raise ValueError("补讲阈值比例应为 0—1、掌握度为 0—100，且高优先级条件更严格")
        return cls(
            weights=w, weak=weak, challenge=challenge,
            min_records_per_topic=_integer(merged["min_records_per_topic"], "min_records_per_topic", 1, 100),
            class_priority=priority,
            max_exercises_per_student=_integer(merged["max_exercises_per_student"], "max_exercises_per_student", 1, 20),
        )

    def to_dict(self) -> dict:
        return {
            "weights": self.weights, "thresholds": {"weak": self.weak, "challenge": self.challenge},
            "min_records_per_topic": self.min_records_per_topic, "class_priority": self.class_priority,
            "max_exercises_per_student": self.max_exercises_per_student,
        }


@dataclass
class Syllabus:
    class_name: str
    course: str
    taught_through_week: int
    topics: list[dict]  # [{topic, week}]

    @classmethod
    def from_dict(cls, d: dict) -> "Syllabus":
        if not isinstance(d, dict):
            raise ValueError("syllabus 必须是 JSON 对象")
        class_name = _text(d.get("class_name", "未命名班级"), "班级", 100)
        course = _text(d.get("course", "未命名课程"), "课程", 100)
        week = _integer(d.get("taught_through_week"), "已授课周次", 0, 100)
        topics = d.get("topics")
        if not isinstance(topics, list) or not 1 <= len(topics) <= 1000:
            raise ValueError("topics 必须是包含 1—1000 个知识点的数组")
        normalized, seen = [], set()
        for item in topics:
            if not isinstance(item, dict):
                raise ValueError("每个知识点必须是包含 topic 和 week 的对象")
            name = _text(item.get("topic"), "知识点", 100)
            if name in seen:
                raise ValueError(f"进度表知识点重复：{name}")
            seen.add(name)
            normalized.append({"topic": name, "week": _integer(item.get("week"), "知识点周次", 1, 100)})
        return cls(class_name, course, week, normalized)

    def topic_week(self, topic: str) -> int | None:
        for t in self.topics:
            if t["topic"] == topic:
                return int(t["week"])
        return None

    def is_taught(self, topic: str) -> bool | None:
        """True 已授课 / False 未授课 / None 不在进度表中。"""
        w = self.topic_week(topic)
        if w is None:
            return None
        return w <= self.taught_through_week

    def order(self) -> list[str]:
        return [t["topic"] for t in sorted(self.topics, key=lambda x: int(x["week"]))]

    def to_dict(self) -> dict:
        return {"class_name": self.class_name, "course": self.course,
                "taught_through_week": self.taught_through_week, "topics": self.topics}


@dataclass
class ExerciseBank:
    bank: dict[str, dict[str, list[dict]]] = field(default_factory=dict)

    def get(self, topic: str, level: str) -> list[dict]:
        return list(self.bank.get(topic, {}).get(level, []))

    def has_topic(self, topic: str) -> bool:
        return topic in self.bank


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _number(value, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} 必须是有限数字")
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} 必须是有限数字") from exc
    if not math.isfinite(value):
        raise ValueError(f"{label} 必须是有限数字")
    return value


def _integer(value, label: str, low: int, high: int) -> int:
    number = _number(value, label)
    if number != int(number) or not low <= number <= high:
        raise ValueError(f"{label} 必须是 {low}—{high} 的整数")
    return int(number)


def _text(value, label: str, limit: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
        raise ValueError(f"{label} 需为 1—{limit} 字文本")
    return value.strip()


def load_rubric(path: Path | None = None) -> Rubric:
    p = path or CONFIG_DIR / "rubric.json"
    return Rubric.from_dict(_load_json(p) if p.exists() else {})


def load_syllabus(path: Path | None = None) -> Syllabus:
    p = path or CONFIG_DIR / "syllabus.json"
    d = _load_json(p)
    return Syllabus.from_dict(d)


def load_exercise_bank(path: Path | None = None) -> ExerciseBank:
    p = path or CONFIG_DIR / "exercise_bank.json"
    return ExerciseBank(bank=_load_json(p) if p.exists() else {})
