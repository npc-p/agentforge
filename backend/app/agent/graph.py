"""Agent 核心：显式状态机（Plan → Act ⇄ Reflect → Answer）。

为什么手写而不是直接交给框架：
1. 终止条件、重试上限、重复调用抑制必须由业务代码强制，不能依赖模型自觉；
2. 每一步都要落库做链路追踪（agent_steps），黑盒循环会丢掉这部分可观测性；
3. 状态迁移显式化，出问题时能从 run 记录精确复现到某个节点。

对外仅暴露 `stream()` 产出结构化事件，SSE 与内部评测共用同一条执行路径。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import prompts
from app.agent.planner import Plan, Planner, plan_summary_text
from app.config import settings
from app.core.exceptions import AppError
from app.core.logging import Timer, bind_trace_id, get_logger
from app.core.text import extract_json, to_json_str, truncate
from app.db.session import AsyncSessionLocal
from app.llm.base import ChatMessage, Usage
from app.llm.router import LLMGateway, get_gateway
from app.models.conversation import AgentRun, AgentStep, Message
from app.tools.executor import ToolResult, get_executor
from app.tools.registry import registry

logger = get_logger(__name__)

# 事件类型（前后端约定，改名需同步前端）
EV_RUN_STARTED = "run_started"
EV_PLAN = "plan"
EV_NODE = "node"
EV_THOUGHT = "thought"
EV_TOOL_CALL = "tool_call"
EV_TOOL_RESULT = "tool_result"
EV_TOKEN = "token"
EV_CITATIONS = "citations"
EV_USAGE = "usage"
EV_RUN_FINISHED = "run_finished"
EV_ERROR = "error"

_NODE_SEQ = {"plan": 1, "act": 2, "reflect": 3, "answer": 4}

# 硬闸门话术：工具全部失败且无检索引用时使用，不调用模型
NO_EVIDENCE_ANSWER = (
    "未能获取到可靠资料，无法确认该问题的答案。\n\n"
    "可能原因：所需工具执行失败（可查看执行轨迹中的错误信息）、知识库中没有相关内容，"
    "或问题需要外部数据而当前未配置相应工具。\n\n"
    "建议：换一种问法重试，或先在「知识库」中补充相关文档。"
)


def has_no_evidence(rt: "Runtime") -> bool:
    """是否处于「无任何可用证据」状态。

    判定条件：调用过工具、没有任何引用、且所有观察都不构成可用证据。
    「可用证据」不等于「调用成功」——检索返回 0 命中虽然 ok=True，但同样是无证据，
    此时让模型作答等同于让它自由发挥，因此一并纳入闸门。
    """
    if not rt.observations or rt.citations:
        return False
    return not any(obs.evidence for obs in rt.observations)


def observation_is_evidence(tool: str, result: "ToolResult") -> bool:
    """判断一次工具调用是否产出可用证据。"""
    if not result.ok:
        return False
    if tool == "knowledge_search":
        data = result.data if isinstance(result.data, dict) else {}
        return int(data.get("hit_count") or 0) > 0
    return True


@dataclass(slots=True)
class AgentEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def sse(self) -> dict[str, str]:
        return {"event": self.type, "data": to_json_str(self.data)}


@dataclass(slots=True)
class Observation:
    tool: str
    ok: bool
    content: str
    latency_ms: float
    signature: str = ""
    retries: int = 0
    # 是否构成「可用证据」：调用成功但结果为空（如检索 0 命中）不算证据
    evidence: bool = False


@dataclass(slots=True)
class Runtime:
    """单次运行的可变状态，独立于数据库快照，便于测试。"""

    run_id: int = 0
    conversation_id: int = 0
    trace_id: str = "-"
    query: str = ""
    kb_id: int | None = None
    history: list[ChatMessage] = field(default_factory=list)
    plan: Plan | None = None
    observations: list[Observation] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""
    steps: int = 0
    tool_calls: int = 0
    usage: Usage = field(default_factory=Usage)
    status: str = "running"
    error: str | None = None

    def observation_text(self, per_item_limit: int = 1500) -> str:
        if not self.observations:
            return "（本轮未调用工具）"
        blocks = []
        for idx, obs in enumerate(self.observations, start=1):
            state = "成功" if obs.ok else "失败"
            blocks.append(
                f"[观察 {idx}] 工具={obs.tool} 状态={state}\n{truncate(obs.content, per_item_limit)}"
            )
        return "\n\n".join(blocks)


def tool_signature(tool: str, args: dict[str, Any]) -> str:
    raw = f"{tool}:{json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class AgentEngine:
    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self.gateway = gateway or get_gateway()
        self.planner = Planner(self.gateway)
        self.executor = get_executor()

    # ------------------------------------------------------------- 对外入口
    async def stream(
        self,
        query: str,
        *,
        conversation_id: int | None = None,
        kb_id: int | None = None,
        history: list[ChatMessage] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        started = time.perf_counter()
        trace_id = bind_trace_id()

        async with AsyncSessionLocal() as session:
            rt = Runtime(
                trace_id=trace_id,
                query=query,
                kb_id=kb_id,
                history=history or [],
                conversation_id=conversation_id or 0,
            )
            rt.run_id = await self._create_run(session, rt)
            yield AgentEvent(
                EV_RUN_STARTED,
                {
                    "run_id": rt.run_id,
                    "trace_id": trace_id,
                    "mock_mode": self.gateway.is_mock,
                    "max_steps": settings.agent_max_steps,
                },
            )

            try:
                async for event in self._execute(session, rt):
                    yield event
            except AppError as exc:
                rt.status, rt.error = "failed", exc.message
                logger.warning("Agent 运行失败: %s", exc.message)
                yield AgentEvent(EV_ERROR, exc.to_dict())
            except Exception as exc:  # noqa: BLE001
                rt.status, rt.error = "failed", str(exc)[:300]
                logger.exception("Agent 运行异常")
                yield AgentEvent(
                    EV_ERROR, {"code": "agent_error", "message": "智能体执行异常", "detail": str(exc)[:300]}
                )
            finally:
                # 客户端断开（GeneratorExit）时也会进入这里；此处只落库、不产出事件
                if rt.status == "running":
                    rt.status = "succeeded" if rt.answer else "cancelled"
                duration_ms = int((time.perf_counter() - started) * 1000)
                try:
                    await self._finish_run(session, rt, duration_ms)
                except Exception:  # noqa: BLE001
                    logger.warning("运行记录收尾失败 run_id=%s", rt.run_id)

            yield AgentEvent(
                EV_USAGE,
                {
                    "prompt_tokens": rt.usage.prompt_tokens,
                    "completion_tokens": rt.usage.completion_tokens,
                    "steps": rt.steps,
                    "tool_calls": rt.tool_calls,
                    "duration_ms": duration_ms,
                    "mock_mode": self.gateway.is_mock,
                },
            )
            yield AgentEvent(
                EV_RUN_FINISHED, {"run_id": rt.run_id, "status": rt.status, "error": rt.error}
            )

    async def run_collect(self, query: str, *, kb_id: int | None = None) -> dict[str, Any]:
        """非流式执行并汇总结果，供评测与脚本断言使用。"""
        result: dict[str, Any] = {
            "answer": "",
            "tool_calls": [],
            "intent": None,
            "steps": 0,
            "duration_ms": 0,
            "usage": {},
            "status": "unknown",
            "run_id": 0,
            "error": None,
            "citations": [],
        }
        async for event in self.stream(query, kb_id=kb_id):
            if event.type == EV_PLAN:
                result["intent"] = event.data.get("intent")
            elif event.type == EV_TOOL_CALL:
                result["tool_calls"].append(event.data.get("name"))
            elif event.type == EV_TOKEN:
                result["answer"] += event.data.get("delta", "")
            elif event.type == EV_CITATIONS:
                result["citations"] = event.data.get("citations", [])
            elif event.type == EV_USAGE:
                result["steps"] = event.data.get("steps", 0)
                result["duration_ms"] = event.data.get("duration_ms", 0)
                result["usage"] = event.data
            elif event.type == EV_RUN_STARTED:
                result["run_id"] = event.data.get("run_id", 0)
            elif event.type == EV_RUN_FINISHED:
                result["status"] = event.data.get("status")
                result["error"] = event.data.get("error")
        return result

    # ------------------------------------------------------------- 状态机
    async def _execute(self, session: AsyncSession, rt: Runtime) -> AsyncIterator[AgentEvent]:
        max_rounds = settings.agent_max_steps

        # ---------- PLAN
        yield AgentEvent(EV_NODE, {"node": "plan", "label": "任务规划"})
        with Timer() as t_plan:
            plan = await self.planner.plan(rt.query, rt.history, session=session)
        rt.plan = plan
        rt.usage = rt.usage + plan.usage
        await self._save_step(
            session,
            rt,
            "plan",
            thought=truncate(plan.reasoning, 2000),
            observation=plan_summary_text(plan),
            latency_ms=t_plan.elapsed_ms,
        )
        yield AgentEvent(
            EV_PLAN,
            {
                **plan.to_dict(),
                "latency_ms": t_plan.elapsed_ms,
                "plan_nodes": len(plan.tasks),
            },
        )

        # ---------- ACT ⇄ REFLECT
        candidate = ""
        while rt.steps < max_rounds:
            yield AgentEvent(
                EV_NODE,
                {"node": "act", "label": f"执行第 {rt.steps + 1} 轮", "round": rt.steps + 1},
            )
            with Timer() as t_act:
                decision = await self._decide(rt, session)
            rt.usage = rt.usage + decision.usage
            thought = decision.content.strip()

            if not decision.tool_calls:
                candidate = thought
                if thought:
                    yield AgentEvent(EV_THOUGHT, {"content": truncate(thought, 800)})
                await self._save_step(
                    session,
                    rt,
                    "act",
                    thought=truncate(thought, 2000),
                    latency_ms=t_act.elapsed_ms,
                )
                rt.steps += 1
                break

            if thought:
                yield AgentEvent(EV_THOUGHT, {"content": truncate(thought, 800)})

            duplicated = False
            for call in decision.tool_calls:
                # 会话绑定了知识库时，模型若未指定 kb_id，则强制收敛到该知识库范围，
                # 避免「前端选了知识库、实际却全库检索」这种静默越界。
                arguments = dict(call.arguments or {})
                if call.name == "knowledge_search" and rt.kb_id and not arguments.get("kb_id"):
                    arguments["kb_id"] = rt.kb_id

                signature = tool_signature(call.name, arguments)
                if any(obs.ok and obs.signature == signature for obs in rt.observations):
                    duplicated = True
                    logger.info("重复工具调用已拦截: %s %s", call.name, signature)
                    yield AgentEvent(
                        EV_THOUGHT,
                        {"content": f"[已阻止重复调用] 工具 {call.name} 与参数相同，结果已存在"},
                    )
                    continue

                rt.tool_calls += 1
                yield AgentEvent(
                    EV_TOOL_CALL,
                    {"name": call.name, "arguments": arguments, "call_id": call.id},
                )
                result: ToolResult = await self.executor.execute(
                    call.name, arguments, run_id=rt.run_id, session=session
                )
                observation_text = result.to_observation()
                rt.observations.append(
                    Observation(
                        tool=call.name,
                        ok=result.ok,
                        content=observation_text,
                        latency_ms=result.latency_ms,
                        signature=signature,
                        retries=result.retries,
                        evidence=observation_is_evidence(call.name, result),
                    )
                )
                self._collect_citations(rt, call.name, result)

                yield AgentEvent(
                    EV_TOOL_RESULT,
                    {
                        "name": call.name,
                        "ok": result.ok,
                        "latency_ms": result.latency_ms,
                        "retries": result.retries,
                        "error": result.error,
                        "preview": truncate(observation_text, 600),
                    },
                )
                await self._save_step(
                    session,
                    rt,
                    "act",
                    thought=truncate(thought, 1000),
                    action=call.name,
                    action_input=arguments,
                    observation=truncate(observation_text, 4000),
                    status="ok" if result.ok else "failed",
                    latency_ms=result.latency_ms,
                )

            rt.steps += 1
            if duplicated or self._all_failed(rt):
                break

            # 校验：信息是否足够决定继续循环还是作答
            yield AgentEvent(EV_NODE, {"node": "reflect", "label": "结果校验"})
            with Timer() as t_ref:
                enough, reason, ref_usage = await self._reflect(rt, session)
            rt.usage = rt.usage + ref_usage
            await self._save_step(
                session,
                rt,
                "reflect",
                thought=reason,
                status="ok" if enough else "insufficient",
                latency_ms=t_ref.elapsed_ms,
            )
            yield AgentEvent(
                EV_THOUGHT,
                {"content": f"[信息校验] {'充分' if enough else '不足'}：{reason or '无需补充'}"},
            )
            if enough:
                break
            rt.observations.append(
                Observation(
                    tool="reflect",
                    ok=False,
                    content=f"信息不足：{reason}。请补充检索或改用其他工具。",
                    latency_ms=t_ref.elapsed_ms,
                )
            )

        # ---------- ANSWER
        if has_no_evidence(rt):
            # 硬闸门：无任何可用证据时不调用模型，避免"合理推测"变成幻觉
            rt.answer = NO_EVIDENCE_ANSWER
            yield AgentEvent(EV_NODE, {"node": "answer", "label": "证据不足，终止作答"})
            yield AgentEvent(
                EV_THOUGHT,
                {"content": "[硬闸门] 工具调用全部失败或返回空结果，且无检索引用，已跳过模型生成"},
            )
            for idx in range(0, len(rt.answer), 24):
                yield AgentEvent(EV_TOKEN, {"delta": rt.answer[idx : idx + 24]})
            await self._save_step(
                session,
                rt,
                "answer",
                thought="no_evidence_gate",
                observation=truncate(rt.answer, 4000),
                status="insufficient",
                latency_ms=0.0,
            )
        elif candidate and not rt.observations:
            # 无需外部信息的简单问答：直接复用决策阶段的输出，省掉一次模型调用
            rt.answer = candidate
            for idx in range(0, len(candidate), 24):
                yield AgentEvent(EV_TOKEN, {"delta": candidate[idx : idx + 24]})
            await self._save_step(
                session, rt, "answer", observation=truncate(rt.answer, 4000), latency_ms=0.0
            )
        else:
            yield AgentEvent(EV_NODE, {"node": "answer", "label": "生成回答"})
            with Timer() as t_ans:
                async for event in self._answer(rt, session):
                    yield event
            await self._save_step(
                session,
                rt,
                "answer",
                observation=truncate(rt.answer, 4000),
                latency_ms=t_ans.elapsed_ms,
            )

        if rt.citations:
            yield AgentEvent(EV_CITATIONS, {"citations": rt.citations})

        rt.status = "succeeded"
        await self._save_assistant_message(session, rt)

    # ------------------------------------------------------------- 节点实现
    async def _decide(self, rt: Runtime, session: AsyncSession):
        system = await prompts.load_template(
            "decide", session=session, variables={"extra_hint": self._decide_hint(rt)}
        )
        messages = self._base_messages(rt, system)
        return await self.gateway.chat(
            messages, tools=registry.openai_schemas(), temperature=0.2
        )

    def _decide_hint(self, rt: Runtime) -> str:
        if not rt.plan:
            return ""
        hints = [
            f"规划意图：{rt.plan.intent}（模型判定{'需要' if rt.plan.need_tools else '无需'}工具）"
        ]
        if rt.plan.need_tools and not rt.observations:
            planned = [t.action for t in rt.plan.tasks if t.action != "answer"]
            if planned:
                hints.append(f"规划已指定工具：{', '.join(planned)}，请先执行再作答")
        if rt.observations:
            hints.append(f"已完成 {len(rt.observations)} 次工具调用，请优先判断能否直接作答")
        if rt.kb_id:
            hints.append(
                f"当前会话已绑定知识库 kb_id={rt.kb_id}，检索将限定在该库范围内"
            )
        if rt.steps:
            hints.append(f"当前为第 {rt.steps + 1} 轮，上限 {settings.agent_max_steps} 轮")
        return "\n".join(hints)

    async def _reflect(self, rt: Runtime, session: AsyncSession) -> tuple[bool, str, Usage]:
        system = await prompts.load_template("reflect", session=session)
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(
                role="user",
                content=f"用户问题：{rt.query}\n\n观察结果：\n{rt.observation_text(800)}",
            ),
        ]
        try:
            result = await self.gateway.chat(messages, temperature=0.0)
        except AppError:
            return True, "校验节点调用失败，按信息充分处理", Usage()
        data = extract_json(result.content) or {}
        enough = bool(data.get("enough", True))
        reason = str(data.get("missing") or data.get("reason") or "")[:200]
        return enough, reason, result.usage

    async def _answer(self, rt: Runtime, session: AsyncSession) -> AsyncIterator[AgentEvent]:
        system = await prompts.load_template("answer", session=session)
        persona = await prompts.load_template("persona", session=session)
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="system", content=persona),
            ChatMessage(
                role="user",
                content=f"【观察结果】\n{rt.observation_text()}\n\n用户问题：{rt.query}",
            ),
        ]

        buffer: list[str] = []
        try:
            async for delta in self.gateway.provider.stream(messages, temperature=0.4):
                buffer.append(delta)
                yield AgentEvent(EV_TOKEN, {"delta": delta})
        except Exception as exc:  # noqa: BLE001 - 流式中断降级为一次性返回
            logger.warning("流式输出失败，降级为非流式: %s", exc)
            result = await self.gateway.chat(messages, temperature=0.4)
            rt.usage = rt.usage + result.usage
            if not buffer:
                buffer.append(result.content)
                yield AgentEvent(EV_TOKEN, {"delta": result.content})

        rt.answer = "".join(buffer)
        if not rt.answer.strip():
            rt.answer = "抱歉，本轮未能生成有效回答，请补充更多信息或重试。"
            yield AgentEvent(EV_TOKEN, {"delta": rt.answer})

    # ------------------------------------------------------------- 辅助
    def _base_messages(self, rt: Runtime, system: str) -> list[ChatMessage]:
        messages = [ChatMessage(role="system", content=system)]
        messages.extend(rt.history[-6:])
        content = rt.query
        if rt.observations:
            content = f"{rt.query}\n\n【已获得的工具观察结果】\n{rt.observation_text()}"
        messages.append(ChatMessage(role="user", content=content))
        return messages

    def _all_failed(self, rt: Runtime) -> bool:
        scope = rt.observations[-3:]
        return bool(scope) and all(not obs.ok for obs in scope)

    def _collect_citations(self, rt: Runtime, tool: str, result: ToolResult) -> None:
        if tool != "knowledge_search" or not result.ok or not isinstance(result.data, dict):
            return
        for hit in result.data.get("hits") or []:
            rt.citations.append(
                {
                    "index": len(rt.citations) + 1,
                    "chunk_id": hit.get("chunk_id"),
                    "doc_name": hit.get("doc_name"),
                    "score": hit.get("score"),
                    "snippet": truncate(str(hit.get("content", "")), 240),
                }
            )

    # ------------------------------------------------------------- 持久化
    async def _create_run(self, session: AsyncSession, rt: Runtime) -> int:
        run = AgentRun(
            conversation_id=rt.conversation_id, trace_id=rt.trace_id, query=rt.query, status="running"
        )
        session.add(run)
        await session.commit()
        return int(run.id)

    async def _save_step(
        self,
        session: AsyncSession,
        rt: Runtime,
        node: str,
        *,
        thought: str | None = None,
        action: str | None = None,
        action_input: dict[str, Any] | None = None,
        observation: str | None = None,
        status: str = "ok",
        latency_ms: float = 0.0,
    ) -> None:
        try:
            session.add(
                AgentStep(
                    run_id=rt.run_id,
                    seq=rt.steps * 10 + _NODE_SEQ.get(node, 9),
                    node=node,
                    thought=thought,
                    action=action,
                    action_input=action_input,
                    observation=observation,
                    status=status,
                    latency_ms=int(latency_ms),
                )
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            logger.warning("执行轨迹写入失败: %s", exc)

    async def _save_assistant_message(self, session: AsyncSession, rt: Runtime) -> None:
        if not rt.conversation_id:
            return
        try:
            session.add(
                Message(
                    conversation_id=rt.conversation_id,
                    run_id=rt.run_id,
                    role="assistant",
                    content=rt.answer,
                    citations=rt.citations or None,
                    token_in=rt.usage.prompt_tokens,
                    token_out=rt.usage.completion_tokens,
                )
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            logger.warning("回答消息写入失败: %s", exc)

    async def _finish_run(self, session: AsyncSession, rt: Runtime, duration_ms: int) -> None:
        from datetime import datetime

        run = await session.get(AgentRun, rt.run_id)
        if run is None:
            return
        run.status = rt.status
        run.intent = rt.plan.intent if rt.plan else None
        run.plan = rt.plan.to_dict() if rt.plan else None
        run.step_count = rt.steps
        run.tool_call_count = rt.tool_calls
        run.prompt_tokens = rt.usage.prompt_tokens
        run.completion_tokens = rt.usage.completion_tokens
        run.duration_ms = duration_ms
        run.error = rt.error
        run.finished_at = datetime.now()
        await session.commit()


_engine: AgentEngine | None = None


def get_engine() -> AgentEngine:
    global _engine
    if _engine is None:
        _engine = AgentEngine()
    return _engine
