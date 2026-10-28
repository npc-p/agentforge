"""数据域模型：数据集、样本、质检记录、版本快照。

对应 JD「智能体训练数据全流程：采集、清洗、标注、质检」——四张表构成一条
可回溯的流水线：样本状态机驱动流转，质检记录逐条留痕，版本快照冻结质量指标。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class Dataset(Base, PKMixin, TimestampMixin):
    """数据集：task_type 决定导出格式与质检规则集。"""

    __tablename__ = "datasets"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, default="sft")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    spec: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DatasetItem(Base, PKMixin, TimestampMixin):
    """样本：status 状态机 raw→cleaned→labeled→passed/rejected。

    dedup_hash 为 SimHash 十六进制串，用于近重复检测；同一数据集内建索引。
    """

    __tablename__ = "dataset_items"
    __table_args__ = (
        Index("idx_item_ds", "dataset_id", "status"),
        Index("idx_item_hash", "dataset_id", "dedup_hash"),
    )

    dataset_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False, default="manual")
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cleaned: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    label: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="raw")
    split: Mapped[str] = mapped_column(String(8), nullable=False, default="train")
    dedup_hash: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    quality_score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    reject_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


class QCRecord(Base, PKMixin):
    """质检记录：一条规则一次判定，保留原始证据便于人工复核。"""

    __tablename__ = "qc_records"
    __table_args__ = (Index("idx_qc_item", "item_id"), Index("idx_qc_ds", "dataset_id", "passed"))

    dataset_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rule: Mapped[str] = mapped_column(String(64), nullable=False)
    passed: Mapped[bool] = mapped_column(nullable=False, default=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="error")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class DatasetVersion(Base, PKMixin):
    """数据集版本快照：冻结样本数与质量指标，支撑效果对比与回滚。"""

    __tablename__ = "dataset_versions"
    __table_args__ = (Index("idx_ver_ds", "dataset_id", "version"),)

    dataset_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pass_rate: Mapped[float] = mapped_column(nullable=False, default=0.0)
    quality: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    export_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    export_format: Mapped[str] = mapped_column(String(16), nullable=False, default="jsonl")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
