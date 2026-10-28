"""Prompt 模板 Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PromptCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    scene: str = Field("agent", max_length=32)
    description: str = Field("", max_length=500)
    content: str = Field(..., min_length=1)
    variables: list[str] | None = None
    is_active: bool = True


class PromptUpdate(BaseModel):
    description: str | None = None
    content: str | None = None
    variables: list[str] | None = None
    is_active: bool | None = None


class PromptOut(BaseModel):
    id: int
    name: str
    scene: str
    description: str
    content: str
    variables: list[str] | None = None
    version: int
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None
