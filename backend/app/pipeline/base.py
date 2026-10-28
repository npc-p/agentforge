"""数据流水线的通用数据结构。

样本在流水线中以不可变语义的 dataclass 传递，各阶段只返回「新对象 + 变更记录」，
不做原地修改，保证每一步的输入输出都能被单独复现与回放。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Sample:
    """统一样本结构。三种常见字段命名在入管线前会被归一到这里。"""

    instruction: str = ""
    input: str = ""
    output: str = ""
    system: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Sample":
        """兼容 instruction/input/output、question/answer、prompt/completion 三种写法。"""
        get = lambda *keys: next(  # noqa: E731
            (str(raw[k]) for k in keys if raw.get(k) not in (None, "")), ""
        )
        known = {"instruction", "input", "output", "system", "question", "answer", "prompt", "completion"}
        extra_meta = {k: v for k, v in raw.items() if k not in known}
        return cls(
            instruction=get("instruction", "question", "prompt"),
            input=get("input"),
            output=get("output", "answer", "completion"),
            system=get("system"),
            meta=extra_meta,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "instruction": self.instruction,
            "input": self.input,
            "output": self.output,
            "system": self.system,
            "meta": self.meta,
        }

    @property
    def prompt_text(self) -> str:
        return f"{self.instruction}\n{self.input}".strip()

    @property
    def char_count(self) -> int:
        return len(self.prompt_text) + len(self.output)


@dataclass(slots=True)
class StageStats:
    """单阶段统计，直接作为接口返回值与版本快照的质量指标。"""

    stage: str
    total: int = 0
    changed: int = 0
    dropped: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "total": self.total,
            "changed": self.changed,
            "dropped": self.dropped,
            "details": self.details,
        }


@dataclass(slots=True)
class QCVerdict:
    passed: bool
    score: float
    failures: list[dict[str, str]] = field(default_factory=list)
