"""工具注册中心。

核心能力：
1. 由函数签名自动推导 JSON Schema（pydantic create_model），避免手写 schema 漂移；
2. 同一份注册表同时服务于「模型侧 function calling 定义」与「执行侧参数校验」，
   从根上消除两侧不一致（配对契约同源）；
3. 支持运行时启停与调用统计，便于灰度下线某个工具。
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, get_type_hints

from pydantic import BaseModel, ValidationError, create_model
from pydantic.fields import FieldInfo

from app.core.exceptions import AppError, ValidationError as AppValidationError

ToolHandler = Callable[..., Awaitable[Any]]


class ToolSchemaError(AppError):
    code = "tool_schema_error"
    http_status = 500
    message = "工具参数结构非法"


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    params_model: type[BaseModel]
    handler: ToolHandler
    display_name: str = ""
    category: str = "general"
    timeout_s: float = 20.0
    max_retries: int = 1
    idempotent: bool = False
    source: str = "builtin"
    enabled: bool = True
    tags: list[str] = field(default_factory=list)

    @property
    def params_schema(self) -> dict[str, Any]:
        return self.params_model.model_json_schema()

    def openai_schema(self) -> dict[str, Any]:
        """转换为 OpenAI function calling 描述。"""
        schema = self.params_schema
        schema.pop("title", None)
        schema.pop("description", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }

    def validate(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            model = self.params_model(**args)
        except ValidationError as exc:
            raise AppValidationError(
                f"工具 {self.name} 参数校验失败",
                detail=exc.errors(include_url=False),
            ) from exc
        # 不使用 exclude_none：默认值必须显式下传，否则函数体会拿到签名里的 FieldInfo 哨兵
        return model.model_dump()


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec, *, override: bool = False) -> ToolSpec:
        if spec.name in self._tools and not override:
            raise ToolSchemaError(f"工具名重复注册: {spec.name}")
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        spec = self._tools.get(name)
        if spec is None:
            raise AppValidationError(f"工具不存在: {name}", detail={"available": self.names()})
        return spec

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[ToolSpec]:
        return [self._tools[n] for n in self.names()]

    def enabled(self) -> list[ToolSpec]:
        return [spec for spec in self.all() if spec.enabled]

    def openai_schemas(self, only: list[str] | None = None) -> list[dict[str, Any]]:
        specs = self.enabled()
        if only is not None:
            allowed = set(only)
            specs = [s for s in specs if s.name in allowed]
        return [s.openai_schema() for s in specs]

    def set_enabled(self, name: str, enabled: bool) -> None:
        self.get(name).enabled = enabled

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)


registry = ToolRegistry()


def _build_params_model(func: ToolHandler, name: str) -> type[BaseModel]:
    """由函数签名生成 pydantic 模型。

    推荐写法是 ``Annotated[T, Field(description=...)]`` + 普通默认值：
    这样注解承载元信息、默认值承载真实值，两者不会互相污染。

    注意：工具模块普遍使用 ``from __future__ import annotations``，此时
    ``inspect.signature`` 拿到的是字符串注解，必须用 ``get_type_hints(include_extras=True)``
    求值，否则 pydantic 会因无法解析 ``Annotated`` 而报 class-not-fully-defined。
    """
    hints: dict[str, Any] = {}
    try:
        hints = get_type_hints(func, include_extras=True)
    except Exception:  # noqa: BLE001 - 注解求值失败时降级为裸类型
        hints = {}

    fields: dict[str, tuple[Any, Any]] = {}
    for param_name, param in inspect.signature(func).parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        annotation = hints.get(param_name, param.annotation)
        if annotation is inspect._empty or isinstance(annotation, str):
            annotation = str  # 兜底：无法求值时按字符串接收，交由函数体自行校验
        default = param.default
        if isinstance(default, FieldInfo):
            fields[param_name] = (annotation, default)
        elif default is inspect._empty:
            # 用 Ellipsis 而非 Field(...)，避免覆盖 Annotated 里的 description/constraints
            fields[param_name] = (annotation, ...)
        else:
            fields[param_name] = (annotation, default)
    model_name = "".join(part.capitalize() for part in name.split("_")) + "Params"
    return create_model(model_name, **fields)  # type: ignore[call-overload]


def tool(
    *,
    name: str,
    description: str,
    category: str = "general",
    display_name: str = "",
    timeout_s: float = 20.0,
    max_retries: int = 1,
    idempotent: bool = False,
    tags: list[str] | None = None,
) -> Callable[[ToolHandler], ToolHandler]:
    """装饰器：把一个 async 函数注册为可被 Agent 调用的工具。"""

    def decorator(func: ToolHandler) -> ToolHandler:
        spec = ToolSpec(
            name=name,
            display_name=display_name or name,
            description=description,
            params_model=_build_params_model(func, name),
            handler=func,
            category=category,
            timeout_s=timeout_s,
            max_retries=max_retries,
            idempotent=idempotent,
            tags=tags or [],
        )
        registry.register(spec)
        return func

    return decorator
