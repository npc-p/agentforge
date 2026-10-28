"""异常体系与统一错误码。

分层原则：
- AppError 及其子类 = 业务可预期错误，携带 code / http_status / detail，交由全局
  异常处理器转换为统一响应体；
- 其他未捕获异常 = 系统缺陷，统一脱敏为 500，细节只落日志，不回传调用方。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    code: str = "internal_error"
    http_status: int = 500
    message: str = "服务内部错误"

    def __init__(
        self,
        message: str | None = None,
        *,
        detail: Any = None,
        code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.detail = detail
        if code:
            self.code = code
        if http_status:
            self.http_status = http_status
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            body["detail"] = self.detail
        return body


class NotFoundError(AppError):
    code = "not_found"
    http_status = 404
    message = "资源不存在"


class ValidationError(AppError):
    code = "validation_error"
    http_status = 422
    message = "参数校验失败"


class ConflictError(AppError):
    code = "conflict"
    http_status = 409
    message = "资源状态冲突"


class LLMError(AppError):
    code = "llm_error"
    http_status = 502
    message = "模型服务调用失败"


class LLMUnavailableError(LLMError):
    code = "llm_unavailable"
    http_status = 503
    message = "模型服务暂不可用（熔断中）"


class ToolExecutionError(AppError):
    code = "tool_error"
    http_status = 502
    message = "工具执行失败"


class AgentError(AppError):
    code = "agent_error"
    http_status = 500
    message = "智能体执行失败"


class VectorStoreError(AppError):
    code = "vector_store_error"
    http_status = 503
    message = "向量库不可用"
