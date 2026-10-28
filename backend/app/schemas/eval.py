"""评测 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SuiteCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    type: Literal["retrieval", "agent"] = "agent"
    description: str = Field("", max_length=2000)
    config: dict[str, Any] | None = None


class SuiteOut(BaseModel):
    id: int
    name: str
    type: str
    description: str
    config: dict[str, Any] | None = None
    case_count: int
    created_at: datetime | None = None


class CaseIn(BaseModel):
    input: str = Field(..., min_length=1)
    expected: str | None = Field(None, description="期望关键词，多个用 | 分隔")
    expect_tools: list[str] | None = Field(None, description="期望被调用的工具名")
    meta: dict[str, Any] | None = None


class CasesIngest(BaseModel):
    cases: list[CaseIn] = Field(..., min_length=1, max_length=200)


class CaseOut(BaseModel):
    id: int
    suite_id: int
    input: str
    expected: str | None = None
    expect_tools: list[str] | None = None
    meta: dict[str, Any] | None = None


class RunRequest(BaseModel):
    top_k: int = Field(5, ge=1, le=20)


class RunOut(BaseModel):
    run_id: int
    suite_id: int
    suite_type: str
    status: str
    metrics: dict[str, Any] | None = None
    case_total: int
    case_passed: int
    error: str | None = None


class ReportOut(BaseModel):
    run_id: int
    suite_id: int
    status: str
    metrics: dict[str, Any]
    case_total: int
    case_passed: int
    latency_p95_ms: int
    started_at: str | None = None
    finished_at: str | None = None
    failures: list[dict[str, Any]] = []
