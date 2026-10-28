"""HTTP 工具：访问第三方 API。

安全约束（对应威胁建模中的 SSRF 风险）：
1. 仅允许 http/https，且拒绝解析到内网/回环/链路本地地址的目标；
2. 响应体截断，避免大对象打爆内存与上下文窗口；
3. 超时与重定向跳数双重限制，防止挂死。
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Annotated, Any
from urllib.parse import urlparse

import httpx
from pydantic import Field

from app.core.exceptions import ToolExecutionError
from app.tools.registry import tool

_MAX_BYTES = 200_000
_MAX_REDIRECTS = 3
_BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "169.254.169.254"}


def _assert_public_host(host: str) -> None:
    if not host:
        raise ToolExecutionError("URL 缺少主机名")
    if host.lower() in _BLOCKED_HOSTS:
        raise ToolExecutionError("目标主机被安全策略拒绝")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ToolExecutionError(f"域名解析失败: {host}") from exc

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ToolExecutionError("目标解析到内网地址，已按安全策略拒绝")


@tool(
    name="http_request",
    display_name="HTTP 请求",
    description="向公开的第三方 HTTP API 发起 GET/POST 请求并返回状态码与响应体（自动截断）。",
    category="network",
    timeout_s=15.0,
    max_retries=1,
    tags=["http", "集成"],
)
async def http_request(
    url: Annotated[str, Field(description="完整 URL，必须以 http:// 或 https:// 开头")],
    method: Annotated[str, Field(description="HTTP 方法，GET 或 POST")] = "GET",
    params: Annotated[dict[str, Any] | None, Field(description="查询参数")] = None,
    json_body: Annotated[dict[str, Any] | None, Field(description="POST 的 JSON 请求体")] = None,
    headers: Annotated[dict[str, str] | None, Field(description="自定义请求头")] = None,
) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ToolExecutionError("仅支持 http/https")
    _assert_public_host(parsed.hostname or "")

    verb = method.upper()
    if verb not in ("GET", "POST"):
        raise ToolExecutionError("仅支持 GET / POST")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=5.0),
        follow_redirects=True,
        max_redirects=_MAX_REDIRECTS,
    ) as client:
        resp = await client.request(
            verb, url, params=params, json=json_body if verb == "POST" else None, headers=headers
        )
        if resp.url.host and resp.url.host != parsed.hostname:
            _assert_public_host(resp.url.host)  # 防重定向绕过
        content = resp.content[:_MAX_BYTES]

    text = content.decode(resp.encoding or "utf-8", errors="replace")
    return {
        "status_code": resp.status_code,
        "content_type": resp.headers.get("content-type", ""),
        "truncated": len(resp.content) > _MAX_BYTES,
        "body": text,
    }
