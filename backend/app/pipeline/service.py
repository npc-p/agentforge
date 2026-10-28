"""数据流水线编排：把五个阶段串成可重复执行、可回放的状态机。

样本状态机：raw → cleaned → labeled → passed / rejected
每个阶段只处理「上一阶段完成」的样本，重复执行是幂等的（已处理样本不再进入）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.llm.router import get_gateway
from app.models.dataset import Dataset, DatasetItem, DatasetVersion, QCRecord
from app.pipeline.base import Sample
from app.pipeline.cleaner import clean_sample, drop_reason
from app.pipeline.dedup import hamming, simhash
from app.pipeline.exporter import export_samples
from app.pipeline.groundedness import DEFAULT_SCORE_THRESHOLD, judge_sample
from app.pipeline.labeler import BATCH_SIZE, Labeler
from app.pipeline.qc import run_qc, summarize_verdicts

logger = get_logger(__name__)

DUPLICATE_THRESHOLD = 14  # 与 dedup._DEFAULT_THRESHOLD 保持一致，见该模块阈值标定说明


def _to_sample(item: DatasetItem) -> Sample:
    payload = item.cleaned or item.raw or {}
    sample = Sample.from_raw(payload)
    sample.meta = {**sample.meta, "label": item.label or {}}
    return sample


async def add_items(
    session: AsyncSession,
    dataset_id: int,
    raws: list[dict[str, Any]],
    *,
    source: str = "manual",
    split: str = "train",
) -> dict[str, Any]:
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        raise NotFoundError(f"数据集不存在: {dataset_id}")

    rows = [
        DatasetItem(dataset_id=dataset_id, source=source, raw=raw, split=split, status="raw")
        for raw in raws
    ]
    session.add_all(rows)
    await session.flush()
    dataset.item_count = int(
        (
            await session.execute(
                select(func.count(DatasetItem.id)).where(DatasetItem.dataset_id == dataset_id)
            )
        ).scalar_one()
    )
    await session.commit()
    return {"dataset_id": dataset_id, "added": len(rows), "item_count": dataset.item_count}


async def stage_clean(session: AsyncSession, dataset_id: int) -> dict[str, Any]:
    items = list(
        (
            await session.execute(
                select(DatasetItem).where(DatasetItem.dataset_id == dataset_id, DatasetItem.status == "raw")
            )
        )
        .scalars()
        .all()
    )
    changed = dropped = 0
    reasons: dict[str, int] = {}

    for item in items:
        sample = Sample.from_raw(item.raw or {})
        reason = drop_reason(sample)
        if reason:
            item.status = "rejected"
            item.reject_reason = reason
            item.quality_score = 0.0
            reasons[reason] = reasons.get(reason, 0) + 1
            dropped += 1
            continue

        cleaned, changes = clean_sample(sample)
        if changes:
            changed += 1
            cleaned.meta = {**cleaned.meta, "clean_changes": changes}
        item.cleaned = cleaned.to_dict()
        item.status = "cleaned"

    await session.commit()
    return {
        "stage": "clean",
        "total": len(items),
        "changed": changed,
        "dropped": dropped,
        "drop_reasons": reasons,
    }


async def stage_dedup(session: AsyncSession, dataset_id: int) -> dict[str, Any]:
    """近重复检测。

    幂等性要点：历史指纹只能来自「已通过本阶段」的样本（status != cleaned），
    绝不能用本批次自身的指纹做参照集合——否则二次执行时每条样本都会与自身比对
    而被整体误判为重复（曾出现 12 条样本全部被判 duplicate 的缺陷）。
    """
    existing = list(
        (
            await session.execute(
                select(DatasetItem.dedup_hash).where(
                    DatasetItem.dataset_id == dataset_id,
                    DatasetItem.dedup_hash != "",
                    DatasetItem.status != "cleaned",
                )
            )
        )
        .scalars()
        .all()
    )
    known = [int(h, 16) for h in existing if h]

    items = list(
        (
            await session.execute(
                select(DatasetItem).where(
                    DatasetItem.dataset_id == dataset_id, DatasetItem.status == "cleaned"
                ).order_by(DatasetItem.id)
            )
        )
        .scalars()
        .all()
    )

    duplicates = 0
    kept = 0
    for item in items:
        sample = _to_sample(item)
        fingerprint = simhash(sample.prompt_text + sample.output)
        if any(hamming(fingerprint, other) <= DUPLICATE_THRESHOLD for other in known):
            item.status = "rejected"
            item.reject_reason = "duplicate"
            # 重复样本不写指纹，避免其在下一次执行时反过来把原样本判为重复
            duplicates += 1
            continue
        known.append(fingerprint)
        item.dedup_hash = f"{fingerprint:016x}"
        kept += 1

    await session.commit()
    return {
        "stage": "dedup",
        "total": len(items),
        "duplicates": duplicates,
        "kept": kept,
        "threshold": DUPLICATE_THRESHOLD,
    }


async def stage_label(session: AsyncSession, dataset_id: int, *, limit: int | None = None) -> dict[str, Any]:
    stmt = (
        select(DatasetItem)
        .where(DatasetItem.dataset_id == dataset_id, DatasetItem.status == "cleaned")
        .order_by(DatasetItem.id)
    )
    if limit:
        stmt = stmt.limit(limit)
    items = list((await session.execute(stmt)).scalars().all())
    if not items:
        return {"stage": "label", "total": 0, "labeled": 0, "source": "skipped"}

    labeler = Labeler(get_gateway())
    from app.tools.registry import registry

    labeled = 0
    source = "rule"
    for start in range(0, len(items), BATCH_SIZE):
        batch = items[start : start + BATCH_SIZE]
        samples = [_to_sample(item) for item in batch]
        labels = await labeler.label_batch(samples, registry.names())
        for item, label in zip(batch, labels, strict=False):
            item.label = label
            item.status = "labeled"
            source = label.get("source", "rule")
            labeled += 1

    await session.commit()
    return {"stage": "label", "total": len(items), "labeled": labeled, "source": source}


async def stage_qc(session: AsyncSession, dataset_id: int) -> dict[str, Any]:
    items = list(
        (
            await session.execute(
                select(DatasetItem).where(
                    DatasetItem.dataset_id == dataset_id, DatasetItem.status == "labeled"
                )
            )
        )
        .scalars()
        .all()
    )

    verdicts = []
    passed = 0
    for item in items:
        sample = _to_sample(item)
        verdict = run_qc(sample)
        verdicts.append(verdict)

        for failure in verdict.failures:
            session.add(
                QCRecord(
                    dataset_id=dataset_id,
                    item_id=item.id,
                    rule=failure["rule"],
                    passed=False,
                    severity=failure["severity"],
                    detail=failure["detail"],
                )
            )
        item.quality_score = verdict.score
        item.status = "passed" if verdict.passed else "rejected"
        if not verdict.passed:
            item.reject_reason = ", ".join(f["rule"] for f in verdict.failures if f["severity"] == "error")[:255]
        else:
            passed += 1

    # 会话启用了 autoflush=False：统计前必须显式 flush，
    # 否则 COUNT 读到的是本事务内状态更新之前的旧数据（曾导致 passed_count 恒为 0）。
    await session.flush()

    dataset = await session.get(Dataset, dataset_id)
    if dataset is not None:
        dataset.passed_count = int(
            (
                await session.execute(
                    select(func.count(DatasetItem.id)).where(
                        DatasetItem.dataset_id == dataset_id, DatasetItem.status == "passed"
                    )
                )
            ).scalar_one()
        )
        dataset.status = "ready" if dataset.passed_count else "draft"

    await session.commit()
    summary = summarize_verdicts(verdicts)
    summary["stage"] = "qc"
    summary["passed_now"] = passed
    return summary


async def stage_groundedness(
    session: AsyncSession,
    dataset_id: int,
    *,
    limit: int | None = None,
    threshold: float | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """答案忠实度校验（基于知识库反查）。

    只处理 passed 样本；已判定过的样本默认跳过，保证重复调用幂等。
    contradicted 直接拒收并降分——这类样本是「格式合格但事实错误」，
    正是形式规则拦不住的那一类。
    """
    stmt = (
        select(DatasetItem)
        .where(DatasetItem.dataset_id == dataset_id, DatasetItem.status == "passed")
        .order_by(DatasetItem.id)
    )
    if limit:
        stmt = stmt.limit(limit)
    items = list((await session.execute(stmt)).scalars().all())

    gateway = get_gateway()
    counts = {"supported": 0, "contradicted": 0, "insufficient": 0}
    judged = skipped = 0

    for item in items:
        label = dict(item.label or {})
        if not force and label.get("groundedness"):
            skipped += 1
            continue

        result = await judge_sample(
            _to_sample(item), gateway, score_threshold=threshold or DEFAULT_SCORE_THRESHOLD
        )
        verdict = result["verdict"]
        counts[verdict] += 1
        judged += 1

        label["groundedness"] = verdict
        label["groundedness_reason"] = result["reason"]
        item.label = label
        session.add(
            QCRecord(
                dataset_id=dataset_id,
                item_id=item.id,
                rule="groundedness",
                passed=verdict == "supported",
                severity="error" if verdict == "contradicted" else "warning",
                detail=f"{verdict}: {result['reason']}",
            )
        )
        if verdict == "contradicted":
            item.status = "rejected"
            item.reject_reason = "groundedness_contradicted"
            item.quality_score = min(float(item.quality_score or 0.0), 0.3)

    dataset = await session.get(Dataset, dataset_id)
    if dataset is not None:
        await session.flush()
        dataset.passed_count = int(
            (
                await session.execute(
                    select(func.count(DatasetItem.id)).where(
                        DatasetItem.dataset_id == dataset_id, DatasetItem.status == "passed"
                    )
                )
            ).scalar_one()
        )
        dataset.status = "ready" if dataset.passed_count else "draft"

    await session.commit()
    return {
        "stage": "groundedness",
        "total": len(items),
        "judged": judged,
        "skipped": skipped,
        "verdicts": counts,
        "contradicted_rate": round(counts["contradicted"] / judged, 4) if judged else 0.0,
    }


async def run_full_pipeline(session: AsyncSession, dataset_id: int) -> dict[str, Any]:
    stages: dict[str, Any] = {}
    # 变量名不能叫 func：本模块用 sqlalchemy.func 做聚合，
    # 同名会让后续在同一函数里写 func.count() 时静默变成局部变量。
    for name, stage_fn in (
        ("clean", stage_clean),
        ("dedup", stage_dedup),
        ("label", stage_label),
        ("qc", stage_qc),
    ):
        stages[name] = await stage_fn(session, dataset_id)
        logger.info("数据流水线阶段完成 dataset=%s stage=%s", dataset_id, name)
    stages["stats"] = await dataset_stats(session, dataset_id)
    return stages


async def dataset_stats(session: AsyncSession, dataset_id: int) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(DatasetItem.status, func.count(DatasetItem.id)).where(
                DatasetItem.dataset_id == dataset_id
            ).group_by(DatasetItem.status)
        )
    ).all()
    by_status = {status: count for status, count in rows}
    total = sum(by_status.values())
    avg_score = (
        await session.execute(
            select(func.avg(DatasetItem.quality_score)).where(DatasetItem.dataset_id == dataset_id)
        )
    ).scalar()

    label_rows = (
        await session.execute(
            select(DatasetItem.label).where(
                DatasetItem.dataset_id == dataset_id, DatasetItem.status == "passed"
            )
        )
    ).scalars().all()
    intents: dict[str, int] = {}
    for label in label_rows:
        intent = (label or {}).get("intent", "unknown")
        intents[intent] = intents.get(intent, 0) + 1

    return {
        "dataset_id": dataset_id,
        "total": total,
        "by_status": by_status,
        "pass_rate": round(by_status.get("passed", 0) / total, 4) if total else 0.0,
        "avg_quality": round(float(avg_score or 0.0), 4),
        "intent_distribution": intents,
    }


async def export_dataset(
    session: AsyncSession,
    dataset_id: int,
    *,
    fmt: str = "chat_jsonl",
    split: str | None = None,
    only_passed: bool = True,
) -> dict[str, Any]:
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        raise NotFoundError(f"数据集不存在: {dataset_id}")

    stmt = select(DatasetItem).where(DatasetItem.dataset_id == dataset_id)
    if only_passed:
        stmt = stmt.where(DatasetItem.status == "passed")
    if split:
        stmt = stmt.where(DatasetItem.split == split)
    items = list((await session.execute(stmt.order_by(DatasetItem.id))).scalars().all())
    if not items:
        raise NotFoundError("没有可导出的样本（需先通过质检）")

    next_version = int(
        (
            await session.execute(
                select(func.max(DatasetVersion.version)).where(DatasetVersion.dataset_id == dataset_id)
            )
        ).scalar()
        or 0
    ) + 1

    samples = [_to_sample(item) for item in items]
    result = export_samples(samples, dataset_id=dataset_id, version=next_version, fmt=fmt)

    stats = await dataset_stats(session, dataset_id)
    session.add(
        DatasetVersion(
            dataset_id=dataset_id,
            version=next_version,
            item_count=result.count,
            pass_rate=stats["pass_rate"],
            quality=stats,
            export_path=result.path,
            export_format=fmt,
        )
    )
    await session.commit()

    return {
        "dataset_id": dataset_id,
        "version": next_version,
        "format": fmt,
        "split": split or "all",
        "count": result.count,
        "size_bytes": result.size_bytes,
        "path": result.path,
        "quality": stats,
    }
