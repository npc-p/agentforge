"""知识库 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class KBCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field("", max_length=2000)
    chunk_size: int = Field(600, ge=100, le=2000)
    chunk_overlap: int = Field(80, ge=0, le=500)


class KBUpdate(BaseModel):
    description: str | None = Field(None, max_length=2000)
    chunk_size: int | None = Field(None, ge=100, le=2000)
    chunk_overlap: int | None = Field(None, ge=0, le=500)


class KBOut(BaseModel):
    id: int
    name: str
    description: str = ""
    embed_provider: str
    embed_model: str
    embed_dim: int
    chunk_size: int
    chunk_overlap: int
    doc_count: int
    chunk_count: int
    status: str
    created_at: datetime | None = None


class DocumentOut(BaseModel):
    id: int
    kb_id: int
    filename: str
    ext: str
    size_bytes: int
    status: str
    chunk_count: int
    char_count: int
    error: str | None = None
    meta: dict[str, Any] | None = None
    created_at: datetime | None = None


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    kb_id: int | None = None
    top_k: int = Field(5, ge=1, le=20)


class SearchHit(BaseModel):
    chunk_id: int
    doc_id: int
    doc_name: str
    kb_id: int
    content: str
    score: float
    vector_score: float = 0.0
    bm25_score: float = 0.0
    vector_rank: int | None = None
    bm25_rank: int | None = None
    seq: int = 0


class SearchResult(BaseModel):
    query: str
    hit_count: int
    latency_ms: float
    hits: list[SearchHit]


class TextIngest(BaseModel):
    filename: str = Field(..., max_length=255)
    text: str = Field(..., min_length=1)
    kb_id: int
