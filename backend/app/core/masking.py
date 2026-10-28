"""脱敏工具：日志与对外输出前的隐私擦除。

与 `pipeline/cleaner.normalize_text` 的差别——两者目的不同，不要混用：

| 场景 | 策略 | 理由 |
|---|---|---|
| 数据清洗（训练样本） | 命中即整体替换为 `[已脱敏]` | 训练数据不该保留任何原始 PII |
| 日志 / 调试输出 | **部分保留**（手机号 `138****5678`） | 既要定位是哪条记录，又要不足以还原 |

设计要点：
- 纯函数、无副作用、不依赖 settings，便于单测；
- 所有替换都是定长，不会因脱敏把日志字段搞乱；
- `mask_text` 不应抛异常——日志链路上的异常会掩盖真正的业务错误。
"""

from __future__ import annotations

import re
from typing import Any

MASK = "[已脱敏]"

# 顺序敏感：身份证（18 位）必须早于银行卡（16~19 位），否则会被当成卡号。
# 手机号（11 位）与两者长度不重叠，放最前无影响。
_PHONE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
_ID_CARD = re.compile(r"(?<!\d)(\d{6})\d{8}(\d{3}[\dXx])(?!\d)")
_BANK_CARD = re.compile(r"(?<!\d)(\d{4})\d{8,11}(\d{4})(?!\d)")
_EMAIL = re.compile(r"\b([\w.+-])[\w.+-]*@([\w-]+\.[\w.]+)")

# 密钥类：保留可识别的前缀，抹掉主体。sk-abc12345... -> sk-abcd***
_SECRET = re.compile(r"\b(sk-[A-Za-z0-9_\-]{4})[A-Za-z0-9_\-]{8,}")
_BEARER = re.compile(r"(Bearer\s+)([A-Za-z0-9\-._~+/]{8,}=*)", re.IGNORECASE)
_KV_SECRET = re.compile(
    r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key|authorization)\b"
    r"\s*[:=]\s*([^\s,;&\"'}\)]{3,})"
)

# 字段名命中即整值擦除（key 本身已说明这是敏感字段，保留部分值没有意义）
_SENSITIVE_KEYS = frozenset(
    {
        "password", "passwd", "pwd", "secret", "token", "access_token",
        "api_key", "apikey", "authorization", "cookie", "set-cookie",
        "id_card", "idcard", "bank_card", "private_key",
    }
)

_MAX_DEPTH = 6


def mask_text(text: str) -> str:
    """对自由文本做 PII 擦除。非字符串原样返回。"""
    if not isinstance(text, str) or not text:
        return text

    # 手机号 / 身份证 / 银行卡：保留首尾，中间打码
    text = _PHONE.sub(r"\1****\2", text)
    text = _ID_CARD.sub(r"\1********\2", text)
    text = _BANK_CARD.sub(r"\1********\2", text)
    # 邮箱：只留首字符与域名
    text = _EMAIL.sub(r"\1***@\2", text)
    # 密钥
    text = _SECRET.sub(r"\1***", text)
    text = _BEARER.sub(r"\1" + MASK, text)
    text = _KV_SECRET.sub(lambda m: f"{m.group(1)}={MASK}", text)
    return text


def _mask_value(key: str, value: Any, depth: int) -> Any:
    if isinstance(key, str) and key.strip().lower() in _SENSITIVE_KEYS:
        return MASK if value not in (None, "") else value
    return mask_any(value, depth + 1)


def mask_any(value: Any, depth: int = 0) -> Any:
    """递归脱敏任意结构（str / dict / list / tuple）。深度受限，防循环引用。"""
    if depth > _MAX_DEPTH:
        return value
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {k: _mask_value(k, v, depth) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        masked = [mask_any(v, depth + 1) for v in value]
        return type(value)(masked) if isinstance(value, tuple) else masked
    if isinstance(value, (bytes, bytearray)):
        return MASK
    return value
