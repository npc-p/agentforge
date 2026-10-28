"""只读数据库连接（供 SQL 工具使用）。

为什么单独一条连接：
SQL 工具接收的是模型生成的语句，属于不可信输入。只靠语句白名单/黑名单是"应用层
防护"，一旦规则有缺口（例如漏掉某个系统库）就会越权。最小权限账号是"数据库层防护"，
两者叠加才算收口：
- 数据库层：`agentforge_ro` 仅被授予 `agentforge.*` 的 SELECT；
- 应用层：表名白名单 + 危险片段黑名单 + 超时与行数限制。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_engine = None
_sessionmaker = None


def _ensure() -> async_sessionmaker:
    global _engine, _sessionmaker
    if _sessionmaker is None:
        _engine = create_async_engine(
            settings.readonly_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=3,
            max_overflow=2,
            pool_recycle=1800,
        )
        _sessionmaker = async_sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
        logger.info("只读数据库引擎已初始化（账号隔离）")
    return _sessionmaker


@asynccontextmanager
async def readonly_session() -> AsyncGenerator:
    maker = _ensure()
    session = maker()
    try:
        yield session
    finally:
        await session.close()


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
