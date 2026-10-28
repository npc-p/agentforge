"""文本切分。

策略：结构优先 + 长度兜底。
1. 先按 Markdown 标题 / 空行段落切成语义块；
2. 超长块再按句子（中英文标点）递归切分；
3. 相邻块之间保留 overlap，避免答案被切断在边界上。

切分质量直接决定召回上限——这是 RAG 里最容易被低估的一环。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])|(?<=\.\s)")
_HEADING = re.compile(r"^#{1,6}\s")
_SENTENCE_END = re.compile(r"[。！？；;.!?]\s*$")


def _looks_like_heading(block: str, min_chars: int) -> bool:
    """判断这一段是不是标题（Markdown 标题，或没有 # 前缀的小节标题）。

    对无 `#` 的标题判据要保守：**短**且**不以句末标点结尾**。
    这样「一、权重配置」算标题，而「参数不要乱改。」不算。

    为什么必须区分：标题要并入**后**一段，短段落要并入**前**一段。
    两者用同一条规则会导致「一、权重配置」挂到上一段末尾，
    标题与它引出的正文被拆到两个切片里。
    """
    if _HEADING.match(block):
        return True
    return len(block) < min_chars and not _SENTENCE_END.search(block)


@dataclass(slots=True)
class TextChunk:
    seq: int
    content: str
    char_count: int

    @property
    def tokens_estimate(self) -> int:
        from app.core.text import estimate_tokens

        return estimate_tokens(self.content)


def _split_paragraphs(text: str) -> list[str]:
    blocks: list[str] = []
    for raw in _PARAGRAPH_SPLIT.split(text):
        block = raw.strip()
        if block:
            blocks.append(block)
    return blocks


def _split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text) if p and p.strip()]
    return parts or [text]


def _merge_headings(blocks: list[str], min_chars: int) -> list[str]:
    """把标题并入其后的正文块，而不是让它单独成块。

    标题单独成块有两个实测问题：
    1. **语义相似度虚高**：短文本叠加了该节的主题词，对任何相关查询都能拿到
       中等相似度，容易把真正含答案的正文块挤出首位（实测出现过标题块抢到 top1）；
    2. **引用不可读**：命中的切片只有一行标题，展示给用户等于没内容。

    合并方向是**向下**：标题属于它引出的那段内容，而不是上一段。
    """
    merged: list[str] = []
    pending: list[str] = []
    for block in blocks:
        if _looks_like_heading(block, min_chars):
            pending.append(block)
            continue
        if pending:
            block = "\n".join([*pending, block])
            pending = []
        merged.append(block)

    if pending:  # 文档以标题结尾：挂到上一块，避免成为孤立短块
        tail = "\n".join(pending)
        if merged:
            merged[-1] = f"{merged[-1]}\n{tail}"
        else:
            merged.append(tail)
    return merged


def _pack(units: list[str], chunk_size: int, overlap: int) -> list[str]:
    """把句子级单元装进不超过 chunk_size 的块，并保留尾部 overlap。"""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for unit in units:
        unit_len = len(unit)
        if unit_len > chunk_size:
            # 极端长句：硬切
            if current:
                chunks.append("".join(current).strip())
                current, current_len = [], 0
            for i in range(0, unit_len, chunk_size):
                chunks.append(unit[i : i + chunk_size].strip())
            continue

        if current_len + unit_len > chunk_size and current:
            chunks.append("".join(current).strip())
            # 构造 overlap 尾巴
            tail: list[str] = []
            tail_len = 0
            for prev in reversed(current):
                if tail_len + len(prev) > overlap:
                    break
                tail.insert(0, prev)
                tail_len += len(prev)
            current = tail
            current_len = tail_len

        current.append(unit)
        current_len += unit_len

    if current:
        chunks.append("".join(current).strip())
    return [c for c in chunks if c]


def split_text(
    text: str,
    *,
    chunk_size: int = 600,
    chunk_overlap: int = 80,
    min_chars: int = 20,
) -> list[TextChunk]:
    """主入口：返回带序号的切分结果。"""
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    if chunk_overlap >= chunk_size:
        chunk_overlap = max(chunk_size // 5, 0)

    raw_chunks: list[str] = []
    for para in _merge_headings(_split_paragraphs(normalized), min_chars):
        if len(para) <= chunk_size:
            raw_chunks.append(para)
        else:
            raw_chunks.extend(_pack(_split_sentences(para), chunk_size, chunk_overlap))

    # 合并过短的相邻块，减少碎片化
    merged: list[str] = []
    for chunk in raw_chunks:
        if merged and len(chunk) < min_chars and len(merged[-1]) + len(chunk) + 1 <= chunk_size:
            merged[-1] = f"{merged[-1]}\n{chunk}"
        else:
            merged.append(chunk)

    return [
        TextChunk(seq=idx, content=content, char_count=len(content))
        for idx, content in enumerate(merged)
    ]
