"""任务规划器：把自然语言请求转成结构化任务序列。

规划结果必须经过结构校验与工具名白名单过滤——模型虚构工具名是常见失效模式，
若不在这一层拦住，错误会以「工具不存在」的形式暴露在执行阶段，排查成本更高。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import prompts
from app.config import settings
from app.core.logging import get_logger
from app.core.text import extract_json
from app.llm.base import ChatMessage, Usage
from app.llm.router import LLMGateway
from app.tools.registry import registry

logger = get_logger(__name__)

INTENTS = {"knowledge_qa", "computation", "data_query", "web_lookup", "chitchat", "mixed"}


@dataclass(slots=True)
class PlanTask:
    step: int
    action: str
    desc: str = ""


@dataclass(slots=True)
class Plan:
    intent: str = "chitchat"
    need_tools: bool = False
    tasks: list[PlanTask] = field(default_factory=list)
    reasoning: str = ""
    degraded: bool = False
    usage: Usage = field(default_factory=Usage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "need_tools": self.need_tools,
            "tasks": [{"step": t.step, "action": t.action, "desc": t.desc} for t in self.tasks],
            "reasoning": self.reasoning,
            "degraded": self.degraded,
        }


def parse_plan(text: str) -> Plan:
    """解析模型输出；失败时降级为「直接回答」而非抛错。"""
    data = extract_json(text)
    if not data:
        logger.warning("规划输出无法解析为 JSON，降级为直接回答: %s", text[:160])
        return Plan(degraded=True, tasks=[PlanTask(1, "answer", "解析失败，直接回答")])

    intent = str(data.get("intent") or "chitchat").strip().lower()
    if intent not in INTENTS:
        intent = "mixed"

    raw_tasks = data.get("tasks")
    tasks: list[PlanTask] = []
    if isinstance(raw_tasks, list):
        for idx, item in enumerate(raw_tasks[: settings.agent_max_plan_tasks], start=1):
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "answer").strip()
            if action != "answer" and action not in registry.names():
                logger.warning("规划中出现未知工具 %s，已改写为 answer", action)
                action = "answer"
            tasks.append(
                PlanTask(
                    step=int(item.get("step") or idx),
                    action=action,
                    desc=str(item.get("desc") or "")[:200],
                )
            )

    if not tasks:
        tasks = [PlanTask(1, "answer", "无需外部工具，直接回答")]

    need_tools = bool(data.get("need_tools")) and any(t.action != "answer" for t in tasks)
    return Plan(
        intent=intent,
        need_tools=need_tools,
        tasks=tasks,
        reasoning=str(data.get("reasoning") or "")[:500],
    )


class Planner:
    def __init__(self, gateway: LLMGateway) -> None:
        self.gateway = gateway

    async def plan(
        self,
        query: str,
        history: list[ChatMessage] | None = None,
        session: AsyncSession | None = None,
    ) -> Plan:
        """session 传入后，规划模板会优先读取数据库中的启用版本（支持线上调优不发版）。"""
        system = await prompts.load_template(
            "plan", session=session, variables={"max_tasks": settings.agent_max_plan_tasks}
        )
        tool_names = ", ".join(registry.names())
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(
                role="system",
                content=f"当前可用工具：{tool_names}。用户问题：" if tool_names else "用户问题：",
            ),
        ]
        messages.extend((history or [])[-4:])
        messages.append(ChatMessage(role="user", content=query))

        result = await self.gateway.chat(messages, json_mode=False, temperature=0.1)
        plan = parse_plan(result.content)
        plan.usage = result.usage
        if not plan.tasks:
            plan = Plan(degraded=True, tasks=[PlanTask(1, "answer", "空计划兜底")], usage=result.usage)
        return plan


def plan_summary_text(plan: Plan) -> str:
    """供执行轨迹落库的紧凑文本，避免把整段 JSON 塞进 observation。"""
    if not plan.tasks:
        return "（空计划）"
    body = "; ".join(f"{t.step}.{t.action}({t.desc})" for t in plan.tasks)
    return f"intent={plan.intent} need_tools={plan.need_tools} tasks=[{body}]"
