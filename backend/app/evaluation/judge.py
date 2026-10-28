"""答案质量判定（LLM-as-judge）。

与关键词断言的互补关系：
- 关键词断言便宜、确定，但只能判断「有没有出现期望内容」；
- judge 能判断「是否答非所问、是否部分正确」，但每次评测都要付一次模型调用。

因此 judge 是**可选开关**（`eval_suites.config.judge = true`），默认关闭。
判定结果与关键词结果同时落库，两者都通过才算通过——宁可严，不可松。
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.core.text import extract_json, truncate
from app.llm.base import ChatMessage
from app.llm.router import LLMGateway

logger = get_logger(__name__)

VERDICT_SCORE = {"correct": 1.0, "partial": 0.5, "wrong": 0.0, "off_topic": 0.0}

JUDGE_PROMPT = """[NODE:EVAL_JUDGE]
你是严格的评测员。请判断「模型回答」相对于「问题」与「期望要点」的质量。

只输出 JSON：{"verdict": "correct|partial|wrong|off_topic", "score": 0到1的小数, "reason": "一句话理由"}

判定标准：
- correct   ：回答命中期望要点且无事实错误；
- partial   ：命中部分要点，或要点正确但包含明显无关内容；
- wrong     ：回答与期望要点矛盾，或存在事实错误；
- off_topic ：答非所问，未回应用户问题。
不要因为措辞不同而扣分，只看语义是否达成。
"""


async def judge_answer(
    gateway: LLMGateway,
    *,
    query: str,
    expected: str | None,
    answer: str,
) -> dict[str, Any]:
    messages = [
        ChatMessage(role="system", content=JUDGE_PROMPT),
        ChatMessage(
            role="user",
            content=(
                f"问题：{query}\n\n期望要点：{expected or '（未提供，仅判断是否切题与有无事实错误）'}"
                f"\n\n模型回答：{truncate(answer, 1500)}"
            ),
        ),
    ]
    try:
        result = await gateway.chat(messages, temperature=0.0, json_mode=True)
    except Exception as exc:  # noqa: BLE001 - judge 失败不应让整轮评测失败
        logger.warning("评测判定调用失败: %s", exc)
        return {
            "verdict": "unknown",
            "score": 0.0,
            "reason": f"判定调用失败: {str(exc)[:120]}",
            "used": False,
        }

    data = extract_json(result.content) or {}
    verdict = str(data.get("verdict") or "unknown").strip().lower()
    if verdict not in VERDICT_SCORE:
        verdict = "unknown"

    score = data.get("score")
    if not isinstance(score, (int, float)):
        score = VERDICT_SCORE.get(verdict, 0.0)

    return {
        "verdict": verdict,
        "score": round(float(score), 4),
        "reason": truncate(str(data.get("reason") or ""), 200),
        "used": True,
    }
