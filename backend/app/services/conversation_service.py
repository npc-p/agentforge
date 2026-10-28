"""会话服务：会话与消息的读写，以及历史消息到模型消息的转换。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.llm.base import ChatMessage
from app.models.conversation import Conversation, Message


async def create_conversation(
    session: AsyncSession, *, title: str = "新会话", kb_id: int | None = None, channel: str = "api"
) -> Conversation:
    conv = Conversation(title=title[:128], channel=channel, knowledge_base_id=kb_id)
    session.add(conv)
    await session.commit()
    return conv


async def get_or_create(
    session: AsyncSession, conversation_id: int | None, *, kb_id: int | None = None
) -> Conversation:
    if conversation_id:
        conv = await session.get(Conversation, conversation_id)
        if conv is None:
            raise NotFoundError(f"会话不存在: {conversation_id}")
        if kb_id and conv.knowledge_base_id != kb_id:
            conv.knowledge_base_id = kb_id
            await session.commit()
        return conv
    return await create_conversation(session, kb_id=kb_id)


async def add_message(
    session: AsyncSession,
    conversation_id: int,
    *,
    role: str,
    content: str,
    run_id: int | None = None,
    citations: list[dict[str, Any]] | None = None,
) -> Message:
    msg = Message(
        conversation_id=conversation_id,
        run_id=run_id,
        role=role,
        content=content,
        citations=citations or None,
    )
    session.add(msg)
    if conversation_id:
        conv = await session.get(Conversation, conversation_id)
        if conv is not None and conv.title in ("新会话", "", None) and role == "user":
            conv.title = content[:40]
    await session.commit()
    return msg


async def list_messages(
    session: AsyncSession, conversation_id: int, *, limit: int = 100
) -> list[Message]:
    rows = (
        await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(reversed(rows))


async def history_as_chat(
    session: AsyncSession, conversation_id: int, *, limit: int = 6
) -> list[ChatMessage]:
    messages = await list_messages(session, conversation_id, limit=limit * 2)
    history: list[ChatMessage] = []
    for msg in messages[-limit:]:
        if msg.role in ("user", "assistant") and msg.content:
            history.append(ChatMessage(role=msg.role, content=msg.content))
    return history


async def list_conversations(
    session: AsyncSession, *, page: int = 1, size: int = 20
) -> tuple[list[dict[str, Any]], int]:
    total = int(
        (await session.execute(select(func.count(Conversation.id)))).scalar_one()
    )
    rows = (
        await session.execute(
            select(Conversation)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).scalars().all()

    counts = dict(
        (
            await session.execute(
                select(Message.conversation_id, func.count(Message.id))
                .where(Message.conversation_id.in_([c.id for c in rows] or [0]))
                .group_by(Message.conversation_id)
            )
        ).all()
    )

    items = [
        {
            "id": c.id,
            "title": c.title,
            "channel": c.channel,
            "status": c.status,
            "knowledge_base_id": c.knowledge_base_id,
            "message_count": int(counts.get(c.id, 0)),
            "created_at": c.created_at,
            "updated_at": c.updated_at,
        }
        for c in rows
    ]
    return items, total
