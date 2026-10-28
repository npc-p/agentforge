"""工具服务：注册表与数据库元数据的同步。

代码里的 ToolSpec 是实现的唯一真相源；数据库中的 tools 表承担三件事：
1. 记录启停状态与调用统计，使运营侧可不改代码灰度下线工具；
2. 为前端提供统一的列表与参数说明；
3. 保留 handler=builtin/http 的扩展位，为后续接入第三方 API 工具留口。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.tool import ToolDefinition
from app.tools.registry import registry

logger = get_logger(__name__)


async def sync_registry(session: AsyncSession) -> dict[str, int]:
    """把代码中的工具规格同步到数据库（幂等 upsert）。"""
    existing = {
        row.name: row
        for row in (await session.execute(select(ToolDefinition))).scalars().all()
    }
    created = updated = 0

    for spec in registry.all():
        row = existing.get(spec.name)
        if row is None:
            session.add(
                ToolDefinition(
                    name=spec.name,
                    display_name=spec.display_name,
                    description=spec.description,
                    params_schema=spec.params_schema,
                    handler=spec.source,
                    config={"timeout_s": spec.timeout_s, "max_retries": spec.max_retries},
                    category=spec.category,
                    timeout_s=spec.timeout_s,
                    enabled=spec.enabled,
                )
            )
            created += 1
        else:
            row.display_name = spec.display_name
            row.description = spec.description
            row.params_schema = spec.params_schema
            row.category = spec.category
            row.timeout_s = spec.timeout_s
            # 启停状态以数据库为准，不被代码默认值覆盖
            spec.enabled = row.enabled
            updated += 1

    await session.commit()
    logger.info("工具注册表同步完成: 新建 %s, 更新 %s", created, updated)
    return {"created": created, "updated": updated, "total": len(registry.all())}


async def list_tools(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (await session.execute(select(ToolDefinition).order_by(ToolDefinition.name))).scalars().all()
    by_name = {row.name: row for row in rows}
    result: list[dict[str, Any]] = []

    for spec in registry.all():
        row = by_name.get(spec.name)
        result.append(
            {
                "id": row.id if row else None,
                "name": spec.name,
                "display_name": spec.display_name,
                "description": spec.description,
                "params_schema": spec.params_schema,
                "category": spec.category,
                "handler": spec.source,
                "timeout_s": spec.timeout_s,
                "enabled": bool(row.enabled) if row else spec.enabled,
                "call_count": row.call_count if row else 0,
                "success_count": row.success_count if row else 0,
                "tags": spec.tags,
            }
        )
    return result


async def set_enabled(session: AsyncSession, name: str, enabled: bool) -> dict[str, Any]:
    row = (
        await session.execute(select(ToolDefinition).where(ToolDefinition.name == name))
    ).scalar_one_or_none()
    if row is None:
        # 首次调用先补齐
        await sync_registry(session)
        row = (
            await session.execute(select(ToolDefinition).where(ToolDefinition.name == name))
        ).scalar_one_or_none()
    if row is None:
        raise ValueError(f"工具不存在: {name}")

    row.enabled = enabled
    registry.set_enabled(name, enabled)
    await session.commit()
    return {"name": name, "enabled": enabled}
