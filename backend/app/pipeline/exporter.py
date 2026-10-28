"""导出阶段：按训练框架要求生成数据文件。

支持三种主流格式：
- chat_jsonl：OpenAI / DeepSeek 微调接口的 messages 格式，也最贴近 Agent 训练样本；
- sharegpt  ：LLaMA-Factory 等框架常用的 conversations 格式；
- alpaca    ：单轮指令微调最简格式。

导出只输出「质检通过」的样本，且按 split 过滤，避免把验证集混进训练集。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.logging import get_logger
from app.pipeline.base import Sample

logger = get_logger(__name__)

FORMATS = ("chat_jsonl", "sharegpt", "alpaca")
_EXT = {"chat_jsonl": "jsonl", "sharegpt": "jsonl", "alpaca": "json"}


@dataclass(slots=True)
class ExportResult:
    path: str
    format: str
    count: int
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "format": self.format,
            "count": self.count,
            "size_bytes": self.size_bytes,
        }


def build_record(sample: Sample, fmt: str) -> dict[str, Any]:
    user_content = sample.prompt_text
    if fmt == "chat_jsonl":
        messages: list[dict[str, str]] = []
        if sample.system:
            messages.append({"role": "system", "content": sample.system})
        messages.append({"role": "user", "content": user_content})
        messages.append({"role": "assistant", "content": sample.output})
        return {"messages": messages}

    if fmt == "sharegpt":
        conversations = [{"from": "human", "value": user_content}]
        if sample.system:
            conversations.insert(0, {"from": "system", "value": sample.system})
        conversations.append({"from": "gpt", "value": sample.output})
        return {"conversations": conversations}

    return {
        "instruction": sample.instruction,
        "input": sample.input,
        "output": sample.output,
        "system": sample.system,
    }


def export_samples(
    samples: list[Sample],
    *,
    dataset_id: int,
    version: int,
    fmt: str = "chat_jsonl",
) -> ExportResult:
    if fmt not in FORMATS:
        raise ValueError(f"不支持的导出格式: {fmt}")

    settings.export_path.mkdir(parents=True, exist_ok=True)
    filename = f"dataset_{dataset_id}_v{version}_{fmt}.{_EXT[fmt]}"
    path = settings.export_path / filename

    if fmt == "alpaca":
        payload = [build_record(s, fmt) for s in samples]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            for sample in samples:
                fh.write(json.dumps(build_record(sample, fmt), ensure_ascii=False) + "\n")

    size = path.stat().st_size
    logger.info("数据集导出完成: %s 条数=%s 大小=%sB", path.name, len(samples), size)
    return ExportResult(path=str(path), format=fmt, count=len(samples), size_bytes=size)


def preview(samples: list[Sample], fmt: str = "chat_jsonl", limit: int = 3) -> list[dict[str, Any]]:
    return [build_record(s, fmt) for s in samples[:limit]]


def file_path_of(relative: str) -> Path:
    return Path(relative) if Path(relative).is_absolute() else settings.export_path / relative
