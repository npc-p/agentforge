"""供应商工厂 + 重试 + 熔断。

可靠性三件套：
- 重试：仅对超时、连接错误、429、5xx 重试，指数退避（0.5s / 1s / 2s）；
- 熔断：连续失败达阈值后打开，冷却期内快速失败，避免雪崩式等待；
- 降级：未配置密钥时自动切 MockProvider，保证系统始终可用。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.config import settings
from app.core.exceptions import LLMError, LLMUnavailableError
from app.core.logging import get_logger
from app.llm.base import ChatMessage, LLMProvider, LLMResult
from app.llm.deepseek import DeepSeekProvider
from app.llm.mock import MockProvider

logger = get_logger(__name__)

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class CircuitBreaker:
    """极简熔断器：closed → open → half_open。"""

    def __init__(self, threshold: int, cooldown: float) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures = 0
        self.opened_at: float = 0.0
        self.state = "closed"

    def before_call(self) -> None:
        if self.state == "open":
            if time.monotonic() - self.opened_at >= self.cooldown:
                self.state = "half_open"
                logger.warning("熔断器进入半开状态，放行探测请求")
            else:
                raise LLMUnavailableError(
                    f"模型服务熔断中，请 {int(self.cooldown)} 秒后重试",
                    detail={"state": self.state, "failures": self.failures},
                )

    def on_success(self) -> None:
        if self.state != "closed":
            logger.info("熔断器恢复关闭状态")
        self.failures = 0
        self.state = "closed"

    def on_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.state = "open"
            self.opened_at = time.monotonic()
            logger.error("熔断器打开：连续失败 %s 次", self.failures)

    def snapshot(self) -> dict[str, Any]:
        return {"state": self.state, "failures": self.failures, "threshold": self.threshold}


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return False


class LLMGateway:
    """对外唯一入口：业务层注入它，而不是直接持有某个 Provider。"""

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self._provider = provider or build_provider()
        self._breaker = CircuitBreaker(settings.llm_circuit_threshold, settings.llm_circuit_cooldown)

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    @property
    def is_mock(self) -> bool:
        return self._provider.name == "mock"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResult:
        self._breaker.before_call()
        attempts = settings.llm_max_retries + 1
        last_exc: Exception | None = None

        for attempt in range(attempts):
            try:
                result = await self._provider.chat(
                    messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                )
                self._breaker.on_success()
                return result
            except LLMUnavailableError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not _is_retryable(exc) or attempt == attempts - 1:
                    self._breaker.on_failure()
                    break
                backoff = 0.5 * (2**attempt)
                logger.warning(
                    "LLM 调用失败（第 %s/%s 次），%.1fs 后重试: %s",
                    attempt + 1,
                    attempts,
                    backoff,
                    type(exc).__name__,
                )
                await asyncio.sleep(backoff)

        raise LLMError(
            "模型服务调用失败，已重试仍未成功",
            detail={"error": str(last_exc)[:300] if last_exc else "unknown"},
        )

    def health(self) -> dict[str, Any]:
        return {
            "provider": self._provider.name,
            "model": self._provider.model,
            "ready": self._provider.ready,
            "mock": self.is_mock,
            "circuit": self._breaker.snapshot(),
        }

    async def close(self) -> None:
        await self._provider.close()


def build_provider() -> LLMProvider:
    """按配置构建供应商；缺密钥时降级 Mock，并显式告警。"""
    if settings.llm_provider == "deepseek":
        provider = DeepSeekProvider()
        if provider.ready:
            return provider
        logger.warning("未检测到 DEEPSEEK_API_KEY，已自动降级为离线 Mock 模式")
        return MockProvider()
    return MockProvider()


_gateway: LLMGateway | None = None


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
