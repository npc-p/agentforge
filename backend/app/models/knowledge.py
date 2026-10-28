"""知识库域模型：知识库、文档、切片。

设计要点：MySQL 是切片原文与元数据的唯一真相源，向量库只保存可重建的索引，
因此向量库损坏或更换后端时，只需按 chunk.vector_id 重建即可。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class KnowledgeBase(Base, PKMixin, TimestampMixin):
    __tablename__ = "knowledge_bases"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    embed_provider: Mapped[str] = mapped_column(String(16), nullable=False, default="hashing")
    embed_model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    embed_dim: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    doc_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")


class Document(Base, PKMixin, TimestampMixin):
    """文档：status 状态机 pending→parsing→chunking→embedding→ready/failed。"""

    __tablename__ = "documents"
    __table_args__ = (
        Index("idx_doc_kb", "kb_id", "id"),
        Index("idx_doc_sha", "sha256"),
    )

    kb_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    ext: Mapped[str] = mapped_column(String(16), nullable=False, default="txt")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Chunk(Base, PKMixin):
    __tablename__ = "chunks"
    __table_args__ = (
        Index("idx_chunk_doc", "doc_id", "seq"),
        Index("idx_chunk_kb", "kb_id"),
    )

    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kb_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vector_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
