"""答案忠实度校验（groundedness）。

解决的问题：形式规则只能判断「格式对不对」，判不出「答案事实对不对」。
本模块用知识库反查：先按问题检索平台知识库，再让模型判断「答案是否被检索到的片段支持」。

三种判定：
- supported     ：片段明确支持答案；
- contradicted  ：片段与答案冲突（需要拒收）；
- insufficient  ：库里没有相关内容，无法判断（不构成错误，只记录）。

成本控制：检索不到相关内容时**不调用模型**，直接返回 insufficient——
否则每条样本都要付一次模型调用，而绝大多数样本与知识库无关。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from app.core.text import extract_json, truncate
from app.llm.base import ChatMessage
from app.llm.router import LLMGateway
from app.pipeline.base import Sample

logger = get_logger(__name__)

VERDICTS = ("supported", "contradicted", "insufficient")
DEFAULT_SCORE_THRESHOLD = 0.35

JUDGE_PROMPT = """[NODE:JUDGE]
你是数据质检员。请判断「候选答案」是否被「参考片段」支持。

只输出 JSON：{"verdict": "supported|contradicted|insufficient", "reason": "一句话理由"}

判定标准：
- supported：参考片段明确给出候选答案的核心事实；
- contradicted：参考片段与候选答案的核心事实冲突（包括否定关系相反）；
- insufficient：参考片段既不能支持也不能反驳（信息不足）。
只依据参考片段判断，不要使用你自己的背景知识。
"""

SearchFn = Callable[..., Awaitable[list[dict[str, Any]]]]


async def _default_search(query: str, *, kb_id: int | None = None, top_k: int = 3, **_):
    from app.rag.service import search_knowledge

    return await search_knowledge(query, kb_id=kb_id, top_k=top_k)


def parse_verdict(text: str) -> tuple[str, str]:
    data = extract_json(text) or {}
    verdict = str(data.get("verdict") or "insufficient").strip().lower()
    if verdict not in VERDICTS:
        verdict = "insufficient"
    return verdict, truncate(str(data.get("reason") or ""), 200)


async def judge_sample(
    sample: Sample,
    gateway: LLMGateway,
    *,
    kb_id: int | None = None,
    top_k: int = 3,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    search_fn: SearchFn | None = None,
) -> dict[str, Any]:
    """返回 {"verdict", "reason", "evidence_chunk", "score", "used_model"}。"""
    search = search_fn or _default_search
    query = sample.prompt_text[:300]
    hits = await search(query, kb_id=kb_id, top_k=top_k)
    if not hits:
        return {
            "verdict": "insufficient",
            "reason": "知识库无相关内容",
            "evidence_chunk": None,
            "score": 0.0,
            "used_model": False,
        }

    top = max(hits, key=lambda h: float(h.get("score") or 0))
    top_score = float(top.get("score") or 0)
    if top_score < score_threshold:
        return {
            "verdict": "insufficient",
            "reason": f"最高相关度 {top_score:.2f} 低于阈值 {score_threshold}",
            "evidence_chunk": top.get("chunk_id"),
            "score": round(top_score, 4),
            "used_model": False,
        }

    context = "\n\n".join(
        f"[片段{i}] {truncate(h.get('content') or '', 600)}" for i, h in enumerate(hits[:top_k], 1)
    )
    messages = [
        ChatMessage(role="system", content=JUDGE_PROMPT),
        ChatMessage(
            role="user",
            content=(
                f"问题：{query}\n\n候选答案：{truncate(sample.output, 800)}\n\n参考片段：\n{context}"
            ),
        ),
    ]
    try:
        result = await gateway.chat(messages, temperature=0.0, json_mode=True)
        verdict, reason = parse_verdict(result.content)
    except Exception as exc:  # noqa: BLE001 - 校验失败不应中断流水线
        logger.warning("忠实度校验调用失败: %s", exc)
        return {
            "verdict": "insufficient",
            "reason": f"校验调用失败: {str(exc)[:120]}",
            "evidence_chunk": top.get("chunk_id"),
            "score": round(top_score, 4),
            "used_model": True,
        }

    return {
        "verdict": verdict,
        "reason": reason,
        "evidence_chunk": top.get("chunk_id"),
        "score": round(top_score, 4),
        "used_model": True,
    }
