"""文本与 JSON 通用工具。

Token 估算刻意不引入 tiktoken：模型厂商分词器不同，估算只为成本量级参考，
用「中文按字、英文按词」的启发式即可，避免额外依赖与离线下载。
"""

from __future__ import annotations

import json
import re
from typing import Any

_CJK = re.compile(r"[\u4e00-\u9fff]")
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def estimate_tokens(text: str) -> int:
    """粗估 token：CJK 字符约 1 token/字，其余约 4 字符/token。"""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    other = max(len(text) - cjk, 0)
    return cjk + other // 4 + 1


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    if text is None:
        return ""
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + suffix


def extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出中稳健提取 JSON 对象。

    依次尝试：整体解析 → 代码块 → 首个平衡大括号片段。
    模型普遍会在 JSON 前后附加解释性文字，此函数负责兜住这类噪声。
    """
    if not text:
        return None
    candidates: list[str] = [text.strip()]

    fence = _JSON_FENCE.search(text)
    if fence:
        candidates.append(fence.group(1).strip())

    balanced = _first_balanced_object(text)
    if balanced:
        candidates.append(balanced)

    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}
    return None


def _first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def to_json_str(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def normalize_space(text: str) -> str:
    return re.sub(r"[ \t\u3000]+", " ", text or "").strip()


def sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()
