"""质检阶段：规则引擎。

设计要点：
1. 每条规则是纯函数，返回 (是否通过, 证据)，便于单测与规则增删；
2. severity 分 error / warning：error 直接拒收，warning 只扣分——避免「一条弱规则
   就把好样本全部拦掉」；
3. 所有判定都落 qc_records，人工复核时可看到具体证据而不只是结论。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from app.pipeline.base import QCVerdict, Sample

_HTML_TAG = re.compile(r"<[^>]+>")
_PLACEHOLDER = re.compile(r"(TODO|FIXME|XXX|待补充|待完善|lorem ipsum)", re.IGNORECASE)
_CJK = re.compile(r"[\u4e00-\u9fff]")
_SENSITIVE = ("身份证号", "银行卡号", "密码是", "密钥是")

Rule = Callable[[Sample, dict[str, Any]], tuple[bool, str]]


@dataclass(slots=True)
class QCRule:
    name: str
    severity: str  # error | warning
    weight: float
    check: Rule
    description: str = ""


def _output_present(sample: Sample, _: dict[str, Any]) -> tuple[bool, str]:
    return bool(sample.output.strip()), "output 为空"


def _output_min_length(sample: Sample, ctx: dict[str, Any]) -> tuple[bool, str]:
    limit = int(ctx.get("min_output_chars", 10))
    ok = len(sample.output.strip()) >= limit
    return ok, f"output 长度 {len(sample.output)} < {limit}"


def _prompt_not_empty(sample: Sample, _: dict[str, Any]) -> tuple[bool, str]:
    return bool(sample.prompt_text.strip()), "instruction 为空"


def _no_placeholder(sample: Sample, _: dict[str, Any]) -> tuple[bool, str]:
    match = _PLACEHOLDER.search(f"{sample.instruction} {sample.output}")
    return match is None, f"命中占位符: {match.group(0) if match else ''}"


def _no_html(sample: Sample, _: dict[str, Any]) -> tuple[bool, str]:
    match = _HTML_TAG.search(sample.output)
    return match is None, f"output 含 HTML 标签: {match.group(0)[:40] if match else ''}"


def _no_sensitive(sample: Sample, _: dict[str, Any]) -> tuple[bool, str]:
    hit = next((w for w in _SENSITIVE if w in sample.output), None)
    return hit is None, f"命中敏感词: {hit}"


def _not_echo(sample: Sample, _: dict[str, Any]) -> tuple[bool, str]:
    same = sample.output.strip() == sample.instruction.strip()
    return not same, "output 与 instruction 完全相同"


def _length_ratio(sample: Sample, ctx: dict[str, Any]) -> tuple[bool, str]:
    low = float(ctx.get("ratio_low", 0.05))
    high = float(ctx.get("ratio_high", 30.0))
    prompt_len = max(len(sample.prompt_text), 1)
    ratio = len(sample.output) / prompt_len
    return low <= ratio <= high, f"输出/输入长度比 {ratio:.2f} 超出 [{low}, {high}]"


def _language_consistent(sample: Sample, _: dict[str, Any]) -> tuple[bool, str]:
    prompt_has_cjk = bool(_CJK.search(sample.prompt_text))
    output_has_cjk = bool(_CJK.search(sample.output))
    return prompt_has_cjk == output_has_cjk or not prompt_has_cjk, "中英文混杂，语种不一致"


def _label_present(sample: Sample, _: dict[str, Any]) -> tuple[bool, str]:
    intent = (sample.meta.get("label") or {}).get("intent")
    return bool(intent), "缺少 intent 标签"


DEFAULT_RULES: list[QCRule] = [
    QCRule("output_present", "error", 1.0, _output_present, "答案非空"),
    QCRule("output_min_length", "error", 1.0, _output_min_length, "答案长度下限"),
    QCRule("prompt_not_empty", "error", 1.0, _prompt_not_empty, "问题非空"),
    QCRule("no_placeholder", "error", 1.0, _no_placeholder, "无占位符"),
    QCRule("no_sensitive", "error", 1.0, _no_sensitive, "无敏感信息"),
    QCRule("not_echo", "error", 0.8, _not_echo, "答案非问题复读"),
    QCRule("no_html", "warning", 0.3, _no_html, "无 HTML 残留"),
    QCRule("length_ratio", "warning", 0.3, _length_ratio, "长度比例合理"),
    QCRule("language_consistent", "warning", 0.2, _language_consistent, "语种一致"),
    QCRule("label_present", "warning", 0.4, _label_present, "已标注意图"),
]


def run_qc(
    sample: Sample,
    *,
    rules: list[QCRule] | None = None,
    ctx: dict[str, Any] | None = None,
) -> QCVerdict:
    rules = rules or DEFAULT_RULES
    ctx = ctx or {}

    failures: list[dict[str, str]] = []
    passed_weight = 0.0
    total_weight = 0.0
    blocked = False

    for rule in rules:
        total_weight += rule.weight
        try:
            ok, detail = rule.check(sample, ctx)
        except Exception as exc:  # noqa: BLE001 - 规则自身异常不应中断整批质检
            ok, detail = False, f"规则执行异常: {exc}"

        if ok:
            passed_weight += rule.weight
        else:
            failures.append({"rule": rule.name, "severity": rule.severity, "detail": detail})
            if rule.severity == "error":
                blocked = True

    score = round(passed_weight / total_weight, 4) if total_weight else 0.0
    return QCVerdict(passed=not blocked, score=score, failures=failures)


def summarize_verdicts(verdicts: list[QCVerdict]) -> dict[str, Any]:
    total = len(verdicts)
    passed = sum(1 for v in verdicts if v.passed)
    rule_hits: dict[str, int] = {}
    for verdict in verdicts:
        for failure in verdict.failures:
            rule_hits[failure["rule"]] = rule_hits.get(failure["rule"], 0) + 1
    return {
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "avg_score": round(sum(v.score for v in verdicts) / total, 4) if total else 0.0,
        "rule_hits": dict(sorted(rule_hits.items(), key=lambda x: x[1], reverse=True)),
    }
