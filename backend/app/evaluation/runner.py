"""评测执行器。

两类套件共用同一套落库与聚合逻辑，只在「单例执行」上分叉：
- retrieval：只跑检索，验证 RAG 召回质量，成本低、可高频回归；
- agent    ：跑完整状态机，验证任务完成度与工具调用正确性，成本高、按版本执行。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import get_engine
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.evaluation.judge import judge_answer
from app.evaluation.metrics import aggregate, answer_scores, retrieval_scores
from app.llm.router import get_gateway
from app.models.evaluation import EvalCase, EvalCaseResult, EvalRun, EvalSuite
from app.rag.service import search_knowledge

logger = get_logger(__name__)


async def run_suite(session: AsyncSession, suite_id: int, *, top_k: int = 5) -> dict[str, Any]:
    suite = await session.get(EvalSuite, suite_id)
    if suite is None:
        raise NotFoundError(f"评测套件不存在: {suite_id}")

    cases = list(
        (
            await session.execute(
                select(EvalCase).where(EvalCase.suite_id == suite_id).order_by(EvalCase.id)
            )
        )
        .scalars()
        .all()
    )
    if not cases:
        raise NotFoundError("评测套件内没有用例")

    run = EvalRun(
        suite_id=suite_id,
        status="running",
        config={"top_k": top_k, "suite_type": suite.type},
        case_total=len(cases),
    )
    session.add(run)
    await session.commit()

    results: list[dict[str, Any]] = []
    use_judge = bool((suite.config or {}).get("judge"))
    try:
        for case in cases:
            single = await _run_case(suite, case, top_k, use_judge=use_judge)
            results.append(single)
            session.add(
                EvalCaseResult(
                    run_id=run.id,
                    case_id=case.id,
                    output=(single.get("output") or "")[:60000],
                    scores=single.get("scores"),
                    passed=bool(single.get("passed")),
                    latency_ms=int(single.get("latency_ms", 0)),
                    error=single.get("error"),
                )
            )
            await session.commit()

        metrics = aggregate(results)
        run.status = "succeeded"
        run.metrics = metrics
        run.case_passed = int(metrics.get("passed", 0))
        run.latency_p95_ms = int(metrics.get("latency_p95_ms", 0))
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = str(exc)[:500]
        logger.exception("评测执行失败 suite=%s", suite_id)
    finally:
        run.finished_at = datetime.now()
        await session.commit()

    return {
        "run_id": run.id,
        "suite_id": suite_id,
        "suite_type": suite.type,
        "status": run.status,
        "metrics": run.metrics,
        "case_total": run.case_total,
        "case_passed": run.case_passed,
        "results": results,
        "error": run.error,
    }


async def _run_case(
    suite: EvalSuite, case: EvalCase, top_k: int, *, use_judge: bool = False
) -> dict[str, Any]:
    started = time.perf_counter()
    if suite.type == "retrieval":
        kb_id = (case.meta or {}).get("kb_id")
        try:
            hits = await search_knowledge(case.input, kb_id=kb_id, top_k=top_k)
            scores = retrieval_scores(hits, case.expected, top_k)
            latency = int((time.perf_counter() - started) * 1000)
            return {
                "case_id": case.id,
                "input": case.input,
                "output": " | ".join(f"#{idx + 1} {h['doc_name']}" for idx, h in enumerate(hits[:3])),
                "scores": scores,
                "passed": bool(scores["hit"]),
                "latency_ms": latency,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "case_id": case.id,
                "input": case.input,
                "output": "",
                "scores": {},
                "passed": False,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "error": str(exc)[:300],
            }

    # agent 套件
    kb_id = (case.meta or {}).get("kb_id")
    try:
        result = await get_engine().run_collect(case.input, kb_id=kb_id)
        scores = answer_scores(
            result.get("answer", ""),
            case.expected,
            case.expect_tools or [],
            result.get("tool_calls", []),
        )
        if use_judge:
            verdict = await judge_answer(
                get_gateway(),
                query=case.input,
                expected=case.expected,
                answer=result.get("answer", ""),
            )
            scores["judge"] = verdict
            # 关键词断言与 judge 都通过才算通过（宁可严，不可松）
            scores["passed"] = bool(scores["passed"]) and verdict["verdict"] == "correct"
        return {
            "case_id": case.id,
            "input": case.input,
            "output": result.get("answer", ""),
            "scores": scores,
            "passed": bool(scores["passed"]),
            "latency_ms": int(result.get("duration_ms", 0)),
            "error": result.get("error"),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "case_id": case.id,
            "input": case.input,
            "output": "",
            "scores": {},
            "passed": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error": str(exc)[:300],
        }


async def report(session: AsyncSession, run_id: int) -> dict[str, Any]:
    run = await session.get(EvalRun, run_id)
    if run is None:
        raise NotFoundError(f"评测运行不存在: {run_id}")

    results = list(
        (
            await session.execute(
                select(EvalCaseResult).where(EvalCaseResult.run_id == run_id).order_by(EvalCaseResult.id)
            )
        )
        .scalars()
        .all()
    )
    failures = [
        {
            "case_id": r.case_id,
            "passed": r.passed,
            "scores": r.scores,
            "error": r.error,
            "output": (r.output or "")[:300],
        }
        for r in results
        if not r.passed
    ]
    return {
        "run_id": run.id,
        "suite_id": run.suite_id,
        "status": run.status,
        "metrics": run.metrics or {},
        "case_total": run.case_total,
        "case_passed": run.case_passed,
        "latency_p95_ms": run.latency_p95_ms,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "failures": failures,
    }
