"""去重阶段：SimHash 近重复检测。

为什么不用精确哈希：同一问题的多种问法、轻微改写的答案，精确哈希完全失效，
而 SimHash 能用汉明距离捕获「大意相同」的样本。

阈值标定（64 位指纹，实测于中文短文本）：
- 改一字（如「与」→「和」）：距离 10~12
- 主题无关的句子：距离 24~30
因此默认阈值取 14，可同时容纳轻微改写而不误杀不同主题。
该值随语料长度变化，长文档建议下调至 6~8，生产环境应按实际语料重新标定。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.pipeline.base import Sample

_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[a-zA-Z0-9]+")
_DEFAULT_THRESHOLD = 14


def _shingles(text: str, size: int = 3) -> list[str]:
    lowered = text.lower()
    units = _CJK.findall(lowered) + [w for w in _LATIN.findall(lowered)]
    if len(units) < size:
        return ["".join(units)] if units else []
    return ["".join(units[i : i + size]) for i in range(len(units) - size + 1)]


def simhash(text: str, bits: int = 64) -> int:
    vector = [0] * bits
    for shingle in _shingles(text):
        digest = hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for i in range(bits):
            vector[i] += 1 if (value >> i) & 1 else -1
    fingerprint = 0
    for i in range(bits):
        if vector[i] > 0:
            fingerprint |= 1 << i
    return fingerprint


def simhash_hex(text: str) -> str:
    return f"{simhash(text):016x}"


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class Deduper:
    """近重复判定器。

    实现选择：精确线性比对。原因是分带索引只有在「分带数 > 汉明阈值」时才能保证
    召回（鸽笼原理），阈值 14 需要 15 个以上分带，届时桶内碰撞严重、收益消失。
    在目标规模（数千至两万条）下，线性比对每次约 0.1~0.2 µs，总开销可接受；
    超过 2e4 条时再引入「分带预筛 + 簇内精算 + 向量聚类」的复合方案。
    """

    def __init__(self, threshold: int = _DEFAULT_THRESHOLD) -> None:
        self.threshold = threshold
        self._fingerprints: list[int] = []

    def add(self, text: str) -> tuple[bool, int, int | None]:
        """返回 (是否重复, 指纹, 命中的已有下标)。"""
        fingerprint = simhash(text)

        for idx, existing in enumerate(self._fingerprints):
            if hamming(fingerprint, existing) <= self.threshold:
                return True, fingerprint, idx

        self._fingerprints.append(fingerprint)
        return False, fingerprint, None


def dedup_batch(samples: list[Sample], threshold: int = _DEFAULT_THRESHOLD) -> tuple[list[Sample], dict[str, Any]]:
    deduper = Deduper(threshold=threshold)
    kept: list[Sample] = []
    duplicates = 0
    seen_exact: dict[str, int] = {}

    for sample in samples:
        key = f"{sample.prompt_text}||{sample.output}"
        if key in seen_exact:
            duplicates += 1
            continue
        seen_exact[key] = len(kept)

        is_dup, fingerprint, _doc_id = deduper.add(sample.prompt_text + sample.output)
        if is_dup:
            duplicates += 1
            continue

        sample.meta = {**sample.meta, "simhash": f"{fingerprint:016x}"}
        kept.append(sample)

    return kept, {
        "total": len(samples),
        "kept": len(kept),
        "duplicates": duplicates,
        "threshold": threshold,
    }
