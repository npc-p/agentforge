"""标注阶段：为样本补充结构化标签。

双轨设计：
- 有可用模型时走批量 LLM 标注（每次 6 条，控制单次上下文与失败重放成本）；
- 模型不可用或调用失败时退回规则标注，保证流水线永远能跑完。
标签结果始终写入 label 字段，规则与模型产出的来源在 label.source 中区分，便于对比。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.core.text import extract_json, truncate
from app.llm.base import ChatMessage
from app.llm.router import LLMGateway
from app.pipeline.base import Sample

logger = get_logger(__name__)

BATCH_SIZE = 6

_INTENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("knowledge_qa", ("是什么", "什么是", "解释", "原理", "区别", "定义")),
    ("computation", ("计算", "多少", "等于", "比例", "百分比")),
    ("data_query", ("统计", "多少条", "查询", "报表", "数据")),
    ("tool_use", ("调用", "接口", "api", "工具")),
    ("chitchat", ("你好", "在吗", "谢谢")),
]

_TOOL_HINTS: list[tuple[str, str]] = [
    ("计算", "calculator"),
    ("时间", "current_time"),
    ("文档", "knowledge_search"),
    ("资料", "knowledge_search"),
    ("数据库", "sql_query"),
    ("接口", "http_request"),
]

LABEL_PROMPT = """你是数据标注专家。请为给定样本标注结构化信息。

只输出 JSON，结构为：
{"items": [{"index": 0, "intent": "意图分类", "difficulty": "easy|medium|hard", "tools": ["可能需要的工具名"], "tags": ["主题词"], "quality": "high|medium|low", "note": "简短判断依据"}]}

标注要求：
- intent 取值：knowledge_qa / computation / data_query / tool_use / chitchat / other
- difficulty 依据推理步数与领域复杂度判定
- tools 仅在确实需要外部工具时给出，工具名限定在：__TOOL_NAMES__
- tags 给出 1-3 个主题词
- 逐条对应输入顺序，index 从 0 开始
"""


def render_label_prompt(tool_names: list[str]) -> str:
    """用显式占位符替换而非 str.format——模板里含 JSON 花括号，format 会误判为占位符。"""
    return LABEL_PROMPT.replace("__TOOL_NAMES__", ", ".join(tool_names[:20]))


def label_by_rules(sample: Sample) -> dict[str, Any]:
    text = f"{sample.instruction} {sample.input}"
    intent = "other"
    for name, keywords in _INTENT_RULES:
        if any(k in text for k in keywords):
            intent = name
            break

    tools = sorted({tool for hint, tool in _TOOL_HINTS if hint in text})
    difficulty = "easy" if len(text) < 40 else ("medium" if len(text) < 120 else "hard")
    return {
        "intent": intent,
        "difficulty": difficulty,
        "tools": tools,
        "tags": [intent],
        "quality": "high" if len(sample.output) > 30 else "medium",
        "note": "规则标注",
        "source": "rule",
    }


class Labeler:
    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self.gateway = gateway

    async def label_batch(self, samples: list[Sample], tool_names: list[str]) -> list[dict[str, Any]]:
        if self.gateway is None or self.gateway.is_mock or not samples:
            return [label_by_rules(s) for s in samples]

        payload = [
            {
                "index": idx,
                "instruction": truncate(s.instruction, 200),
                "input": truncate(s.input, 200),
                "output": truncate(s.output, 300),
            }
            for idx, s in enumerate(samples)
        ]
        messages = [
            ChatMessage(
                role="system",
                content=render_label_prompt(tool_names),
            ),
            ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
        ]
        try:
            result = await self.gateway.chat(messages, json_mode=True, temperature=0.0)
            data = extract_json(result.content) or {}
            items = data.get("items")
            if not isinstance(items, list) or len(items) != len(samples):
                raise ValueError("标注返回数量不匹配")
        except Exception as exc:  # noqa: BLE001 - 标注失败降级为规则
            logger.warning("LLM 标注失败，降级为规则标注: %s", exc)
            return [label_by_rules(s) for s in samples]

        labeled: list[dict[str, Any]] = []
        for idx, sample in enumerate(samples):
            item = items[idx] if isinstance(items[idx], dict) else {}
            fallback = label_by_rules(sample)
            tools = item.get("tools") if isinstance(item.get("tools"), list) else []
            labeled.append(
                {
                    "intent": str(item.get("intent") or fallback["intent"]),
                    "difficulty": str(item.get("difficulty") or fallback["difficulty"]),
                    "tools": [t for t in tools if isinstance(t, str)][:5],
                    "tags": [t for t in (item.get("tags") or []) if isinstance(t, str)][:5]
                    or fallback["tags"],
                    "quality": str(item.get("quality") or fallback["quality"]),
                    "note": truncate(str(item.get("note") or ""), 200),
                    "source": "llm",
                }
            )
        return labeled
