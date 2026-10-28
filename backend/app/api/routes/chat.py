"""对话接口：SSE 流式 + 同步返回两种模式，共用同一套状态机。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.agent.graph import EV_CITATIONS, EV_RUN_FINISHED, EV_RUN_STARTED, EV_TOKEN, EV_USAGE, get_engine
from app.api.deps import db_session
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.core.response import ok
from app.models.conversation import AgentRun, AgentStep, Conversation, Message
from app.schemas.chat import ChatRequest, ConversationCreate, ConversationUpdate
from app.services import conversation_service as conv_svc

logger = get_logger(__name__)
router = APIRouter(tags=["chat"])


@router.post("/chat")
async def chat(payload: ChatRequest, session: AsyncSession = Depends(db_session)):
    """同步对话：内部仍走完整状态机，事件聚合后一次性返回。"""
    conv = await conv_svc.get_or_create(session, payload.conversation_id, kb_id=payload.kb_id)
    await conv_svc.add_message(session, conv.id, role="user", content=payload.query)
    history = await conv_svc.history_as_chat(session, conv.id)

    answer, citations, usage, run_id, status, error = "", [], {}, 0, "unknown", None
    tool_calls: list[str] = []

    async for event in get_engine().stream(
        payload.query, conversation_id=conv.id, kb_id=payload.kb_id, history=history
    ):
        if event.type == EV_TOKEN:
            answer += event.data.get("delta", "")
        elif event.type == EV_CITATIONS:
            citations = event.data.get("citations", [])
        elif event.type == EV_USAGE:
            usage = event.data
        elif event.type == EV_RUN_STARTED:
            run_id = event.data.get("run_id", 0)
        elif event.type == "tool_call":
            tool_calls.append(event.data.get("name"))
        elif event.type == EV_RUN_FINISHED:
            status, error = event.data.get("status"), event.data.get("error")

    return ok(
        {
            "conversation_id": conv.id,
            "run_id": run_id,
            "answer": answer,
            "citations": citations,
            "tool_calls": tool_calls,
            "steps": usage.get("steps", 0),
            "duration_ms": usage.get("duration_ms", 0),
            "mock_mode": usage.get("mock_mode", False),
            "status": status,
            "error": error,
        }
    )


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest, session: AsyncSession = Depends(db_session)):
    """SSE 流式对话。事件类型见 app/agent/graph.py 顶部常量。"""
    conv = await conv_svc.get_or_create(session, payload.conversation_id, kb_id=payload.kb_id)
    await conv_svc.add_message(session, conv.id, role="user", content=payload.query)
    history = await conv_svc.history_as_chat(session, conv.id)
    conversation_id = conv.id

    async def event_generator():
        yield {
            "event": "conversation",
            "data": json.dumps({"conversation_id": conversation_id}, ensure_ascii=False),
        }
        try:
            async for event in get_engine().stream(
                payload.query,
                conversation_id=conversation_id,
                kb_id=payload.kb_id,
                history=history,
            ):
                yield event.sse()
        except Exception as exc:  # noqa: BLE001
            logger.exception("SSE 流中断")
            yield {
                "event": "error",
                "data": json.dumps(
                    {"code": "stream_error", "message": str(exc)[:200]}, ensure_ascii=False
                ),
            }

    return EventSourceResponse(event_generator(), ping=15000)


@router.get("/conversations")
async def list_conversations(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(db_session),
):
    items, total = await conv_svc.list_conversations(session, page=page, size=size)
    return ok({"items": items, "total": total, "page": page, "size": size})


@router.post("/conversations")
async def create_conversation(payload: ConversationCreate, session: AsyncSession = Depends(db_session)):
    conv = await conv_svc.create_conversation(
        session, title=payload.title, kb_id=payload.knowledge_base_id, channel="web"
    )
    return ok({"id": conv.id, "title": conv.title})


@router.patch("/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: int, payload: ConversationUpdate, session: AsyncSession = Depends(db_session)
):
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise NotFoundError(f"会话不存在: {conversation_id}")
    if payload.title is not None:
        conv.title = payload.title
    if payload.status is not None:
        conv.status = payload.status
    await session.commit()
    return ok({"id": conv.id, "title": conv.title, "status": conv.status})


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int, session: AsyncSession = Depends(db_session)):
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise NotFoundError(f"会话不存在: {conversation_id}")
    from sqlalchemy import delete as sa_delete

    await session.execute(sa_delete(Message).where(Message.conversation_id == conversation_id))
    await session.execute(
        sa_delete(AgentStep).where(
            AgentStep.run_id.in_(select(AgentRun.id).where(AgentRun.conversation_id == conversation_id))
        )
    )
    await session.execute(sa_delete(AgentRun).where(AgentRun.conversation_id == conversation_id))
    await session.delete(conv)
    await session.commit()
    return ok({"deleted": conversation_id})


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: int, session: AsyncSession = Depends(db_session)):
    messages = await conv_svc.list_messages(session, conversation_id, limit=200)
    return ok(
        [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "citations": m.citations,
                "token_in": m.token_in,
                "token_out": m.token_out,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ]
    )


@router.get("/runs")
async def list_runs(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(db_session),
):
    total = int((await session.execute(select(func.count(AgentRun.id)))).scalar_one())
    rows = (
        await session.execute(
            select(AgentRun).order_by(AgentRun.id.desc()).offset((page - 1) * size).limit(size)
        )
    ).scalars().all()
    return ok(
        {
            "items": [_run_dict(r) for r in rows],
            "total": total,
            "page": page,
            "size": size,
        }
    )


@router.get("/runs/{run_id}")
async def get_run(run_id: int, session: AsyncSession = Depends(db_session)):
    run = await session.get(AgentRun, run_id)
    if run is None:
        raise NotFoundError(f"运行记录不存在: {run_id}")
    steps = (
        await session.execute(
            select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.seq, AgentStep.id)
        )
    ).scalars().all()
    data = _run_dict(run)
    data["steps"] = [
        {
            "seq": s.seq,
            "node": s.node,
            "thought": s.thought,
            "action": s.action,
            "action_input": s.action_input,
            "observation": s.observation,
            "status": s.status,
            "latency_ms": s.latency_ms,
        }
        for s in steps
    ]
    return ok(data)


def _run_dict(run: AgentRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "conversation_id": run.conversation_id,
        "trace_id": run.trace_id,
        "query": run.query,
        "intent": run.intent,
        "plan": run.plan,
        "status": run.status,
        "step_count": run.step_count,
        "tool_call_count": run.tool_call_count,
        "prompt_tokens": run.prompt_tokens,
        "completion_tokens": run.completion_tokens,
        "duration_ms": run.duration_ms,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }
