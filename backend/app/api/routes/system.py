"""系统接口：健康检查、运行配置、平台总览。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session
from app.config import settings
from app.core.response import ok
from app.db.session import ping
from app.llm.router import get_gateway
from app.models.conversation import AgentRun, Conversation, Message
from app.models.dataset import Dataset, DatasetItem
from app.models.evaluation import EvalRun
from app.models.knowledge import Chunk, Document, KnowledgeBase
from app.models.tool import ToolDefinition
from app.rag.embedder import embedder_info
from app.rag.vector_store import get_vector_store

router = APIRouter(tags=["system"])


@router.get("/system/health")
async def health(session: AsyncSession = Depends(db_session)):
    db_ok = await ping()
    gateway = get_gateway()
    llm = gateway.health()

    try:
        store_health = get_vector_store().health()
    except Exception as exc:  # noqa: BLE001
        store_health = {"backend": settings.vector_store, "ok": False, "error": str(exc)[:200]}

    overall = "ok" if db_ok and store_health.get("ok") else "degraded"
    return ok(
        {
            "status": overall,
            "app": settings.app_name,
            "version": settings.app_version,
            "env": settings.env,
            "database": {
                "ok": db_ok,
                "dialect": settings.database_url.split("://")[0],
                # SQL 工具是否已使用独立只读账号（最小权限），便于在界面上确认防护等级
                "readonly_isolated": settings.readonly_isolated,
            },
            "vector_store": store_health,
            "embedder": embedder_info(),
            "llm": llm,
        }
    )


@router.get("/system/config")
async def config():
    """只暴露非敏感配置，密钥类字段一律不返回。"""
    gateway = get_gateway()
    return ok(
        {
            "app_name": settings.app_name,
            "version": settings.app_version,
            "env": settings.env,
            "llm": {
                "provider": gateway.provider.name,
                "model": gateway.provider.model,
                "mock_mode": gateway.is_mock,
                "ready": gateway.provider.ready,
                "max_steps": settings.agent_max_steps,
            },
            "embedder": embedder_info(),
            "vector_store": {"backend": settings.vector_store, "path": str(settings.chroma_path)},
            "rag": {
                "chunk_size": settings.rag_chunk_size,
                "chunk_overlap": settings.rag_chunk_overlap,
                "top_k": settings.rag_top_k,
                "vector_weight": settings.rag_vector_weight,
                "bm25_weight": settings.rag_bm25_weight,
            },
            "limits": {
                "max_upload_mb": settings.max_upload_mb,
                "agent_max_plan_tasks": settings.agent_max_plan_tasks,
                "tool_timeout_s": settings.agent_tool_timeout,
            },
        }
    )


@router.get("/system/overview")
async def overview(session: AsyncSession = Depends(db_session)):
    async def count(model) -> int:
        return int((await session.execute(select(func.count(model.id)))).scalar_one())

    avg_latency = (
        await session.execute(
            select(func.avg(AgentRun.duration_ms)).where(AgentRun.duration_ms > 0)
        )
    ).scalar()
    tool_ok = (
        await session.execute(
            select(func.count(AgentRun.id)).where(AgentRun.tool_call_count > 0)
        )
    ).scalar_one()

    return ok(
        {
            "conversations": await count(Conversation),
            "messages": await count(Message),
            "agent_runs": await count(AgentRun),
            "runs_with_tools": int(tool_ok),
            "avg_run_latency_ms": round(float(avg_latency or 0), 2),
            "knowledge_bases": await count(KnowledgeBase),
            "documents": await count(Document),
            "chunks": await count(Chunk),
            "datasets": await count(Dataset),
            "dataset_items": await count(DatasetItem),
            "tools": await count(ToolDefinition),
            "eval_runs": await count(EvalRun),
            "llm_mock_mode": get_gateway().is_mock,
        }
    )
