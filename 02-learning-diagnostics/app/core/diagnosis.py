"""基于成绩、错因标签与教学进度的规则诊断；不推断学生心理或调用模型。"""
from __future__ import annotations

from collections import Counter, defaultdict
from .config import Rubric, Syllabus, ExerciseBank
from .schema import ParseResult, TYPE_LABELS
from .scoring import score_students, level_of

MAX_STUDENTS = 2_000
MAX_TOPICS = 200
MAX_MATRIX_CELLS = 100_000


def evidence_for(record) -> str:
    cause = f"错因标签：{record.error_tag}" if record.error_tag else "未标注错因，不能仅凭分数判断原因"
    when = record.date.isoformat() if record.date else "日期未确定"
    return (f"第 {record.row} 行 · {record.student_id} · {record.topic} · {record.type_label}「{record.item}」"
            f" · {record.score:g}/{record.max_score:g}（{record.ratio:.0%}） · {when} · {cause}"
            + (f"；备注：{record.note}" if record.note else ""))


def build_report(parsed: ParseResult, rubric: Rubric, syllabus: Syllabus, bank: ExerciseBank) -> dict:
    # 在分组评分和学生×知识点矩阵构建前限制规模；CSV行数本身不能约束矩阵大小。
    student_count = len({r.student_id for r in parsed.records})
    topic_names = syllabus.order() + sorted({r.topic for r in parsed.records} - set(syllabus.order()))
    if student_count > MAX_STUDENTS:
        raise ValueError(f"学生数量超过 {MAX_STUDENTS} 人上限，请按班级拆分 CSV。")
    if len(topic_names) > MAX_TOPICS:
        raise ValueError(f"知识点数量超过 {MAX_TOPICS} 个上限（含课程进度与 CSV 知识点），请按章节拆分。")
    if student_count * len(topic_names) > MAX_MATRIX_CELLS:
        raise ValueError(f"学生数 × 知识点数超过 {MAX_MATRIX_CELLS} 个诊断单元上限，请按班级或章节拆分后上传。")
    scored = score_students(parsed.records, rubric)
    names = {r.student_id: r.name for r in parsed.records}
    warnings = list(parsed.warnings)
    unknown = [t for t in topic_names if syllabus.is_taught(t) is None]
    if unknown:
        warnings.append("以下知识点未列入课程进度表，保留证据但不计入掌握度与练习：" + "、".join(unknown))
    future = [t for t in topic_names if syllabus.is_taught(t) is False]
    if future:
        warnings.append("尚未授课的知识点不判薄弱、不纳入均分和补讲：" + "、".join(future))
    if len(scored) < 3:
        warnings.append(f"当前仅有 {len(scored)} 名学生，班级结论只描述已上传样本，不能外推整个班级。")
    students = []
    for sid, scores in sorted(scored.items()):
        details, errors = [], []
        for topic in topic_names:
            score = scores.get(topic)
            taught = syllabus.is_taught(topic)
            status = "unknown_topic" if taught is None else ("not_taught" if not taught else "insufficient_data")
            weighted_count = sum(rubric.weights.get(r.assessment_type, 0) > 0 for r in score.records) if score else 0
            if taught and score and weighted_count >= rubric.min_records_per_topic:
                status = "assessed"
            records = score.records if score else []
            evidence = [evidence_for(r) for r in records if r.score < r.max_score][:5]
            if status == "assessed":
                missing = [TYPE_LABELS[t] for t in rubric.weights if rubric.weights[t] > 0 and t not in score.by_type]
                if missing:
                    evidence.append("缺少" + "、".join(missing) + "记录，已对实际考核类型重新归一权重。")
                errors.extend(evidence)
            details.append({"topic": topic, "mastery": score.mastery if status == "assessed" else None,
                            "status": status, "record_count": len(records),
                            "by_type": score.by_type if score else {}, "evidence": evidence})
        assessed = [t for t in details if t["status"] == "assessed"]
        mastery = round(sum(t["mastery"] for t in assessed) / len(assessed), 1) if assessed else None
        level = level_of(mastery, rubric) if mastery is not None else None
        weak = [t["topic"] for t in sorted(assessed, key=lambda t: t["mastery"]) if t["mastery"] < rubric.weak]
        exercises = []
        for t in sorted(assessed, key=lambda t: t["mastery"]):
            if len(exercises) >= rubric.max_exercises_per_student:
                break
            available = bank.get(t["topic"], level)
            if available:
                exercises.append({"topic": t["topic"], "level": level, **available[0]})
            else:
                warnings.append(f"{sid} 的「{t['topic']}」缺少对应分层题目，未编造练习。")
        if mastery is None:
                errors.append(f"数据不足：已授课知识点均未达到每人每知识点至少 {rubric.min_records_per_topic} 条正权重考核记录，暂不分层或分配练习。")
        elif not errors:
            errors.append("有效考核未发现失分记录；这不代表已验证全部应用情境。")
        students.append({"student_id": sid, "name": names[sid], "mastery": mastery, "level": level,
                         "status": "assessed" if assessed else "insufficient_data", "weak_topics": weak,
                         "evidence": errors[:12], "exercises": exercises, "topic_scores": details,
                         "record_count": sum(s.record_count for s in scores.values())})
    topics, reteaching = [], []
    for topic in topic_names:
        entries = [next(t for t in student["topic_scores"] if t["topic"] == topic) for student in students]
        assessed = [t for t in entries if t["status"] == "assessed"]
        mastery = round(sum(t["mastery"] for t in assessed) / len(assessed), 1) if assessed else None
        weak_count = sum(t["mastery"] < rubric.weak for t in assessed)
        weak_rate = weak_count / len(assessed) if assessed else None
        taught = syllabus.is_taught(topic)
        status = "unknown_topic" if taught is None else ("not_taught" if not taught else ("assessed" if assessed else "insufficient_data"))
        priority = "not_evaluated"
        if assessed:
            p = rubric.class_priority
            priority = "high" if weak_rate >= p["high_weak_rate"] or mastery < p["high_mastery"] else (
                "medium" if weak_rate >= p["medium_weak_rate"] or mastery < p["medium_mastery"] else "none")
        observed = [r for r in parsed.records if r.topic == topic]
        tags = Counter(r.error_tag for r in observed if r.error_tag and r.score < r.max_score)
        evidence = [evidence_for(r) for r in sorted(observed, key=lambda r: r.ratio) if r.ratio < 1][:5]
        topics.append({"topic": topic, "week": syllabus.topic_week(topic), "taught": taught, "status": status,
                       "mastery": mastery, "student_count": sum(bool(t["record_count"]) for t in entries),
                       "assessed_count": len(assessed), "weak_count": weak_count, "weak_rate": weak_rate,
                       "priority": priority, "evidence": evidence, "error_tags": dict(tags),
                       "record_count": len(observed)})
        if priority in ("high", "medium"):
            tag_text = "、".join(tag for tag, _ in tags.most_common(2))
            reteaching.append({"topic": topic, "priority": priority,
                "reason": f"已达到数据要求的 {len(assessed)} 人中，{weak_count} 人低于 {rubric.weak:g} 分（{weak_rate:.0%}），平均 {mastery:g} 分。"
                          + (f"已标注错因集中在：{tag_text}。" if tag_text else "没有错因标签，需先核对原题与作答。"),
                "suggestion": f"用「{topic}」的实际失分题复盘" + (f"{tag_text}，" if tag_text else "解题步骤，")
                              + "先示范一个最小例子，再安排同类变式与一次随堂复测；比较新旧证据后调整分层。"})
    reteaching.sort(key=lambda item: (item["priority"] != "high", topic_names.index(item["topic"])))
    evaluated = [s for s in students if s["mastery"] is not None]
    insufficient = sum(s["status"] == "insufficient_data" for s in students)
    if insufficient:
        warnings.append(f"{insufficient} 名学生数据不足；缺失记录不按零分处理，不纳入平均掌握度与薄弱人数。")
    return {
        "class_name": syllabus.class_name, "course": syllabus.course,
        "summary": {"student_count": len(students), "topic_count": len(topic_names),
            "average_mastery": round(sum(s["mastery"] for s in evaluated) / len(evaluated), 1) if evaluated else None,
            "at_risk_count": sum(s["mastery"] < rubric.weak for s in evaluated),
            "record_count": len(parsed.records), "assessed_student_count": len(evaluated),
            "insufficient_student_count": insufficient},
        "topics": topics, "students": students, "reteaching": reteaching,
        "warnings": list(dict.fromkeys(warnings)), "parse_stats": parsed.stats,
        "methodology": {"mode": "rule_based", "description": "按上传成绩与显式错因标签进行规则统计，不使用大模型，不推断心理或认知成因。",
            "formula": "同一考核类型内Σ得分/Σ满分，再按权重归一；个人与班级均分分别对可评估知识点、可评估学生等权平均。",
            "minimum_records": rubric.min_records_per_topic, "missing_data": "不足记录、未授课、进度外知识点均不按零分计入。",
            "scope": "只描述本次上传样本；分层是练习建议，不是固定能力标签。", "rubric": rubric.to_dict(), "syllabus": syllabus.to_dict()},
    }
