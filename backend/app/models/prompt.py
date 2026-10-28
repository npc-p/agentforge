"""Prompt 模板：支持版本化与灰度启用。

对应 JD「Prompt 工程」——模板不写死在代码里，改为可版本化的数据，
使 A/B 对比与快速回滚成为常规操作。
"""

from __future__ import annotations

from sqlalchemy import Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class PromptTemplate(Base, PKMixin, TimestampMixin):
    __tablename__ = "prompt_templates"
    __table_args__ = (
        Index("idx_prompt_scene", "scene", "is_active"),
        Index("idx_prompt_name_ver", "name", "version", unique=True),
    )

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    scene: Mapped[str] = mapped_column(String(32), nullable=False, default="chat")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list | None] = mapped_column(JSON, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
