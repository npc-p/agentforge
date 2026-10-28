"""离线 Mock 供应商。

用途有两个：
1. 未配置 API Key 时保证全链路可运行（演示、CI、面试环境无网）；
2. 单元测试中提供确定性输出，避免测试依赖外部模型。

实现方式：各 Agent 节点在 system prompt 首行写入节点标识（如 `[NODE:PLAN]`），
Mock 依据标识走不同的输出模板；工具选择走关键词启发式规则。
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from app.llm.base import ChatMessage, LLMProvider, LLMResult, ToolCall, Usage

_NODE_PATTERN = re.compile(r"\[NODE:([A-Z_]+)\]")

_KEYWORD_TOOLS: list[tuple[tuple[str, ...], str]] = [
    (("计算", "算一下", "等于", "加", "减", "乘", "除", "百分比", "求和"), "calculator"),
    (("几点", "日期", "今天", "现在", "星期", "时间"), "current_time"),
    (("知识库", "文档", "资料", "根据材料", "查一下资料", "检索"), "knowledge_search"),
    (("多少行", "统计", "表里", "数据库查询"), "sql_query"),
]


class MockProvider(LLMProvider):
    name = "mock"
    model = "mock-deterministic"

    @property
    def ready(self) -> bool:
        return True

    def _last_user(self, messages: list[ChatMessage]) -> str:
        for msg in reversed(messages):
            if msg.role == "user":
                return msg.content
        return messages[-1].content if messages else ""

    def _node(self, messages: list[ChatMessage]) -> str:
        for msg in messages:
            if msg.role == "system":
                match = _NODE_PATTERN.search(msg.content)
                if match:
                    return match.group(1)
        return "ANSWER"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResult:
        node = self._node(messages)
        query = self._last_user(messages)

        if node == "PLAN":
            return self._plan(query, messages)
        if node == "DECIDE":
            return self._decide(query, tools or [])
        if node == "REFLECT":
            return LLMResult(
                content=json.dumps({"enough": True, "reason": "mock 判定信息充足"}, ensure_ascii=False),
                usage=Usage(80, 20),
                model=self.model,
            )
        return self._answer(query, messages)

    def _plan(self, query: str, messages: list[ChatMessage]) -> LLMResult:
        task_type = "knowledge_qa" if any(k in query for k in ("文档", "资料", "知识库", "是什么")) else "general"
        compute = any(k in query for k in ("计算", "算", "等于", "元", "百分比"))
        tasks: list[dict[str, Any]] = []
        if task_type == "knowledge_qa":
            tasks.append({"step": 1, "action": "knowledge_search", "desc": "检索知识库相关片段"})
        if compute:
            tasks.append({"step": len(tasks) + 1, "action": "calculator", "desc": "完成数值计算"})
        if not tasks:
            tasks.append({"step": 1, "action": "answer", "desc": "直接依据通用知识回答"})

        plan = {
            "intent": task_type,
            "tasks": tasks,
            "need_tools": any(t["action"] != "answer" for t in tasks),
            "reasoning": "mock 规划器依据关键词划分任务",
        }
        return LLMResult(
            content=json.dumps(plan, ensure_ascii=False),
            usage=Usage(120, 40),
            model=self.model,
        )

    def _decide(self, query: str, tools: list[dict[str, Any]]) -> LLMResult:
        available = {t.get("function", {}).get("name") for t in tools}
        chosen: str | None = None
        for keywords, tool in _KEYWORD_TOOLS:
            if tool in available and any(k in query for k in keywords):
                chosen = tool
                break

        if not chosen:
            return LLMResult(
                content="",
                tool_calls=[],
                usage=Usage(90, 10),
                model=self.model,
                finish_reason="stop",
            )

        args = self._infer_args(chosen, query)
        call = ToolCall(id=f"mock_{chosen}", name=chosen, arguments=args)
        return LLMResult(
            content="",
            tool_calls=[call],
            usage=Usage(90, 25),
            model=self.model,
            finish_reason="tool_calls",
        )

    def _infer_args(self, tool: str, query: str) -> dict[str, Any]:
        if tool == "calculator":
            expr = _extract_expression(query)
            return {"expression": expr or "1+1"}
        if tool == "current_time":
            return {"timezone": "Asia/Shanghai"}
        if tool == "knowledge_search":
            return {"query": query[:120], "top_k": 5}
        if tool == "sql_query":
            return {"sql": "SELECT COUNT(*) AS cnt FROM conversations"}
        return {}

    def _answer(self, query: str, messages: list[ChatMessage]) -> LLMResult:
        context = ""
        joined = "\n".join(m.content for m in messages if m.role in ("user", "tool", "system"))
        marker = "【参考资料】"
        if marker in joined:
            tail = joined.split(marker, 1)[1]
            context = tail.split("【", 1)[0].strip()

        if context:
            body = (
                f"根据知识库检索到的内容，针对「{query[:60]}」的可确认信息如下：\n\n"
                f"{context[:600]}\n\n"
                "以上结论均来自上述片段，引用编号见消息尾部。"
            )
        else:
            body = (
                f"（离线 Mock 模式）已接收问题：{query[:80]}。\n\n"
                "当前未配置真实模型 API Key，因此未生成真实推理结果。"
                "系统已完成意图识别、任务规划与工具编排链路验证；"
                "配置 DEEPSEEK_API_KEY 后即可获得真实回答。"
            )

        return LLMResult(
            content=body,
            usage=Usage(prompt_tokens=len(joined) // 3, completion_tokens=len(body) // 3),
            model=self.model,
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        result = self._answer(self._last_user(messages), messages)
        for i in range(0, len(result.content), 8):
            yield result.content[i : i + 8]

    async def close(self) -> None:
        return None


def _extract_expression(query: str) -> str | None:
    """从自然语言中抽取算术表达式，例如「帮我算 12*8+5 等于多少」。"""
    expr = re.search(r"([0-9\.\+\-\*/\(\)\s%]{3,})", query)
    if not expr:
        return None
    text = expr.group(1).strip()
    text = re.sub(r"\s+", "", text)
    return text if any(ch.isdigit() for ch in text) else None
