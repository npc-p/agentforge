"""日志与追踪工具。

统一使用标准库 logging，附带 request_id / trace_id 透传，便于串联一次
Agent 执行的完整链路（HTTP 请求 → Agent Run → 工具调用）。

两种输出格式：
- `json`（默认）：每行一个 JSON 对象，字段固定为 ts / level / logger / trace / msg，
  便于 `jq`、ELK、Loki 等按字段筛选统计，而不是只能 Ctrl+F；
- `text`：本地临时排障用的可读单行格式。

**脱敏放在基础设施层，而不是靠每个调用点自觉**：`MaskingFilter` 会在消息
真正写出前扫描 msg 与 args，把手机号、身份证、邮箱、密钥等按规则打码。
这样将来新增代码即使直接把用户 query 写进日志，也不会把隐私落到磁盘上。
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from datetime import datetime
from typing import Any

from app.config import settings
from app.core.masking import mask_any

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")

_TEXT_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | trace=%(trace_id)s | %(message)s"

# LogRecord 的标准属性：这些不进 JSON 的 extra 区，其余自定义字段原样带出
_STD_KEYS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message", "asctime", "trace_id",
    }
)


class TraceIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.trace_id = trace_id_var.get()
        return True


class MaskingFilter(logging.Filter):
    """在写出前擦除 msg、args **以及 extra 字段**中的 PII。

    extra 必须一起处理：JSON 化之后，`logger.info("x", extra={"query": ...})`
    会把值原样写进日志。早期只擦 msg/args，纯文本格式下 extra 不会出现在输出里
    所以看不出问题；改成结构化输出后这条路径就变成了漏点（已实测泄露手机号）。

    脱敏绝不能影响业务：任何异常都被吞掉并放行原始记录——宁可日志不完美，
    也不能让日志链路把一次正常请求搞崩。
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if not settings.log_mask:
            return True
        try:
            if isinstance(record.msg, str):
                record.msg = mask_any(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: mask_any(v) for k, v in record.args.items()}
                else:
                    record.args = tuple(mask_any(a) for a in record.args)
            # extra 会作为自定义字段进入 JSON，必须同样擦一遍（只覆盖自定义键，
            # 标准属性如 levelname/pathname 不动）
            for key, value in list(record.__dict__.items()):
                if key in _STD_KEYS or key.startswith("_"):
                    continue
                record.__dict__[key] = mask_any(value)
        except Exception:  # noqa: BLE001 - 脱敏失败不得中断日志
            pass
        return True


class JsonFormatter(logging.Formatter):
    """单行 JSON。字段固定，自定义 extra 追加在后面。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created)
            .astimezone()
            .isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "trace": getattr(record, "trace_id", "-"),
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STD_KEYS or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            # 兜底：extra 里有无法序列化的对象时，退回只输出基础字段
            return json.dumps(
                {k: str(v) for k, v in payload.items()}, ensure_ascii=False
            )


def setup_logging(level: str = "INFO", fmt: str | None = None) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(TraceIdFilter())
    handler.addFilter(MaskingFilter())

    if (fmt or settings.log_format) == "text":
        handler.setFormatter(logging.Formatter(_TEXT_FORMAT))
    else:
        handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    for noisy in ("aiomysql", "chromadb", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def bind_trace_id(value: str | None = None) -> str:
    tid = value or new_trace_id()
    trace_id_var.set(tid)
    return tid


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


class Timer:
    """毫秒级计时上下文，用于记录 LLM / 工具 / 检索耗时。"""

    def __init__(self) -> None:
        self._start = 0.0
        self.elapsed_ms: float = 0.0

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.elapsed_ms = round((time.perf_counter() - self._start) * 1000, 2)
