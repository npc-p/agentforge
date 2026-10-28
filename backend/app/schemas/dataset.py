"""数据集 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class DatasetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    task_type: Literal["sft", "rag_qa", "agent_trace"] = "sft"
    description: str = Field("", max_length=2000)
    spec: dict[str, Any] | None = None


class DatasetOut(BaseModel):
    id: int
    name: str
    task_type: str
    description: str
    status: str
    item_count: int
    passed_count: int
    created_at: datetime | None = None


class ItemIn(BaseModel):
    instruction: str = ""
    input: str = ""
    output: str = ""
    system: str = ""
    meta: dict[str, Any] | None = None


class ItemsIngest(BaseModel):
    items: list[dict[str, Any]] = Field(..., min_length=1, max_length=500)
    source: str = Field("manual", max_length=128)
    split: Literal["train", "val", "test"] = "train"


class ItemOut(BaseModel):
    id: int
    status: str
    split: str
    source: str
    raw: dict[str, Any] | None = None
    cleaned: dict[str, Any] | None = None
    label: dict[str, Any] | None = None
    quality_score: float
    reject_reason: str | None = None
    dedup_hash: str = ""


class PipelineRequest(BaseModel):
    stages: list[Literal["clean", "dedup", "label", "qc", "groundedness"]] | None = Field(
        None, description="留空表示执行 clean/dedup/label/qc；groundedness 需显式指定（有模型调用成本）"
    )
    label_limit: int | None = Field(None, ge=1, le=500)
    groundedness_limit: int | None = Field(None, ge=1, le=500)
    groundedness_threshold: float | None = Field(None, ge=0.0, le=1.0)


class ExportRequest(BaseModel):
    format: Literal["chat_jsonl", "sharegpt", "alpaca"] = "chat_jsonl"
    split: Literal["train", "val", "test"] | None = None
    only_passed: bool = True


class VersionOut(BaseModel):
    id: int
    version: int
    item_count: int
    pass_rate: float
    quality: dict[str, Any] | None = None
    export_path: str | None = None
    export_format: str
    created_at: datetime | None = None
