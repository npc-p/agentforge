"""工具执行器：参数校验 → 超时控制 → 有限重试 → 结果归一化 → 流水记录。

重试策略只在「可重试异常」上生效（超时、连接错误、5xx），参数错误不重试——
参数错误重试一万次结果仍然是错误，这是最容易被写错的浪费。
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, ToolExecutionError
from app.core.logging import Timer, get_logger
from app.models.tool import ToolInvocation
from app.tools.registry import ToolSpec, registry

logger = get_logger(__name__)

_RETRYABLE_HINTS = ("timeout", "timed out", "connection", "temporarily", "502", "503", "504")


@dataclass(slots=True)
class ToolResult:
    tool: str
    ok: bool
    data: Any = None
    error: str | None = None
    latency_ms: float = 0.0
    retries: int = 0
    display: str = ""

    def to_observation(self, limit: int = 2000) -> str:
        """转换为回灌给模型的可读观察文本。"""
        if not self.ok:
            return f"[工具 {self.tool} 执行失败] {self.error}"
        text = self.display or (self.data if isinstance(self.data, str) else None)
        if text:
            return text[:limit]
        import json

        return json.dumps(self.data, ensure_ascii=False, default=str)[:limit]


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    msg = str(exc).lower()
    return any(hint in msg for hint in _RETRYABLE_HINTS)


class ToolExecutor:
    def __init__(self, registry_ref=registry) -> None:
        self.registry = registry_ref

    def _idempotency_key(self, tool: str, args: dict[str, Any], run_id: int | None) -> str | None:
        spec = self.registry.get(tool)
        if not spec.idempotent:
            return None
        raw = f"{run_id}:{tool}:{sorted(args.items(), key=lambda x: x[0])}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any] | None = None,
        *,
        run_id: int | None = None,
        session: AsyncSession | None = None,
        timeout_s: float | None = None,
    ) -> ToolResult:
        spec: ToolSpec = self.registry.get(tool_name)
        raw_args = args or {}
        try:
            validated = spec.validate(raw_args)
        except AppError as exc:
            # 参数错误是确定性失败：直接返回失败结果，不抛异常、不重试。
            # 若向上抛，Agent 整轮运行会因一次错误调用而中断，这是不可接受的放大。
            logger.warning("工具 %s 参数校验失败: %s", tool_name, exc.message)
            return ToolResult(tool=tool_name, ok=False, error=exc.message, retries=0)
        timeout = timeout_s or spec.timeout_s
        idem_key = self._idempotency_key(tool_name, validated, run_id)

        attempts = max(1, spec.max_retries + 1)
        last_exc: Exception | None = None
        retries_used = 0

        with Timer() as timer:
            for attempt in range(attempts):
                try:
                    data = await asyncio.wait_for(spec.handler(**validated), timeout=timeout)
                    result = ToolResult(
                        tool=tool_name,
                        ok=True,
                        data=data,
                        latency_ms=0.0,
                        retries=attempt,
                    )
                    break
                except AppError as exc:
                    # 业务性错误（含参数/权限问题）不重试
                    result = ToolResult(
                        tool=tool_name, ok=False, error=exc.message, retries=attempt
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    retries_used = attempt
                    if not _is_retryable(exc) or attempt == attempts - 1:
                        result = ToolResult(
                            tool=tool_name,
                            ok=False,
                            error=f"{type(exc).__name__}: {exc}"[:300],
                            retries=attempt,
                        )
                        break
                    await asyncio.sleep(0.3 * (2**attempt))
                    logger.warning("工具 %s 第 %s 次失败，准备重试: %s", tool_name, attempt + 1, exc)

        result.latency_ms = timer.elapsed_ms
        result.retries = retries_used
        if last_exc and not result.ok:
            logger.error("工具 %s 最终失败: %s", tool_name, result.error)

        await self._record(result, validated, run_id, session, idem_key)
        return result

    async def _record(
        self,
        result: ToolResult,
        args: dict[str, Any],
        run_id: int | None,
        session: AsyncSession | None,
        idem_key: str | None,
    ) -> None:
        if session is None:
            return
        try:
            payload = result.data if isinstance(result.data, (dict, list)) else {"value": result.data}
            inv = ToolInvocation(
                run_id=run_id,
                tool_name=result.tool,
                args=args,
                result=payload,
                status="ok" if result.ok else "failed",
                error=result.error,
                latency_ms=int(result.latency_ms),
                retries=result.retries,
                idempotency_key=idem_key,
            )
            session.add(inv)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            # 流水写入失败不应影响主流程
            await session.rollback()
            logger.warning("工具调用流水写入失败: %s", exc)

    async def execute_many(
        self,
        calls: list[tuple[str, dict[str, Any]]],
        *,
        run_id: int | None = None,
        session: AsyncSession | None = None,
    ) -> list[ToolResult]:
        """并发执行多个工具；用于规划阶段识别出的无依赖并行任务。"""
        tasks = [
            self.execute(name, args, run_id=run_id, session=session) for name, args in calls
        ]
        return list(await asyncio.gather(*tasks))


_executor: ToolExecutor | None = None


def get_executor() -> ToolExecutor:
    global _executor
    if _executor is None:
        _executor = ToolExecutor()
    return _executor


__all__ = ["ToolResult", "ToolExecutor", "get_executor", "ToolExecutionError"]
