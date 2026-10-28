"""BM25 检索（纯 Python 实现）。

为什么需要它：向量检索擅长语义近似，但会漏掉精确术语、编号、专有名词；
BM25 恰好补这一点。两者用 RRF 融合，是中小知识库性价比最高的混合方案。

中文分词不引入 jieba：用「拉丁词 + 中文二元组（含单字）」的切分方式，
在检索任务上接近粗分词效果，且零依赖、无词典截断问题。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

_LATIN = re.compile(r"[a-zA-Z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "或", "有", "对", "为", "中", "上", "下", "我", "你",
    "他", "这个", "那个", "什么", "怎么", "如何", "请", "吗", "呢", "吧", "啊",
    "the", "a", "an", "of", "to", "in", "is", "are", "and", "or", "for",
}


def tokenize(text: str) -> list[str]:
    """统一的切分入口，索引与查询必须共用同一函数（配对契约同源）。"""
    if not text:
        return []
    lowered = text.lower()
    tokens: list[str] = []

    for word in _LATIN.findall(lowered):
        if word not in _STOPWORDS:
            tokens.append(f"w:{word}")

    chars = _CJK.findall(lowered)
    for ch in chars:
        if ch not in _STOPWORDS:
            tokens.append(f"c:{ch}")
    for i in range(len(chars) - 1):
        tokens.append(f"b:{chars[i]}{chars[i + 1]}")

    return tokens


@dataclass(slots=True)
class BM25Document:
    doc_id: str
    tokens: list[str]
    length: int
    term_freq: Counter = field(default_factory=Counter)


class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._docs: dict[str, BM25Document] = {}
        self._df: Counter = Counter()
        self._avg_len: float = 0.0
        self._dirty = False

    def __len__(self) -> int:
        return len(self._docs)

    def add(self, doc_id: str, text: str) -> None:
        tokens = tokenize(text)
        self._docs[doc_id] = BM25Document(
            doc_id=doc_id,
            tokens=tokens,
            length=len(tokens),
            term_freq=Counter(tokens),
        )
        self._dirty = True

    def remove(self, doc_id: str) -> None:
        if self._docs.pop(doc_id, None) is not None:
            self._dirty = True

    def _ensure_stats(self) -> None:
        if not self._dirty:
            return
        self._df = Counter()
        for doc in self._docs.values():
            for term in doc.term_freq:
                self._df[term] += 1
        total = sum(d.length for d in self._docs.values())
        self._avg_len = (total / len(self._docs)) if self._docs else 0.0
        self._dirty = False

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        if not self._docs:
            return []
        self._ensure_stats()

        q_tokens = [t for t in tokenize(query)]
        if not q_tokens:
            return []

        n_docs = len(self._docs)
        scores: dict[str, float] = {}

        for term in set(q_tokens):
            df = self._df.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            for doc in self._docs.values():
                tf = doc.term_freq.get(term, 0)
                if not tf:
                    continue
                denom = tf + self.k1 * (
                    1 - self.b + self.b * (doc.length / self._avg_len if self._avg_len else 1)
                )
                scores[doc.doc_id] = scores.get(doc.doc_id, 0.0) + idf * (tf * (self.k1 + 1)) / denom

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_k]
        max_score = ranked[0][1] if ranked else 0.0
        if max_score > 0:
            ranked = [(doc_id, score / max_score) for doc_id, score in ranked]
        return ranked

    def stats(self) -> dict[str, Any]:
        self._ensure_stats()
        return {
            "doc_count": len(self._docs),
            "vocab_size": len(self._df),
            "avg_doc_len": round(self._avg_len, 2),
        }
