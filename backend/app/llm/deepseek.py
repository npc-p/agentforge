"""DeepSeek 供应商实现（OpenAI 兼容协议）。

同一份实现可复用于任何 OpenAI 兼容端点（硅基流动、Moonshot、自建 vLLM），
只需改 base_url / model / api_key。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import settings
from app.core.logging import Timer, get_logger
from app.llm.base import ChatMessage, LLMProvider, LLMResult, ToolCall, Usage

logger = get_logger(__name__)


class DeepSeekProvider(LLMProvider):
    name = "deepseek"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.deepseek_api_key
        self.base_url = (base_url or settings.deepseek_base_url).rstrip("/")
        self.model = model or settings.deepseek_model
        self.timeout = timeout or settings.llm_timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    def _payload(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None,
        temperature: float | None,
        max_tokens: int | None,
        json_mode: bool,
        stream: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_openai() for m in messages],
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or settings.llm_max_tokens,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResult:
        body = self._payload(messages, tools, temperature, max_tokens, json_mode, False)
        with Timer() as timer:
            resp = await self._http().post("/chat/completions", json=body)
            if resp.status_code >= 400:
                # 交由上层 RetryPolicy 依据状态码判定是否重试
                raise httpx.HTTPStatusError(
                    f"LLM HTTP {resp.status_code}: {resp.text[:300]}",
                    request=resp.request,
                    response=resp,
                )
            data = resp.json()

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage_raw = data.get("usage") or {}

        tool_calls: list[ToolCall] = []
        for idx, tc in enumerate(message.get("tool_calls") or []):
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except json.JSONDecodeError:
                logger.warning("工具参数非合法 JSON，已置空: %s", raw_args[:200])
                args = {"_raw": raw_args}
            tool_calls.append(
                ToolCall(id=tc.get("id") or f"call_{idx}", name=fn.get("name", ""), arguments=args)
            )

        return LLMResult(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            usage=Usage(
                prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
                completion_tokens=int(usage_raw.get("completion_tokens") or 0),
            ),
            latency_ms=timer.elapsed_ms,
            model=data.get("model") or self.model,
            finish_reason=choice.get("finish_reason") or "stop",
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        body = self._payload(messages, None, temperature, max_tokens, False, True)
        async with self._http().stream("POST", "/chat/completions", json=body) as resp:
            if resp.status_code >= 400:
                detail = (await resp.aread()).decode("utf-8", "ignore")[:300]
                raise httpx.HTTPStatusError(
                    f"LLM stream HTTP {resp.status_code}: {detail}",
                    request=resp.request,
                    response=resp,
                )
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    payload = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                delta = ((payload.get("choices") or [{}])[0].get("delta") or {}).get("content")
                if delta:
                    yield delta

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
