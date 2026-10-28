"""评测域模型：套件、用例、运行、单例结果。

指标口径统一存放在 EvalRun.metrics，便于横向对比不同版本/参数的效果。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class EvalSuite(Base, PKMixin, TimestampMixin):
    """评测套件：type 决定执行器与指标集。

    type=retrieval 检索评测（hit_rate / MRR / recall）
    type=agent     端到端评测（任务完成率 / 工具成功率 / 平均步数）
    """

    __tablename__ = "eval_suites"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="agent")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class EvalCase(Base, PKMixin):
    __tablename__ = "eval_cases"
    __table_args__ = (Index("idx_case_suite", "suite_id", "id"),)

    suite_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input: Mapped[str] = mapped_column(Text, nullable=False)
    expected: Mapped[str | None] = mapped_column(Text, nullable=True)
    expect_tools: Mapped[list | None] = mapped_column(JSON, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class EvalRun(Base, PKMixin):
    __tablename__ = "eval_runs"
    __table_args__ = (Index("idx_run_suite", "suite_id", "id"),)

    suite_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, default="-")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    case_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    case_passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_p95_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EvalCaseResult(Base, PKMixin):
    __tablename__ = "eval_case_results"
    __table_args__ = (Index("idx_res_run", "run_id", "id"),)

    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    case_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    passed: Mapped[bool] = mapped_column(nullable=False, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
