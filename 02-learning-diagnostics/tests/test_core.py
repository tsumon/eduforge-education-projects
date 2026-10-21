import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import Rubric, Syllabus, ExerciseBank, load_syllabus
from app.core.schema import parse_csv, SchemaError
from app.core.scoring import weighted_mastery

HEADER = "学号,姓名,知识点,考核类型,题目,得分,满分,日期,错因,备注\n"


def csv_rows(*rows):
    return HEADER + "\n".join(rows)


class InputTests(unittest.TestCase):
    def test_non_finite_scores_are_skipped(self):
        for bad in ("NaN", "inf", "-inf", "1e309"):
            with self.subTest(bad=bad):
                parsed = parse_csv(csv_rows(
                    f"1,甲,循环,作业,A,{bad},10,2026-10-01,,",
                    "2,乙,循环,作业,B,8,10,2026-10-01,,"))
                self.assertEqual([r.student_id for r in parsed.records], ["2"])
                self.assertTrue(parsed.warnings)

    def test_non_finite_and_extreme_maximum_are_skipped(self):
        for bad in ("NaN", "inf", "1e308"):
            with self.subTest(bad=bad):
                parsed = parse_csv(csv_rows(
                    f"1,甲,循环,作业,A,5,{bad},2026-10-01,,",
                    "2,乙,循环,作业,B,8,10,2026-10-01,,"))
                self.assertEqual(len(parsed.records), 1)

    def test_missing_headers_and_all_invalid_data_are_rejected(self):
        with self.assertRaises(SchemaError):
            parse_csv("学号,得分\n1,3")
        with self.assertRaises(SchemaError):
            parse_csv(csv_rows("1,甲,循环,作业,A,-1,10,2026-10-01,,"))

    def test_duplicates_keep_last_value_and_dates_warn(self):
        parsed = parse_csv(csv_rows("1,甲,循环,作业,A,2,10,2026-10-01,,",
                                    "1,甲,循环,作业,A,8,10,2026-10-01,,",
                                    "1,甲,循环,随堂测,B,6,10,坏日期,,"))
        self.assertEqual(len(parsed.records), 2)
        self.assertEqual(parsed.records[0].score, 8)
        self.assertIsNone(parsed.records[1].date)
        self.assertEqual(parsed.stats["duplicates"], 1)

    def test_evidence_rows_use_physical_csv_line_numbers(self):
        parsed = parse_csv(csv_rows('1,甲,循环,作业,"题干第一行\n题干第二行",8,10,2026-10-01,,',
                                    '2,乙,循环,作业,B,6,10,2026-10-01,,'))
        self.assertEqual([record.row for record in parsed.records], [2, 4])

    def test_weighting_uses_points_within_type_and_renormalizes(self):
        records = parse_csv(csv_rows("1,甲,循环,作业,A,2,2,2026-10-01,,",
                                    "1,甲,循环,作业,B,0,18,2026-10-02,,",
                                    "1,甲,循环,随堂测,C,8,10,2026-10-03,,")).records
        score, breakdown = weighted_mastery(records, Rubric.from_dict({}))
        self.assertEqual(score, 45.0)
        self.assertEqual(breakdown["homework"]["ratio"], .1)

    def test_rubric_rejects_non_finite_unknown_and_invalid_limits(self):
        invalid = [
            {"weights": {"homework": math.nan, "quiz": .5, "interaction": .5}},
            {"weights": {"unknown": 1}},
            {"min_records_per_topic": 0},
            {"max_exercises_per_student": -1},
            {"thresholds": {"weak": 90, "challenge": 80}},
        ]
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                Rubric.from_dict(config)

    def test_syllabus_rejects_duplicate_or_invalid_weeks(self):
        for topics in ([{"topic": "循环", "week": 0}],
                       [{"topic": "循环", "week": 1}, {"topic": "循环", "week": 2}]):
            with self.subTest(topics=topics), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "syllabus.json"
                path.write_text(json.dumps({"class_name": "A", "course": "Python", "taught_through_week": 2, "topics": topics}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_syllabus(path)


class DiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.rubric = Rubric.from_dict({})
        self.syllabus = Syllabus("A班", "Python", 2, [{"topic": "循环", "week": 1}, {"topic": "函数", "week": 2}, {"topic": "异常", "week": 3}])
        self.bank = ExerciseBank({"循环": {level: [{"question": "题干", "answer": "答案", "hint": "提示"}] for level in ("foundation", "consolidation", "challenge")}})

    def report(self, *rows):
        from app.core.diagnosis import build_report
        return build_report(parse_csv(csv_rows(*rows)), self.rubric, self.syllabus, self.bank)

    def test_uncovered_and_untaught_topics_are_not_zero_or_weak(self):
        report = self.report("1,甲,循环,作业,A,4,10,2026-10-01,边界条件,",
                             "1,甲,循环,随堂测,B,4,10,2026-10-02,边界条件,",
                             "1,甲,异常,作业,C,0,10,2026-10-03,,",
                             "1,甲,未知知识点,随堂测,D,0,10,2026-10-03,,")
        topics = {t["topic"]: t for t in report["topics"]}
        self.assertEqual(report["students"][0]["mastery"], 40)
        self.assertEqual(topics["函数"]["status"], "insufficient_data")
        self.assertIsNone(topics["函数"]["mastery"])
        self.assertEqual(topics["异常"]["status"], "not_taught")
        self.assertIsNone(topics["异常"]["mastery"])
        self.assertEqual(topics["未知知识点"]["status"], "unknown_topic")
        self.assertEqual(report["students"][0]["weak_topics"], ["循环"])
        self.assertEqual([r["topic"] for r in report["reteaching"]], ["循环"])
        self.assertTrue(any("第 2 行" in e and "边界条件" in e for e in report["students"][0]["evidence"]))

    def test_insufficient_student_is_not_labelled_or_assigned_practice(self):
        report = self.report("1,甲,循环,作业,A,0,10,2026-10-01,,")
        student = report["students"][0]
        self.assertIsNone(student["mastery"])
        self.assertIsNone(student["level"])
        self.assertEqual(student["status"], "insufficient_data")
        self.assertEqual(student["exercises"], [])
        self.assertIsNone(report["summary"]["average_mastery"])
        self.assertEqual(report["summary"]["at_risk_count"], 0)

    def test_three_practice_levels_include_answers_and_hints(self):
        rows = []
        for sid, score in (("1", 4), ("2", 7), ("3", 9)):
            rows.extend([f"{sid},学生{sid},循环,作业,A,{score},10,2026-10-01,,",
                         f"{sid},学生{sid},循环,随堂测,B,{score},10,2026-10-02,,"])
        report = self.report(*rows)
        students = {s["student_id"]: s for s in report["students"]}
        for sid, level in (("1", "foundation"), ("2", "consolidation"), ("3", "challenge")):
            self.assertEqual(students[sid]["level"], level)
            self.assertEqual(students[sid]["exercises"][0]["level"], level)
            self.assertTrue(students[sid]["exercises"][0]["answer"])
            self.assertTrue(students[sid]["exercises"][0]["hint"])

    def test_missing_error_tags_are_not_invented_as_cognitive_causes(self):
        report = self.report("1,甲,循环,作业,A,4,10,2026-10-01,,",
                             "1,甲,循环,随堂测,B,4,10,2026-10-02,,")
        self.assertTrue(any("未标注错因" in e for e in report["students"][0]["evidence"]))

    def test_zero_weight_records_do_not_fill_evidence_minimum(self):
        self.rubric = Rubric.from_dict({"weights": {"homework": 1, "quiz": 0, "interaction": 0}})
        report = self.report("1,甲,循环,作业,A,4,10,2026-10-01,,",
                             "1,甲,循环,课堂互动,B,10,10,2026-10-02,,",
                             "1,甲,循环,课堂互动,C,10,10,2026-10-03,,")
        self.assertIsNone(report["students"][0]["mastery"])

    def test_unknown_exercise_topics_are_not_filled_with_invented_questions(self):
        self.bank = ExerciseBank({})
        report = self.report("1,甲,循环,作业,A,4,10,2026-10-01,,",
                             "1,甲,循环,随堂测,B,4,10,2026-10-02,,")
        self.assertEqual(report["students"][0]["exercises"], [])
        self.assertTrue(any("未编造练习" in warning for warning in report["warnings"]))

    def test_student_topic_matrix_limit_is_checked_before_scoring(self):
        with patch("app.core.diagnosis.MAX_MATRIX_CELLS", 4, create=True), \
             patch("app.core.diagnosis.score_students", side_effect=AssertionError("不应在规模校验前开始构建")):
            with self.assertRaisesRegex(ValueError, "学生.*知识点"):
                self.report("1,甲,循环,作业,A,4,10,2026-10-01,,",
                            "2,乙,循环,作业,A,6,10,2026-10-01,,")


if __name__ == "__main__":
    unittest.main()
