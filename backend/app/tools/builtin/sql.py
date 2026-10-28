"""只读 SQL 工具。

四层防护，由外到内：
1. **账号隔离**（数据库层）：走独立的只读连接（`agentforge_ro`，仅 `agentforge.*` 的 SELECT），
   即使应用层判断漏了，数据库也会拒绝；
2. **表名白名单**：只允许访问本项目的 18 张业务表，`.user`/`information_schema` 等一律拒绝——
   这比"黑名单列举系统表"可靠，因为白名单天然完备；
3. **语句白名单 + 危险片段黑名单**：仅 SELECT/SHOW/DESCRIBE/EXPLAIN/WITH，禁多语句与文件读写；
4. **执行约束**：14 秒超时 + 最多 50 行。
"""

from __future__ import annotations

import asyncio
import re
from typing import Annotated, Any

from pydantic import Field
from sqlalchemy import text

from app.config import settings
from app.core.exceptions import ToolExecutionError
from app.db.readonly import readonly_session
from app.tools.registry import tool

_ALLOWED_PREFIX = ("select", "show", "describe", "desc", "explain", "with")
# SHOW 只放行"看表结构"这一族；SHOW DATABASES / VARIABLES / PROCESSLIST / GRANTS
# 没有业务价值且会泄露实例信息，一律拒绝
_ALLOWED_SHOW = (
    "show tables",
    "show full tables",
    "show columns",
    "show full columns",
    "show index",
    "show create table",
    "show table status",
)
_FORBIDDEN = (
    "insert", "update", "delete", "drop", "alter", "truncate", "create",
    "grant", "revoke", "replace", "merge", "call", "handler", "load_file",
    "outfile", "dumpfile", "sleep(", "benchmark(", "mysql.", "information_schema",
    "performance_schema", "sys.",
)
_COMMENT = re.compile(r"(/\*.*?\*/)|(--[^\n]*)", re.DOTALL)
_TABLE_REF = re.compile(r"\b(?:from|join)\s+`?([A-Za-z0-9_.]+)`?", re.IGNORECASE)

_ALLOWED_TABLES: set[str] | None = None


def allowed_tables() -> set[str]:
    """本项目业务表白名单，直接由 ORM 元数据生成，新增表无需改这里。"""
    global _ALLOWED_TABLES
    if _ALLOWED_TABLES is None:
        from app import models  # noqa: F401  触发注册
        from app.db.base import Base

        _ALLOWED_TABLES = set(Base.metadata.tables.keys())
    return _ALLOWED_TABLES


def _sanitize(sql: str) -> str:
    cleaned = _COMMENT.sub(" ", sql).strip()
    if not cleaned:
        raise ToolExecutionError("SQL 为空")
    if ";" in cleaned.rstrip(";"):
        raise ToolExecutionError("禁止一次执行多条语句")
    cleaned = cleaned.rstrip(";").strip()
    lowered = cleaned.lower()
    if not lowered.startswith(_ALLOWED_PREFIX):
        raise ToolExecutionError("仅允许只读查询（SELECT/SHOW/DESCRIBE/EXPLAIN/WITH）")

    if lowered.startswith("show") and not lowered.startswith(_ALLOWED_SHOW):
        raise ToolExecutionError(
            "SHOW 仅允许查看表结构（TABLES / COLUMNS / INDEX / CREATE TABLE / TABLE STATUS）"
        )
    if "@@" in cleaned:
        raise ToolExecutionError("禁止读取系统变量（@@）")

    for word in _FORBIDDEN:
        if word in lowered:
            raise ToolExecutionError(f"SQL 含被禁止的关键字: {word.strip('(')}")

    # 表名白名单：任何 FROM/JOIN 引用的表必须在业务表白名单内
    whitelist = allowed_tables()
    referenced = {m.group(1).split(".")[-1].lower() for m in _TABLE_REF.finditer(cleaned)}
    unauthorized = sorted(name for name in referenced if name not in whitelist)
    if unauthorized:
        raise ToolExecutionError(
            f"不允许访问表: {', '.join(unauthorized)}（仅限本平台业务表）"
        )
    return cleaned


@tool(
    name="sql_query",
    display_name="数据库只读查询",
    description="对平台自身数据执行只读查询，返回列名与行数据（最多 50 行）。仅可访问本平台业务表，仅支持 SELECT/SHOW/DESCRIBE/EXPLAIN。",
    category="data",
    timeout_s=15.0,
    tags=["sql", "mysql", "只读"],
)
async def sql_query(
    sql: Annotated[
        str, Field(description="只读 SQL 语句，例如 SELECT COUNT(*) AS c FROM conversations")
    ],
    limit: Annotated[int, Field(ge=1, le=200, description="最大返回行数")] = 50,
) -> dict[str, Any]:
    statement = _sanitize(sql)

    async def _run() -> dict[str, Any]:
        async with readonly_session() as session:
            result = await session.execute(text(statement))
            columns = list(result.keys()) if result.returns_rows else []
            rows = result.fetchmany(limit) if result.returns_rows else []
            return {
                "columns": columns,
                "rows": [
                    {col: _normalize(val) for col, val in zip(columns, row, strict=False)}
                    for row in rows
                ],
                "row_count": len(rows),
                "readonly_account": settings.readonly_isolated,
            }

    try:
        return await asyncio.wait_for(_run(), timeout=14.0)
    except asyncio.TimeoutError as exc:
        raise ToolExecutionError("查询超时（>14s），请缩小范围或增加过滤条件") from exc


def _normalize(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)
