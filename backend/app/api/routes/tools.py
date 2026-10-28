"""工具接口：列表、启停、在线调试、调用流水。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session
from app.core.response import ok
from app.models.tool import ToolInvocation
from app.schemas.tool import ToolInvokeRequest, ToolToggle
from app.services import tool_service
from app.tools.executor import get_executor
from app.tools.registry import registry

router = APIRouter(tags=["tools"])


@router.get("/tools")
async def list_tools(session: AsyncSession = Depends(db_session)):
    return ok(await tool_service.list_tools(session))


@router.get("/tools/schemas")
async def tool_schemas():
    """返回 OpenAI function calling 形态的完整工具描述，便于第三方接入。"""
    return ok({"count": len(registry.enabled()), "tools": registry.openai_schemas()})


@router.patch("/tools/{name}/enabled")
async def toggle_tool(name: str, payload: ToolToggle, session: AsyncSession = Depends(db_session)):
    return ok(await tool_service.set_enabled(session, name, payload.enabled))


@router.post("/tools/{name}/invoke")
async def invoke_tool(name: str, payload: ToolInvokeRequest, session: AsyncSession = Depends(db_session)):
    result = await get_executor().execute(name, payload.args, session=session)
    return ok(
        {
            "tool": result.tool,
            "ok": result.ok,
            "data": result.data,
            "error": result.error,
            "latency_ms": result.latency_ms,
            "retries": result.retries,
            "observation": result.to_observation(),
        }
    )


@router.get("/tools/invocations")
async def list_invocations(
    tool_name: str | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(db_session),
):
    stmt = select(ToolInvocation)
    count_stmt = select(func.count(ToolInvocation.id))
    if tool_name:
        stmt = stmt.where(ToolInvocation.tool_name == tool_name)
        count_stmt = count_stmt.where(ToolInvocation.tool_name == tool_name)

    total = int((await session.execute(count_stmt)).scalar_one())
    rows = (
        await session.execute(
            stmt.order_by(ToolInvocation.id.desc()).offset((page - 1) * size).limit(size)
        )
    ).scalars().all()
    return ok(
        {
            "items": [
                {
                    "id": r.id,
                    "run_id": r.run_id,
                    "tool_name": r.tool_name,
                    "args": r.args,
                    "result": r.result,
                    "status": r.status,
                    "error": r.error,
                    "latency_ms": r.latency_ms,
                    "retries": r.retries,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "size": size,
        }
    )
