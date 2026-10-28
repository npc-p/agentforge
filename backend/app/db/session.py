"""异步数据库引擎与会话管理（MySQL + aiomysql）。

事务边界由 service 层显式控制；依赖注入只负责「用完关闭 / 出错回滚」。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 测试环境使用 NullPool：pytest 每个用例可能运行在不同事件循环上，
# 池化连接会跨循环复用并抛出 "attached to a different loop"。
_engine_kwargs: dict = {"echo": settings.db_echo}
if settings.env == "test":
    _engine_kwargs["poolclass"] = NullPool
else:
    _engine_kwargs.update(
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=1800,
    )

engine = create_async_engine(settings.database_url, **_engine_kwargs)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：每请求一个会话。"""
    session = AsyncSessionLocal()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def ping() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # pragma: no cover - 环境相关
        logger.error("数据库连通性检查失败: %s", exc)
        return False


async def create_all() -> None:
    """仅用于开发/测试环境快速建表；生产环境走 Alembic。"""
    from app.db.base import Base
    from app import models  # noqa: F401  触发模型注册

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose() -> None:
    await engine.dispose()
