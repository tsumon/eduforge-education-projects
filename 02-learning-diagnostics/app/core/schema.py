"""CSV 解析与校验。

输入列（中英文表头均可）：
    学号 student_id | 姓名 name | 知识点 topic | 考核类型 assessment_type | 题目 item
    得分 score | 满分 max_score | 日期 date | 错因 error_tag（可选） | 备注 note（可选）
考核类型：作业 homework / 随堂测 quiz / 课堂互动 interaction。

校验规则（每条都会写进 warnings，便于老师回头修数据）：
- 缺少必需列 → 整体拒绝；
- 学号/知识点/考核类型为空、得分或满分为空或非数字、满分 ≤ 0、得分越界（<0 或 >满分）→ 跳过该行；
- 日期格式错误 → 保留该行但日期置空；
- 重复记录（学号+知识点+类型+题目+日期相同）→ 保留最后一条；
- 未知考核类型 → 跳过该行。
"""
from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field
from datetime import date

REQUIRED = ("student_id", "name", "topic", "assessment_type", "item", "score", "max_score", "date")
OPTIONAL = ("error_tag", "note")

HEADER_ALIASES = {
    "学号": "student_id", "student_id": "student_id", "id": "student_id",
    "姓名": "name", "name": "name", "学生": "name",
    "知识点": "topic", "topic": "topic", "章节": "topic",
    "考核类型": "assessment_type", "类型": "assessment_type", "assessment_type": "assessment_type", "type": "assessment_type",
    "题目": "item", "题号": "item", "item": "item",
    "得分": "score", "分数": "score", "score": "score",
    "满分": "max_score", "总分": "max_score", "max_score": "max_score", "max": "max_score",
    "日期": "date", "date": "date", "时间": "date",
    "错因": "error_tag", "错误类型": "error_tag", "error_tag": "error_tag",
    "备注": "note", "note": "note",
}

TYPE_ALIASES = {
    "作业": "homework", "课后作业": "homework", "homework": "homework", "hw": "homework",
    "随堂测": "quiz", "随堂测验": "quiz", "测验": "quiz", "小测": "quiz", "quiz": "quiz",
    "课堂互动": "interaction", "互动": "interaction", "课堂": "interaction", "interaction": "interaction",
}
TYPE_LABELS = {"homework": "作业", "quiz": "随堂测", "interaction": "课堂互动"}

MAX_ROWS = 50_000
MAX_POINTS = 1_000_000_000


class SchemaError(ValueError):
    """表头级错误：无法继续解析。"""


@dataclass
class Record:
    row: int
    student_id: str
    name: str
    topic: str
    assessment_type: str
    item: str
    score: float
    max_score: float
    date: date | None
    error_tag: str = ""
    note: str = ""

    @property
    def ratio(self) -> float:
        return self.score / self.max_score if self.max_score else 0.0

    @property
    def type_label(self) -> str:
        return TYPE_LABELS.get(self.assessment_type, self.assessment_type)


@dataclass
class ParseResult:
    records: list[Record]
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise SchemaError("无法识别文件编码（支持 UTF-8 / GB18030 / UTF-16）")


def _norm_header(h: str) -> str | None:
    key = (h or "").strip().strip("﻿").lower()
    return HEADER_ALIASES.get(key) or HEADER_ALIASES.get((h or "").strip())


def _parse_date(s: str) -> date | None:
    s = (s or "").strip().replace("/", "-").replace(".", "-")
    if not s:
        return None
    parts = s.split("-")
    if len(parts) != 3:
        raise ValueError
    y, m, d = (int(p) for p in parts)
    if y < 100:
        y += 2000
    return date(y, m, d)


def _to_float(s: str) -> float | None:
    s = (s or "").strip().replace("，", "").replace(",", "")
    if s == "":
        return None
    value = float(s)
    if not math.isfinite(value):
        raise ValueError("分值必须是有限数字")
    return value


