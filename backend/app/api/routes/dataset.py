"""数据集接口：样本入湖、分阶段执行、统计、导出、版本。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session
from app.core.exceptions import NotFoundError, ValidationError
from app.core.response import ok
from app.models.dataset import Dataset, DatasetItem, DatasetVersion, QCRecord
from app.pipeline import service as pipeline
from app.schemas.dataset import DatasetCreate, ExportRequest, ItemsIngest, PipelineRequest

router = APIRouter(tags=["dataset"])


@router.get("/datasets")
async def list_datasets(session: AsyncSession = Depends(db_session)):
    rows = (await session.execute(select(Dataset).order_by(Dataset.id.desc()))).scalars().all()
    return ok(
        [
            {
                "id": d.id,
                "name": d.name,
                "task_type": d.task_type,
                "description": d.description,
                "status": d.status,
                "item_count": d.item_count,
                "passed_count": d.passed_count,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in rows
        ]
    )


@router.post("/datasets")
async def create_dataset(payload: DatasetCreate, session: AsyncSession = Depends(db_session)):
    exists = (
        await session.execute(select(Dataset).where(Dataset.name == payload.name))
    ).scalar_one_or_none()
    if exists:
        raise ValidationError(f"数据集名称已存在: {payload.name}")
    dataset = Dataset(
        name=payload.name,
        task_type=payload.task_type,
        description=payload.description,
        spec=payload.spec,
    )
    session.add(dataset)
    await session.commit()
    return ok({"id": dataset.id, "name": dataset.name})


@router.post("/datasets/{dataset_id}/items")
async def ingest_items(
    dataset_id: int, payload: ItemsIngest, session: AsyncSession = Depends(db_session)
):
    result = await pipeline.add_items(
        session, dataset_id, payload.items, source=payload.source, split=payload.split
    )
    return ok(result)


@router.get("/datasets/{dataset_id}/items")
async def list_items(
    dataset_id: int,
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    session: AsyncSession = Depends(db_session),
):
    stmt = select(DatasetItem).where(DatasetItem.dataset_id == dataset_id)
    count_stmt = select(func.count(DatasetItem.id)).where(DatasetItem.dataset_id == dataset_id)
    if status:
        stmt = stmt.where(DatasetItem.status == status)
        count_stmt = count_stmt.where(DatasetItem.status == status)

    total = int((await session.execute(count_stmt)).scalar_one())
    rows = (
        await session.execute(
            stmt.order_by(DatasetItem.id.desc()).offset((page - 1) * size).limit(size)
        )
    ).scalars().all()

    return ok(
        {
            "items": [
                {
                    "id": i.id,
                    "status": i.status,
                    "split": i.split,
                    "source": i.source,
                    "raw": i.raw,
                    "cleaned": i.cleaned,
                    "label": i.label,
                    "quality_score": i.quality_score,
                    "reject_reason": i.reject_reason,
                    "dedup_hash": i.dedup_hash,
                }
                for i in rows
            ],
            "total": total,
            "page": page,
            "size": size,
        }
    )


@router.post("/datasets/{dataset_id}/pipeline")
async def run_pipeline(
    dataset_id: int, payload: PipelineRequest | None = None, session: AsyncSession = Depends(db_session)
):
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        raise NotFoundError(f"数据集不存在: {dataset_id}")

    stages = (payload.stages if payload else None) or ["clean", "dedup", "label", "qc"]
    handlers = {
        "clean": pipeline.stage_clean,
        "dedup": pipeline.stage_dedup,
        "label": pipeline.stage_label,
        "qc": pipeline.stage_qc,
    }

    results = {}
    for stage in stages:
        if stage not in handlers and stage != "groundedness":
            raise ValidationError(f"未知阶段: {stage}")
        if stage == "label":
            results[stage] = await pipeline.stage_label(
                session, dataset_id, limit=(payload.label_limit if payload else None)
            )
        elif stage == "groundedness":
            # 该阶段会逐条调用模型，默认不进入全量流水线，需显式指定
            results[stage] = await pipeline.stage_groundedness(
                session,
                dataset_id,
                limit=(payload.groundedness_limit if payload else None),
                threshold=(payload.groundedness_threshold if payload else None),
            )
        else:
            results[stage] = await handlers[stage](session, dataset_id)

    results["stats"] = await pipeline.dataset_stats(session, dataset_id)
    return ok(results)


@router.get("/datasets/{dataset_id}/stats")
async def stats(dataset_id: int, session: AsyncSession = Depends(db_session)):
    return ok(await pipeline.dataset_stats(session, dataset_id))


@router.get("/datasets/{dataset_id}/qc")
async def qc_records(
    dataset_id: int, limit: int = Query(100, ge=1, le=500), session: AsyncSession = Depends(db_session)
):
    rows = (
        await session.execute(
            select(QCRecord)
            .where(QCRecord.dataset_id == dataset_id)
            .order_by(QCRecord.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return ok(
        [
            {
                "id": r.id,
                "item_id": r.item_id,
                "rule": r.rule,
                "passed": r.passed,
                "severity": r.severity,
                "detail": r.detail,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    )


@router.post("/datasets/{dataset_id}/export")
async def export(
    dataset_id: int, payload: ExportRequest, session: AsyncSession = Depends(db_session)
):
    return ok(
        await pipeline.export_dataset(
            session,
            dataset_id,
            fmt=payload.format,
            split=payload.split,
            only_passed=payload.only_passed,
        )
    )


@router.get("/datasets/{dataset_id}/versions")
async def versions(dataset_id: int, session: AsyncSession = Depends(db_session)):
    rows = (
        await session.execute(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset_id)
            .order_by(DatasetVersion.version.desc())
        )
    ).scalars().all()
    return ok(
        [
            {
                "id": v.id,
                "version": v.version,
                "item_count": v.item_count,
                "pass_rate": v.pass_rate,
                "quality": v.quality,
                "export_path": v.export_path,
                "export_format": v.export_format,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in rows
        ]
    )
