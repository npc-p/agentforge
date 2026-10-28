"""FastAPI 应用入口。

启动顺序：日志 → 目录 → 数据库连通性 → 建表（仅非生产）→ 工具注册表同步。
任一环节失败都不阻断进程启动：服务先起来，健康检查如实报告哪一项不可用，
这比「起不来又说不清原因」更容易运维定位。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import api_router
from app.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger, setup_logging, trace_id_var
from app.core.response import ok

logger = get_logger(__name__)


async def _warmup_embedder() -> None:
    """后台预加载本地向量模型。

    失败只记警告、不抛异常：预热是优化，不是启动前提。真正不可用时首次检索
    仍会给出明确错误（`embed_model_missing` / `embed_dependency_missing`）。
    """
    from app.rag.embedder import get_embedder

    try:
        embedder = get_embedder()
        await embedder.warmup()
        logger.info("嵌入模型预热完成: %s dim=%s", embedder.name, embedder.dim)
    except Exception as exc:  # noqa: BLE001
        logger.warning("嵌入模型预热失败（首次检索时会重试）: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging("DEBUG" if settings.debug else "INFO")
    settings.ensure_dirs()
    logger.info("启动 %s v%s (env=%s)", settings.app_name, settings.app_version, settings.env)

    from app.db.session import AsyncSessionLocal, create_all, dispose, ping

    if await ping():
        if settings.env != "prod":
            try:
                await create_all()
                logger.info("数据表已就绪（dev 模式自动建表）")
            except Exception as exc:  # noqa: BLE001
                logger.error("建表失败: %s", exc)
        try:
            from app.services.tool_service import sync_registry

            async with AsyncSessionLocal() as session:
                await sync_registry(session)
        except Exception as exc:  # noqa: BLE001
            logger.error("工具注册表同步失败: %s", exc)
    else:
        logger.error("数据库不可用，服务以降级模式启动")

    if not settings.llm_ready:
        logger.warning("未配置 LLM Key，智能体运行在离线 Mock 模式")

    # 后台预热嵌入模型：BGE-M3 权重 2.2G，首次加载 10~30s。若等到第一个检索
    # 请求才加载，用户看到的是「首问卡住」；这里启动即异步预载，且不阻塞监听。
    # 测试环境跳过——否则每次 pytest 都要多等半分钟加载模型。
    if settings.env != "test" and settings.embed_provider == "bge-m3":
        app.state.warmup_task = asyncio.create_task(_warmup_embedder())

    yield

    await dispose()
    logger.info("服务已停止")


app = FastAPI(
    title="AgentForge API",
    version=settings.app_version,
    description=(
        "智能体研发平台后端：Agent 状态机、工具调用、RAG 混合检索、数据流水线、评测体系。\n\n"
        "所有业务接口统一返回 `{code, message, data, trace_id}`；SSE 接口以事件流形式返回。"
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    from app.core.logging import bind_trace_id

    tid = request.headers.get("X-Trace-Id") or bind_trace_id()
    bind_trace_id(tid)
    response = await call_next(request)
    response.headers["X-Trace-Id"] = tid
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    body: dict[str, Any] = exc.to_dict()
    body["trace_id"] = trace_id_var.get()
    return JSONResponse(status_code=exc.http_status, content=body)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "code": "validation_error",
            "message": "请求参数校验失败",
            "detail": exc.errors(),
            "trace_id": trace_id_var.get(),
        },
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未捕获异常: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "code": "internal_error",
            "message": "服务内部错误",
            "trace_id": trace_id_var.get(),
        },
    )


app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/health", tags=["system"], summary="存活探针")
async def health_probe():
    return ok({"status": "alive"})


@app.get("/", tags=["system"], summary="服务信息")
async def root():
    return ok(
        {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "api_prefix": settings.api_prefix,
        }
    )
