"""清洗阶段：把采集到的脏数据规整为可标注的样本。

清洗规则全部是确定性变换，每条规则独立可测；命中即记录变更原因，
便于在质检环节回溯「这条样本到底被改过什么」。
"""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any

from app.pipeline.base import Sample

_HTML_TAG = re.compile(r"<[^>]+>")
_HTML_ENTITY = re.compile(r"&[a-zA-Z#0-9]{2,8};")
_URL = re.compile(r"https?://\S+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_MULTI_SPACE = re.compile(r"[ \t\u3000]{2,}")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 占位符是低质数据的强信号：模型会学到「怎么写废话」
_PLACEHOLDER = re.compile(r"(TODO|FIXME|XXX|待补充|待完善|暂无内容|lorem ipsum)", re.IGNORECASE)

SENSITIVE_WORDS = ("身份证号", "银行卡号", "密码是", "密钥是", "信用卡号")


def normalize_text(text: str, *, strip_url: bool = False, mask_pii: bool = True) -> tuple[str, list[str]]:
    """返回 (清洗后文本, 变更说明列表)。"""
    changes: list[str] = []
    if not text:
        return "", changes

    original = text
    text = unicodedata.normalize("NFKC", text)
    if text != original:
        changes.append("unicode_nfkc")

    if _CONTROL.search(text):
        text = _CONTROL.sub("", text)
        changes.append("remove_control_chars")

    if _HTML_TAG.search(text):
        text = _HTML_TAG.sub(" ", text)
        changes.append("strip_html_tags")
    if _HTML_ENTITY.search(text):
        text = html.unescape(text)
        changes.append("unescape_html_entities")

    if _MULTI_SPACE.search(text):
        text = _MULTI_SPACE.sub(" ", text)
        changes.append("collapse_spaces")
    if _MULTI_NEWLINE.search(text):
        text = _MULTI_NEWLINE.sub("\n\n", text)
        changes.append("collapse_newlines")

    if mask_pii:
        for pattern, label in (
            (_ID_CARD, "mask_id_card"),
            (_PHONE, "mask_phone"),
            (_EMAIL, "mask_email"),
        ):
            if pattern.search(text):
                text = pattern.sub("[已脱敏]", text)
                changes.append(label)

    if strip_url and _URL.search(text):
        text = _URL.sub("[链接]", text)
        changes.append("mask_url")

    text = text.strip()
    if text != original.strip():
        changes.append("strip_whitespace")
    return text, sorted(set(changes))


def clean_sample(sample: Sample) -> tuple[Sample, list[str]]:
    changes: list[str] = []
    new = Sample(
        system=normalize_text(sample.system)[0],
        instruction=normalize_text(sample.instruction)[0],
        input=normalize_text(sample.input)[0],
        output=normalize_text(sample.output, strip_url=True)[0],
        meta=dict(sample.meta),
    )
    if new.to_dict() == sample.to_dict():
        return new, changes

    for field in ("instruction", "input", "output", "system"):
        if getattr(new, field) != getattr(sample, field):
            changes.append(f"cleaned_{field}")

    # 结构性修正：问句缺失标点、答案首尾多余列表符号
    if new.instruction and new.instruction[-1] not in "？?。.!！":
        new.instruction = new.instruction + "？" if _looks_like_question(new.instruction) else new.instruction + "。"
        changes.append("fix_instruction_punct")

    return new, sorted(set(changes))


def _looks_like_question(text: str) -> bool:
    markers = ("吗", "什么", "如何", "怎么", "为什么", "是否", "哪些", "多少")
    return any(m in text for m in markers)


def drop_reason(sample: Sample, *, min_chars: int = 2000) -> str | None:
    """清洗阶段直接判死的样本，返回丢弃原因。"""
    if not sample.instruction.strip():
        return "missing_instruction"
    if not sample.output.strip():
        return "missing_output"
    if _PLACEHOLDER.search(sample.instruction + sample.output):
        return "placeholder_content"
    if sample.char_count > min_chars:
        return "too_long"
    if any(word in sample.output for word in SENSITIVE_WORDS):
        return "sensitive_word"
    return None


def clean_batch(samples: list[Sample]) -> tuple[list[Sample], dict[str, Any]]:
    kept: list[Sample] = []
    dropped: dict[str, int] = {}
    changed = 0

    for sample in samples:
        reason = drop_reason(sample)
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        new, changes = clean_sample(sample)
        if changes:
            changed += 1
            new.meta = {**new.meta, "clean_changes": changes}
        kept.append(new)

    return kept, {
        "total": len(samples),
        "changed": changed,
        "dropped": len(samples) - len(kept),
        "drop_reasons": dropped,
    }
