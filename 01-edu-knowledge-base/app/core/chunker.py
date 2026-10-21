"""文档分片：按标题/段落/句子打包到目标长度，标题作为上下文前缀，相邻分片重叠一句。"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .tokenizer import split_sentences

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s*(.+?)\s*#*\s*$")


@dataclass
class Chunk:
    ord: int
    text: str
    heading: str = ""


def _raw_blocks(text: str):
    heading = ""
    para: list[str] = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        m = _HEADING_RE.match(line)
        if m:
            if para:
                yield heading, "\n".join(para)
                para = []
            heading = m.group(2).strip()
            continue
        if not line.strip():
            if para:
                yield heading, "\n".join(para)
                para = []
            continue
        para.append(line.strip())
    if para:
        yield heading, "\n".join(para)


def _is_blockquote(para: str) -> bool:
    return all(line.startswith(">") for line in para.split("\n"))


def _blocks(text: str):
    """把全文切成 (heading, paragraph) 序列。

    位于正文之前的引用块（`> ...`，通常是声明/备注）不参与分片，避免被当作证据引用；
    若整篇只有引用块则原样保留。
    """
    blocks = list(_raw_blocks(text))
    first_body = next((i for i, (_, p) in enumerate(blocks) if not _is_blockquote(p)), None)
    if first_body is None:
        yield from blocks
        return
    for i, (h, p) in enumerate(blocks):
        if i < first_body and _is_blockquote(p):
            continue
        yield h, p


def chunk_text(text: str, target: int = 320, max_len: int = 480, overlap_sentences: int = 1) -> list[Chunk]:
    """返回分片列表。

    - 段落优先：段落 ≤ max_len 时整段入片，多个短段落合并到接近 target。
    - 超长段落按句子切分再打包，相邻片重叠 `overlap_sentences` 句。
    - 标题变化时强制换片，标题写入 Chunk.heading（检索时拼接进文本，便于命中章节名）。
    """
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_len = 0
    cur_heading = ""

    def flush():
        nonlocal buf, buf_len
        if buf:
            chunks.append(Chunk(ord=len(chunks), text="\n".join(buf).strip(), heading=cur_heading))
        buf, buf_len = [], 0

    for heading, para in _blocks(text):
        if heading != cur_heading:
            flush()
            cur_heading = heading
        if len(para) <= max_len:
            if buf_len + len(para) > target and buf:
                flush()
            buf.append(para)
            buf_len += len(para)
            continue
        # 超长段落：句子级打包
        flush()
        sents = split_sentences(para)
        window: list[str] = []
        wlen = 0
        for s in sents:
            if wlen + len(s) > target and window:
                chunks.append(Chunk(ord=len(chunks), text="".join(window), heading=cur_heading))
                tail = window[-overlap_sentences:] if overlap_sentences else []
                window = list(tail)
                wlen = sum(len(x) for x in window)
            window.append(s)
            wlen += len(s)
        if window:
            chunks.append(Chunk(ord=len(chunks), text="".join(window), heading=cur_heading))
    flush()
    # 过滤空片并重编号
    out = [c for c in chunks if c.text]
    for i, c in enumerate(out):
        c.ord = i
    return out


def chunk_index_text(chunk: Chunk) -> str:
    """用于建索引的文本：标题 + 正文。"""
    return f"{chunk.heading}\n{chunk.text}" if chunk.heading else chunk.text
