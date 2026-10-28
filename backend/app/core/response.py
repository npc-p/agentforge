"""统一响应封装。

约定：所有业务接口返回 {"code": 0, "message": "ok", "data": ..., "trace_id": ...}，
错误返回 {"code": "xxx", "message": "...", "detail": ..., "trace_id": ...}。
SSE 流式接口不走此封装。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from app.core.logging import trace_id_var

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    code: int | str = 0
    message: str = "ok"
    data: T | None = None
    trace_id: str = "-"


def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    return {
        "code": 0,
        "message": message,
        "data": data,
        "trace_id": trace_id_var.get(),
    }
