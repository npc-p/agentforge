"""对话与 Agent 运行相关 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000, description="用户问题")
    conversation_id: int | None = Field(None, description="会话 ID，留空则新建会话")
    kb_id: int | None = Field(None, description="绑定的知识库 ID")
    stream: bool = Field(True, description="是否使用 SSE 流式返回")


class ConversationCreate(BaseModel):
    title: str = Field("新会话", max_length=128)
    knowledge_base_id: int | None = None


class ConversationUpdate(BaseModel):
    title: str | None = Field(None, max_length=128)
    status: str | None = None


class ConversationOut(BaseModel):
    id: int
    title: str
    channel: str
    status: str
    knowledge_base_id: int | None = None
    message_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    tool_calls: list[Any] | None = None
    citations: list[Any] | None = None
    token_in: int = 0
    token_out: int = 0
    latency_ms: int = 0
    created_at: datetime | None = None


class AgentStepOut(BaseModel):
    seq: int
    node: str
    thought: str | None = None
    action: str | None = None
    action_input: dict[str, Any] | None = None
    observation: str | None = None
    status: str
    latency_ms: int = 0


class AgentRunOut(BaseModel):
    id: int
    conversation_id: int
    trace_id: str
    query: str
    intent: str | None = None
    plan: dict[str, Any] | None = None
    status: str
    step_count: int = 0
    tool_call_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0
    error: str | None = None
    created_at: datetime | None = None


class ChatResult(BaseModel):
    conversation_id: int
    run_id: int
    answer: str
    citations: list[dict[str, Any]] = []
    tool_calls: list[str] = []
    steps: int = 0
    duration_ms: int = 0
    mock_mode: bool = False
