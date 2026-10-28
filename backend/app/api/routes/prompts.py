"""Prompt 模板接口：版本化维护，支持启用切换。

约定：同名模板新增即产生 version+1 的新行，旧版本保留但不启用，
这样任意一次「模板调优」都能回滚，也能对比不同版本的效果。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session
from app.core.exceptions import NotFoundError
from app.core.response import ok
from app.models.prompt import PromptTemplate
from app.schemas.prompt import PromptCreate, PromptUpdate

router = APIRouter(tags=["prompts"])


def _to_dict(row: PromptTemplate) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "scene": row.scene,
        "description": row.description,
        "content": row.content,
        "variables": row.variables,
        "version": row.version,
        "is_active": row.is_active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/prompts")
async def list_prompts(
    scene: str | None = Query(None), session: AsyncSession = Depends(db_session)
):
    stmt = select(PromptTemplate).order_by(PromptTemplate.name, PromptTemplate.version.desc())
    if scene:
        stmt = stmt.where(PromptTemplate.scene == scene)
    rows = (await session.execute(stmt)).scalars().all()
    return ok([_to_dict(r) for r in rows])


@router.post("/prompts")
async def create_prompt(payload: PromptCreate, session: AsyncSession = Depends(db_session)):
    latest = (
        await session.execute(
            select(PromptTemplate)
            .where(PromptTemplate.name == payload.name)
            .order_by(PromptTemplate.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    version = (latest.version + 1) if latest else 1
    if payload.is_active:
        await session.execute(
            update(PromptTemplate)
            .where(PromptTemplate.name == payload.name)
            .values(is_active=False)
        )

    row = PromptTemplate(
        name=payload.name,
        scene=payload.scene,
        description=payload.description,
        content=payload.content,
        variables=payload.variables,
        version=version,
        is_active=payload.is_active,
    )
    session.add(row)
    await session.commit()
    return ok({"id": row.id, "name": row.name, "version": version})


@router.get("/prompts/{prompt_id}")
async def get_prompt(prompt_id: int, session: AsyncSession = Depends(db_session)):
    row = await session.get(PromptTemplate, prompt_id)
    if row is None:
        raise NotFoundError(f"模板不存在: {prompt_id}")
    return ok(_to_dict(row))


@router.patch("/prompts/{prompt_id}")
async def update_prompt(
    prompt_id: int, payload: PromptUpdate, session: AsyncSession = Depends(db_session)
):
    row = await session.get(PromptTemplate, prompt_id)
    if row is None:
        raise NotFoundError(f"模板不存在: {prompt_id}")
    for field in ("description", "content", "variables", "is_active"):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value)
    await session.commit()
    # updated_at 由数据库 onupdate 生成，提交后该属性处于过期状态；
    # 异步会话中直接读取会触发同步惰性加载并抛 MissingGreenlet，必须先显式 refresh。
    await session.refresh(row)
    return ok(_to_dict(row))


@router.post("/prompts/{prompt_id}/activate")
async def activate_prompt(prompt_id: int, session: AsyncSession = Depends(db_session)):
    row = await session.get(PromptTemplate, prompt_id)
    if row is None:
        raise NotFoundError(f"模板不存在: {prompt_id}")
    await session.execute(
        update(PromptTemplate)
        .where(PromptTemplate.name == row.name)
        .values(is_active=False)
    )
    row.is_active = True
    await session.commit()
    await session.refresh(row)
    return ok({"id": row.id, "name": row.name, "version": row.version, "is_active": True})
