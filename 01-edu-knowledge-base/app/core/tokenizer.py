"""中英混合分词（无外部依赖，跨环境结果一致）。

策略：
- 中日韩文字按停用字切成连续片段，每个片段产出 **字 bigram**（主检索单元）与单字（弱信号）。
- ASCII 字母/数字连续串作为一个词（小写），长度 1 的纯字母忽略。
- 返回的 token 列表保留重复，供 BM25 计算词频；`content_tokens` 仅保留 bigram 与英文词，
  用于覆盖率判断（单字过于常见，不能作为"资料是否覆盖问题"的依据）。
"""
from __future__ import annotations

import re
from typing import Iterable

# 常见虚词/疑问词：在这些字处切断片段，使 bigram 不跨越它们。
# 注意：并/对/为/最/更/因/等/过/问/请 不作停用字——它们是 并发/对象/行为/最大/更新/原因/等价/超过/问题/请假 的组成部分。
STOP_CHARS = set(
    "的了是在和与及或吗呢啊吧呀么着将把被给于从以也都还就才又再很"
    "什怎如何哪些个这那它他她我你们其中之所而且但却此所如果虽然"
    "一二三四五六七八九十零几多少第"
)
STOP_WORDS = {
    "什么", "怎么", "如何", "哪些", "哪个", "是否", "可以", "能否", "请问", "介绍", "一下",
    "说明", "解释", "关于", "有关", "包括", "进行", "需要", "应该", "我们", "你们", "他们",
    "因为", "因此", "所以", "为什", "并且", "对于", "等等", "以及", "或者", "还是", "就是",
    "the", "a", "an", "of", "to", "in", "is", "are", "and", "or", "for", "on", "with",
}

_CJK = r"一-鿿㐀-䶿"
_TOKEN_RE = re.compile(rf"([{_CJK}]+)|([A-Za-z][A-Za-z0-9_\-\.]*|[0-9]+(?:\.[0-9]+)?)")


def _split_cjk(run: str) -> Iterable[str]:
    """把中文连续串在停用字处切开。"""
    buf = []
    for ch in run:
        if ch in STOP_CHARS:
            if buf:
                yield "".join(buf)
                buf = []
        else:
            buf.append(ch)
    if buf:
        yield "".join(buf)


def tokenize(text: str) -> list[str]:
    """全量 token（含单字），用于建索引和打分。"""
    tokens: list[str] = []
    for cjk, ascii_ in _TOKEN_RE.findall(text or ""):
        if cjk:
            for seg in _split_cjk(cjk):
                if len(seg) == 1:
                    tokens.append(seg)
                    continue
                tokens.extend(seg[i : i + 2] for i in range(len(seg) - 1))
                tokens.extend(seg)
        elif ascii_:
            w = ascii_.lower().strip(".")
            if len(w) <= 1 and w.isalpha():
                continue
            if w and w not in STOP_WORDS:
                tokens.append(w)
    return [t for t in tokens if t not in STOP_WORDS]


def segments(text: str) -> list[list[str]]:
    """把问题切成"词组"：中文按停用字切开的连续串、独立的英文/数字词，各自展开为 bigram/词列表。

    用于词组级覆盖判断：问题里的每个词组都应有一部分出现在证据片段中，
    否则说明问题的主语/实体（如"量子计算""Java"）在资料里不存在。
    只含单字的词组被忽略。
    """
    out: list[list[str]] = []
    for cjk, ascii_ in _TOKEN_RE.findall(text or ""):
        if cjk:
            for seg in _split_cjk(cjk):
                if len(seg) < 2:
                    continue
                grams = [seg[i : i + 2] for i in range(len(seg) - 1)]
                grams = [g for g in grams if g not in STOP_WORDS]
                if grams:
                    out.append(grams)
        elif ascii_:
            w = ascii_.lower().strip(".")
            if len(w) >= 2 and w not in STOP_WORDS:
                out.append([w])
    return out


def content_tokens(text: str) -> list[str]:
    """只保留 bigram 与英文/数字词，去重保序。用于覆盖率与句子打分。"""
    seen = set()
    out = []
    for t in tokenize(text):
        if len(t) < 2:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


_SENT_RE = re.compile(r"(?<=[。！？!?])\s*|\n+")


def split_sentences(text: str) -> list[str]:
    """按中文/英文句末标点与换行切句，去掉空白。"""
    parts = [p.strip() for p in _SENT_RE.split(text or "")]
    return [p for p in parts if p]
