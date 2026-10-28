"""评测接口：套件、用例、执行、报告。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session
from app.core.exceptions import NotFoundError, ValidationError
from app.core.response import ok
from app.evaluation import runner
from app.models.evaluation import EvalCase, EvalRun, EvalSuite
from app.schemas.eval import CasesIngest, RunRequest, SuiteCreate

router = APIRouter(tags=["evaluation"])


@router.get("/eval/suites")
async def list_suites(session: AsyncSession = Depends(db_session)):
    rows = (await session.execute(select(EvalSuite).order_by(EvalSuite.id))).scalars().all()
    return ok(
        [
            {
                "id": s.id,
                "name": s.name,
                "type": s.type,
                "description": s.description,
                "config": s.config,
                "case_count": s.case_count,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in rows
        ]
    )


@router.post("/eval/suites")
async def create_suite(payload: SuiteCreate, session: AsyncSession = Depends(db_session)):
    exists = (
        await session.execute(select(EvalSuite).where(EvalSuite.name == payload.name))
    ).scalar_one_or_none()
    if exists:
        raise ValidationError(f"套件名称已存在: {payload.name}")
    suite = EvalSuite(
        name=payload.name, type=payload.type, description=payload.description, config=payload.config
    )
    session.add(suite)
    await session.commit()
    return ok({"id": suite.id, "name": suite.name, "type": suite.type})


@router.post("/eval/suites/{suite_id}/cases")
async def add_cases(
    suite_id: int, payload: CasesIngest, session: AsyncSession = Depends(db_session)
):
    suite = await session.get(EvalSuite, suite_id)
    if suite is None:
        raise NotFoundError(f"套件不存在: {suite_id}")

    session.add_all(
        [
            EvalCase(
                suite_id=suite_id,
                input=case.input,
                expected=case.expected,
                expect_tools=case.expect_tools,
                meta=case.meta,
            )
            for case in payload.cases
        ]
    )
    await session.flush()
    suite.case_count = int(
        (
            await session.execute(
                select(func.count(EvalCase.id)).where(EvalCase.suite_id == suite_id)
            )
        ).scalar_one()
    )
    await session.commit()
    return ok({"suite_id": suite_id, "case_count": suite.case_count})


@router.get("/eval/suites/{suite_id}/cases")
async def list_cases(suite_id: int, session: AsyncSession = Depends(db_session)):
    rows = (
        await session.execute(
            select(EvalCase).where(EvalCase.suite_id == suite_id).order_by(EvalCase.id)
        )
    ).scalars().all()
    return ok(
        [
            {
                "id": c.id,
                "suite_id": c.suite_id,
                "input": c.input,
                "expected": c.expected,
                "expect_tools": c.expect_tools,
                "meta": c.meta,
            }
            for c in rows
        ]
    )


@router.post("/eval/suites/{suite_id}/run")
async def run_suite(
    suite_id: int, payload: RunRequest | None = None, session: AsyncSession = Depends(db_session)
):
    top_k = payload.top_k if payload else 5
    result = await runner.run_suite(session, suite_id, top_k=top_k)
    return ok(
        {
            "run_id": result["run_id"],
            "suite_id": result["suite_id"],
            "suite_type": result["suite_type"],
            "status": result["status"],
            "metrics": result["metrics"],
            "case_total": result["case_total"],
            "case_passed": result["case_passed"],
            "error": result["error"],
        }
    )


@router.get("/eval/runs")
async def list_runs(
    suite_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(db_session),
):
    stmt = select(EvalRun)
    count_stmt = select(func.count(EvalRun.id))
    if suite_id:
        stmt = stmt.where(EvalRun.suite_id == suite_id)
        count_stmt = count_stmt.where(EvalRun.suite_id == suite_id)
    total = int((await session.execute(count_stmt)).scalar_one())
    rows = (
        await session.execute(stmt.order_by(EvalRun.id.desc()).offset((page - 1) * size).limit(size))
    ).scalars().all()
    return ok(
        {
            "items": [
                {
                    "run_id": r.id,
                    "suite_id": r.suite_id,
                    "status": r.status,
                    "metrics": r.metrics,
                    "case_total": r.case_total,
                    "case_passed": r.case_passed,
                    "latency_p95_ms": r.latency_p95_ms,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "size": size,
        }
    )


@router.get("/eval/runs/{run_id}/report")
async def get_report(run_id: int, session: AsyncSession = Depends(db_session)):
    return ok(await runner.report(session, run_id))
