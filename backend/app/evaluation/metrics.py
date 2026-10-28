"""评测指标实现（纯函数，便于单测）。

期望值语法（`expected` 字段）：
- `15|80`      ：必须**全部包含**这些关键词；
- `15|!16`     ：必须包含 `15`，且**不得包含** `16`（`!` 前缀为负例断言）；
- `15|!不是`   ：可用于拦截「答反」类输出。

口径说明：
- HitRate@k：前 k 个结果中至少命中一条相关片段的比例（宽松，仅说明"召回到"）；
- HitRate@1：首位即命中，衡量排序质量的主指标；
- MRR@k   ：首个命中位置的倒数均值；
- 任务通过：正向关键词全部命中 且 无负例命中 且 期望工具全部被调用。
"""

from __future__ import annotations

import statistics
from typing import Any


def parse_expectations(expected: str | None) -> tuple[list[str], list[str]]:
    """把期望串解析为 (正向关键词, 负向关键词)。"""
    must: list[str] = []
    must_not: list[str] = []
    if not expected:
        return must, must_not
    for part in expected.replace("｜", "|").split("|"):
        token = part.strip()
        if not token:
            continue
        if token.startswith("!") and len(token) > 1:
            must_not.append(token[1:].strip())
        else:
            must.append(token)
    return must, must_not


def _keywords(expected: str | None) -> list[str]:
    return parse_expectations(expected)[0]


def first_hit_rank(hits: list[dict[str, Any]], expected: str | None) -> int | None:
    keywords = _keywords(expected)
    if not keywords:
        return None
    for idx, hit in enumerate(hits, start=1):
        content = f"{hit.get('content', '')} {hit.get('doc_name', '')}"
        if any(keyword in content for keyword in keywords):
            return idx
    return None


def retrieval_scores(hits: list[dict[str, Any]], expected: str | None, k: int) -> dict[str, Any]:
    rank = first_hit_rank(hits[:k], expected)
    return {
        "hit": rank is not None,
        "hit_top1": rank == 1,
        "rank": rank,
        "mrr": round(1.0 / rank, 4) if rank else 0.0,
        "top_k": k,
        "returned": len(hits),
    }


def answer_scores(
    answer: str, expected: str | None, expect_tools: list[str] | None, used_tools: list[str]
) -> dict[str, Any]:
    must, must_not = parse_expectations(expected)
    text = answer or ""
    matched = [kw for kw in must if kw in text]
    keyword_ok = bool(must) and len(matched) == len(must)
    violations = [kw for kw in must_not if kw in text]
    tools_needed = expect_tools or []
    tools_ok = all(tool in used_tools for tool in tools_needed)

    return {
        "keyword_total": len(must),
        "keyword_matched": len(matched),
        "missing_keywords": [kw for kw in must if kw not in matched],
        "keyword_ok": keyword_ok,
        "must_not_violations": violations,
        "tools_expected": tools_needed,
        "tools_used": used_tools,
        "tools_ok": tools_ok,
        "passed": keyword_ok and tools_ok and not violations,
    }


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    idx = max(0, min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1)))))
    return round(ordered[idx], 2)


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """把单例结果聚合成套件级指标。"""
    total = len(results)
    if total == 0:
        return {"total": 0}

    latencies = [float(r.get("latency_ms", 0)) for r in results]
    passed = sum(1 for r in results if r.get("passed"))
    hits = [1 if r.get("scores", {}).get("hit") else 0 for r in results if "hit" in r.get("scores", {})]
    top1 = [
        1 if r.get("scores", {}).get("hit_top1") else 0
        for r in results
        if "hit_top1" in r.get("scores", {})
    ]
    mrrs = [float(r.get("scores", {}).get("mrr", 0)) for r in results if "mrr" in r.get("scores", {})]

    metrics: dict[str, Any] = {
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4),
        "latency_avg_ms": round(statistics.fmean(latencies), 2) if latencies else 0.0,
        "latency_p95_ms": percentile(latencies, 95),
        "errors": sum(1 for r in results if r.get("error")),
    }
    if hits:
        metrics["hit_rate"] = round(sum(hits) / len(hits), 4)
    if top1:
        metrics["hit_rate_top1"] = round(sum(top1) / len(top1), 4)
    if mrrs:
        metrics["mrr"] = round(sum(mrrs) / len(mrrs), 4)
    return metrics
