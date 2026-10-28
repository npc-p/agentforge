"""工具 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ToolOut(BaseModel):
    id: int | None = None
    name: str
    display_name: str
    description: str
    params_schema: dict[str, Any]
    category: str
    handler: str = "builtin"
    timeout_s: float = 20.0
    enabled: bool = True
    call_count: int = 0
    success_count: int = 0
    tags: list[str] = []


class ToolToggle(BaseModel):
    enabled: bool


class ToolInvokeRequest(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict, description="工具入参，需符合 params_schema")


class ToolInvokeResult(BaseModel):
    tool: str
    ok: bool
    data: Any = None
    error: str | None = None
    latency_ms: float
    retries: int = 0
    observation: str = ""


class ToolInvocationOut(BaseModel):
    id: int
    run_id: int | None = None
    tool_name: str
    args: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    status: str
    error: str | None = None
    latency_ms: int
    retries: int
    created_at: datetime | None = None
