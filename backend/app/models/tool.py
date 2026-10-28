"""工具域模型：工具注册表与调用流水。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class ToolDefinition(Base, PKMixin, TimestampMixin):
    """工具注册表。

    handler 区分实现来源：
    - builtin：随代码发布的 Python 函数（name 与注册中心 key 对应）
    - http：第三方 HTTP API，由 config 描述 method/url/headers/参数映射
    """

    __tablename__ = "tools"
    __table_args__ = (Index("idx_tool_enabled", "enabled"),)

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    params_schema: Mapped[dict] = mapped_column(JSON, nullable=False)
    handler: Mapped[str] = mapped_column(String(16), nullable=False, default="builtin")
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="general")
    timeout_s: Mapped[float] = mapped_column(nullable=False, default=20.0)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ToolInvocation(Base, PKMixin):
    """工具调用流水：幂等键 + 重试次数 + 耗时，支撑审计与性能分析。"""

    __tablename__ = "tool_invocations"
    __table_args__ = (
        Index("idx_inv_run", "run_id"),
        Index("idx_inv_tool", "tool_name", "id"),
        Index("idx_inv_idem", "idempotency_key", unique=True),
    )

    run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    args: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