def parse_csv(raw: bytes | str) -> ParseResult:
    text = decode_bytes(raw) if isinstance(raw, (bytes, bytearray)) else raw
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise SchemaError("CSV 文件为空")
    reader = csv.reader(io.StringIO(text))
    try:
        header_raw = next(reader)
    except StopIteration:
        raise SchemaError("CSV 文件为空")
    mapping: dict[int, str] = {}
    unknown: list[str] = []
    for i, h in enumerate(header_raw):
        n = _norm_header(h)
        if n and n not in mapping.values():
            mapping[i] = n
        elif h.strip():
            unknown.append(h.strip())
    missing = [c for c in REQUIRED if c not in mapping.values()]
    if missing:
        zh = {"student_id": "学号", "name": "姓名", "topic": "知识点", "assessment_type": "考核类型", "item": "题目",
              "score": "得分", "max_score": "满分", "date": "日期"}
        raise SchemaError("缺少必需列：" + "、".join(f"{zh[c]}（{c}）" for c in missing))

    warnings: list[str] = []
    if unknown:
        warnings.append(f"忽略了未识别的列：{'、'.join(unknown)}")

    records: dict[tuple, Record] = {}
    skipped = {"空值": 0, "非数字": 0, "满分无效": 0, "得分越界": 0, "考核类型未知": 0, "日期格式错误": 0}
    dup = 0
    total = 0
    next_row = reader.line_num + 1
    for row in reader:
        row_no = next_row
        next_row = reader.line_num + 1
        if not any(c.strip() for c in row):
            continue
        total += 1
        if total > MAX_ROWS:
            raise SchemaError(f"行数超过上限 {MAX_ROWS}")
        cells = {mapping[i]: (row[i] if i < len(row) else "") for i in mapping}
        sid = cells.get("student_id", "").strip()
        topic = cells.get("topic", "").strip()
        type_raw = cells.get("assessment_type", "").strip()
        if not sid or not topic or not type_raw or cells.get("score", "").strip() == "" or cells.get("max_score", "").strip() == "":
            skipped["空值"] += 1
            warnings.append(f"第 {row_no} 行：学号/知识点/考核类型/得分/满分存在空值，已跳过")
            continue
        atype = TYPE_ALIASES.get(type_raw.lower()) or TYPE_ALIASES.get(type_raw)
        if not atype:
            skipped["考核类型未知"] += 1
            warnings.append(f"第 {row_no} 行：考核类型「{type_raw}」无法识别（支持 作业/随堂测/课堂互动），已跳过")
            continue
        try:
            score = _to_float(cells.get("score"))
            max_score = _to_float(cells.get("max_score"))
        except ValueError:
            skipped["非数字"] += 1
            warnings.append(f"第 {row_no} 行：得分或满分不是数字，已跳过")
            continue
        if max_score is None or max_score <= 0 or max_score > MAX_POINTS:
            skipped["满分无效"] += 1
            warnings.append(f"第 {row_no} 行：满分必须大于 0 且不超过 {MAX_POINTS}，已跳过")
            continue
        if score is None or score < 0 or score > max_score:
            skipped["得分越界"] += 1
            warnings.append(f"第 {row_no} 行：得分 {cells.get('score', '').strip()} 超出 0—{max_score:g} 范围，已跳过")
            continue
        try:
            d = _parse_date(cells.get("date", ""))
        except ValueError:
            d = None
            skipped["日期格式错误"] += 1
            warnings.append(f"第 {row_no} 行：日期「{cells.get('date', '').strip()}」格式错误（应为 YYYY-MM-DD），已保留记录但日期置空")
        name = cells.get("name", "").strip() or sid
        item = cells.get("item", "").strip() or "-"
        rec = Record(
            row=row_no, student_id=sid, name=name, topic=topic, assessment_type=atype, item=item,
            score=score, max_score=max_score, date=d,
            error_tag=cells.get("error_tag", "").strip(), note=cells.get("note", "").strip(),
        )
        key = (sid, topic, atype, item, d)
        if key in records:
            dup += 1
        records[key] = rec
    if dup:
        warnings.append(f"发现 {dup} 条重复记录（学号+知识点+类型+题目+日期相同），已保留最后一条")
    result = ParseResult(records=list(records.values()), warnings=warnings)
    result.stats = {"rows": total, "valid": len(result.records), "duplicates": dup, "skipped": {k: v for k, v in skipped.items() if v}}
    if not result.records:
        raise SchemaError("没有任何有效记录：" + "；".join(warnings[-5:]))
    return result
