"""对话域模型：会话、消息、Agent 运行记录与执行轨迹。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class Conversation(Base, PKMixin, TimestampMixin):
    """会话：一次连续交互的容器。"""

    __tablename__ = "conversations"
    __table_args__ = (Index("idx_conv_status", "status", "updated_at"),)

    title: Mapped[str] = mapped_column(String(128), nullable=False, default="新会话")
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="web")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    knowledge_base_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Message(Base, PKMixin):
    """消息：user / assistant / tool / system 四类角色。"""

    __tablename__ = "messages"
    __table_args__ = (
        Index("idx_msg_conv", "conversation_id", "id"),
        Index("idx_msg_run", "run_id"),
    )

    conversation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    citations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    token_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class AgentRun(Base, PKMixin):
    """单次 Agent 运行：承载规划结果、用量与耗时，用于可观测与复盘。"""

    __tablename__ = "agent_runs"
    __table_args__ = (Index("idx_run_conv", "conversation_id", "id"),)

    conversation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="-")
    query: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentStep(Base, PKMixin):
    """执行轨迹：每个状态机节点的输入输出，支撑链路追踪与问题排查。"""

    __tablename__ = "agent_steps"
    __table_args__ = (Index("idx_step_run", "run_id", "seq"),)

    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    node: Mapped[str] = mapped_column(String(32), nullable=False)
    thought: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_input: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
