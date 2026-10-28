"""纯函数与单模块单元测试（不依赖数据库）。

覆盖：文本切分、BM25、向量化确定性、清洗、去重、质检、导出、计算器安全、规划解析、评测指标。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.agent.planner import parse_plan
from app.evaluation.metrics import aggregate, answer_scores, retrieval_scores
from app.pipeline.base import Sample
from app.pipeline.cleaner import clean_batch, drop_reason, normalize_text
from app.pipeline.dedup import Deduper, hamming, simhash, simhash_hex
from app.pipeline.exporter import build_record, export_samples
from app.pipeline.qc import DEFAULT_RULES, run_qc
from app.rag.bm25 import BM25Index, tokenize
from app.rag.embedder import HashingEmbedder
from app.rag.splitter import split_text


class TestSplitter:
    def test_split_respects_size(self):
        text = "第一段内容。" * 50 + "\n\n" + "第二段内容。" * 50
        chunks = split_text(text, chunk_size=200, chunk_overlap=40)
        assert chunks
        assert all(c.char_count <= 200 + 20 for c in chunks)  # 允许标点带来的少量溢出
        assert [c.seq for c in chunks] == list(range(len(chunks)))

    def test_empty_text(self):
        assert split_text("   ") == []

    def test_overlap_larger_than_size_is_clamped(self):
        chunks = split_text("句子。" * 100, chunk_size=100, chunk_overlap=500)
        assert chunks  # 不应抛错或产生空结果


class TestBM25:
    def test_tokenize_mixed(self):
        tokens = tokenize("向量检索 RRF")
        assert any(t.startswith("b:向量") for t in tokens)
        assert "w:rrf" in tokens

    def test_ranking_prefers_relevant(self):
        index = BM25Index()
        index.add("a", "混合检索使用 RRF 融合向量与 BM25 结果")
        index.add("b", "今天天气不错适合出门散步")
        result = index.search("RRF 融合", top_k=2)
        assert result[0][0] == "a"
        assert result[0][1] > 0

    def test_empty_index(self):
        assert BM25Index().search("任意") == []


class TestEmbedder:
    def test_deterministic_across_instances(self):
        a = asyncio.run(HashingEmbedder(dim=64).embed_one("向量检索"))
        b = asyncio.run(HashingEmbedder(dim=64).embed_one("向量检索"))
        assert a == b, "哈希向量必须跨实例稳定（使用 blake2b 而非内置 hash）"

    def test_unit_norm(self):
        vec = asyncio.run(HashingEmbedder(dim=128).embed_one("测试文本归一化"))
        norm = sum(v * v for v in vec) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-6)


class TestCleaner:
    def test_normalize_removes_html_and_spaces(self):
        text, changes = normalize_text("<p>你好&nbsp;&nbsp;世界</p>")
        assert "<p>" not in text
        assert "strip_html_tags" in changes

    def test_pii_masked(self):
        text, changes = normalize_text("联系电话 13800138000")
        assert "13800138000" not in text
        assert "mask_phone" in changes

    def test_drop_missing_output(self):
        assert drop_reason(Sample(instruction="问题", output="")) == "missing_output"

    def test_drop_placeholder(self):
        assert drop_reason(Sample(instruction="问题", output="待补充")) == "placeholder_content"

    def test_clean_batch_stats(self):
        samples = [
            Sample(instruction="什么是 RAG", output="RAG 是检索增强生成"),
            Sample(instruction="空答案", output=""),
            Sample(instruction="占位", output="TODO"),
        ]
        kept, stats = clean_batch(samples)
        assert len(kept) == 1
        assert stats["dropped"] == 2


class TestDedup:
    def test_simhash_stable(self):
        assert simhash("相同文本") == simhash("相同文本")
        assert simhash_hex("相同文本") == simhash_hex("相同文本")

    def test_near_duplicate_detected(self):
        base = "如何配置知识库的切分参数与重叠长度"
        near = "如何配置知识库的切分参数和重叠长度"
        unrelated = "今天天气不错适合出门散步"
        # 阈值 14 的依据见 dedup 模块文档：改一字约 10~12，无关句子 24~30
        assert hamming(simhash(base), simhash(near)) <= 14
        assert hamming(simhash(base), simhash(unrelated)) > 14

    def test_deduper_blocks_repeat(self):
        d = Deduper(threshold=14)
        is_dup, _, _ = d.add("如何配置混合检索的权重")
        assert is_dup is False
        is_dup2, _, hit = d.add("如何配置混合检索的权重")
        assert is_dup2 is True and hit == 0

    def test_deduper_blocks_minor_rewrite(self):
        d = Deduper(threshold=14)
        d.add("混合检索使用RRF融合向量与BM25结果")
        is_dup, _, _ = d.add("混合检索使用RRF融合向量和BM25结果")
        assert is_dup is True


class TestQC:
    def test_clean_sample_passes(self):
        sample = Sample(
            instruction="什么是混合检索",
            output="混合检索同时使用向量召回与 BM25 召回，并通过 RRF 融合结果。",
            meta={"label": {"intent": "knowledge_qa"}},
        )
        verdict = run_qc(sample)
        assert verdict.passed is True
        assert verdict.score > 0.9

    def test_empty_output_blocks(self):
        verdict = run_qc(Sample(instruction="问题", output="", meta={"label": {"intent": "x"}}))
        assert verdict.passed is False
        assert any(f["rule"] == "output_present" for f in verdict.failures)

    def test_rules_are_enumerable(self):
        assert len(DEFAULT_RULES) >= 8
        assert {r.severity for r in DEFAULT_RULES} == {"error", "warning"}


class TestExporter:
    def test_chat_jsonl_format(self):
        record = build_record(Sample(instruction="问", output="答"), "chat_jsonl")
        assert record["messages"][-1] == {"role": "assistant", "content": "答"}

    def test_sharegpt_format(self):
        record = build_record(Sample(instruction="问", output="答"), "sharegpt")
        assert record["conversations"][0]["from"] == "human"

    def test_export_writes_file(self, tmp_path, monkeypatch):
        from app.pipeline import exporter

        monkeypatch.setattr(exporter.settings, "export_dir", str(tmp_path))
        result = export_samples([Sample(instruction="问", output="答")], dataset_id=999, version=1)
        assert result.count == 1
        with open(result.path, encoding="utf-8") as fh:
            line = json.loads(fh.readline())
        assert "messages" in line


class TestCalculatorTool:
    def test_safe_expression(self):
        from app.tools.executor import ToolExecutor

        result = asyncio.run(ToolExecutor().execute("calculator", {"expression": "2*(3+4)"}))
        assert result.ok and result.data["result"] == 14

    def test_rejects_code_injection(self):
        from app.tools.executor import ToolExecutor

        result = asyncio.run(
            ToolExecutor().execute("calculator", {"expression": "__import__('os').system('x')"})
        )
        assert result.ok is False

    def test_rejects_huge_power(self):
        from app.tools.executor import ToolExecutor

        result = asyncio.run(ToolExecutor().execute("calculator", {"expression": "9**9**9"}))
        assert result.ok is False

    def test_invalid_params_not_retried(self):
        from app.tools.executor import ToolExecutor

        result = asyncio.run(ToolExecutor().execute("calculator", {}))
        assert result.ok is False
        assert result.retries == 0


class TestPlannerParsing:
    def test_valid_plan(self):
        raw = json.dumps(
            {
                "intent": "computation",
                "need_tools": True,
                "tasks": [{"step": 1, "action": "calculator", "desc": "计算"}],
                "reasoning": "需要精确计算",
            }
        )
        plan = parse_plan(raw)
        assert plan.intent == "computation"
        assert plan.tasks[0].action == "calculator"

    def test_unknown_tool_rewritten(self):
        raw = json.dumps({"intent": "mixed", "tasks": [{"action": "nonexistent_tool"}]})
        plan = parse_plan(raw)
        assert plan.tasks[0].action == "answer"

    def test_broken_json_degrades(self):
        plan = parse_plan("这不是 JSON")
        assert plan.degraded is True
        assert plan.tasks[0].action == "answer"


class TestPromptRender:
    def test_placeholder_replaced(self):
        from app.agent.prompts import render

        out = render("最多 {max_tasks} 个任务", {"max_tasks": 6})
        assert "6" in out and "{max_tasks}" not in out

    def test_json_braces_preserved(self):
        from app.agent.prompts import DEFAULT_TEMPLATES, render

        out = render(DEFAULT_TEMPLATES["plan"]["content"], {"max_tasks": 4})
        assert '"intent"' in out, "JSON 示例中的花括号必须原样保留"
        assert "不超过 4 个" in out

    def test_decide_hint_injected(self):
        from app.agent.prompts import DEFAULT_TEMPLATES, render

        out = render(DEFAULT_TEMPLATES["decide"]["content"], {"extra_hint": "规划已指定工具"})
        assert "规划已指定工具" in out


class _StubGateway:
    """最小 LLM 网关桩：只返回固定 content，用于测试判定逻辑而不调用真实模型。"""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def chat(self, messages, **kwargs):  # noqa: ANN001, ANN003
        from types import SimpleNamespace

        self.calls += 1
        return SimpleNamespace(content=self.content, usage=None)


class TestGroundedness:
    """答案忠实度校验（基于知识库反查）。"""

    def _sample(self, output: str):
        return Sample(instruction="切片重叠长度是多少", output=output)

    def _hits(self, score: float = 0.9):
        return [
            {
                "chunk_id": 11,
                "doc_name": "rag-design.md",
                "score": score,
                "content": "相邻块之间保留 80 个字符的重叠，避免答案被切断在边界上。",
            }
        ]

    async def test_contradicted_answer_detected(self):
        from app.pipeline.groundedness import judge_sample

        gateway = _StubGateway('{"verdict":"contradicted","reason":"片段说 80，答案说 20"}')

        async def fake_search(*args, **kwargs):
            return self._hits()

        res = await judge_sample(self._sample("重叠长度是 20 个字符"), gateway, search_fn=fake_search)
        assert res["verdict"] == "contradicted"
        assert res["used_model"] is True
        assert res["evidence_chunk"] == 11

    async def test_supported_answer(self):
        from app.pipeline.groundedness import judge_sample

        gateway = _StubGateway('{"verdict":"supported","reason":"与片段一致"}')

        async def fake_search(*args, **kwargs):
            return self._hits()

        res = await judge_sample(self._sample("相邻切片保留 80 个字符重叠"), gateway, search_fn=fake_search)
        assert res["verdict"] == "supported"

    async def test_no_hits_skips_model_call(self):
        """检索不到内容时不应付模型费用。"""
        from app.pipeline.groundedness import judge_sample

        gateway = _StubGateway('{"verdict":"supported"}')

        async def empty_search(*args, **kwargs):
            return []

        res = await judge_sample(self._sample("任意答案"), gateway, search_fn=empty_search)
        assert res["verdict"] == "insufficient"
        assert res["used_model"] is False
        assert gateway.calls == 0

    async def test_low_score_skips_model_call(self):
        from app.pipeline.groundedness import judge_sample

        gateway = _StubGateway('{"verdict":"supported"}')

        async def weak_search(*args, **kwargs):
            return self._hits(score=0.1)

        res = await judge_sample(
            self._sample("任意答案"), gateway, search_fn=weak_search, score_threshold=0.35
        )
        assert res["verdict"] == "insufficient"
        assert gateway.calls == 0

    def test_parse_invalid_verdict_falls_back(self):
        from app.pipeline.groundedness import parse_verdict

        assert parse_verdict('{"verdict":"maybe"}')[0] == "insufficient"
        assert parse_verdict("不是 JSON")[0] == "insufficient"


class TestEvalJudge:
    """评测 LLM-as-judge。"""

    async def test_correct_verdict(self):
        from app.evaluation.judge import judge_answer

        res = await judge_answer(
            _StubGateway('{"verdict":"correct","score":0.95,"reason":"命中要点"}'),
            query="(12+8)*3/4 等于多少",
            expected="15",
            answer="结果是 15",
        )
        assert res["verdict"] == "correct"
        assert res["score"] == 0.95
        assert res["used"] is True

    async def test_off_topic_verdict(self):
        from app.evaluation.judge import judge_answer

        res = await judge_answer(
            _StubGateway('{"verdict":"off_topic","reason":"答非所问"}'),
            query="切片重叠多少",
            expected="80",
            answer="今天天气不错",
        )
        assert res["verdict"] == "off_topic"
        assert res["score"] == 0.0

    async def test_unparsable_output_is_unknown(self):
        from app.evaluation.judge import judge_answer

        res = await judge_answer(
            _StubGateway("我觉得还行"), query="q", expected="15", answer="a"
        )
        assert res["verdict"] == "unknown"
        assert res["score"] == 0.0


class TestMetrics:
    def test_retrieval_hit_and_mrr(self):
        hits = [{"content": "无关内容"}, {"content": "混合检索使用 RRF"}]
        scores = retrieval_scores(hits, "RRF", 5)
        assert scores["hit"] is True
        assert scores["hit_top1"] is False
        assert scores["rank"] == 2
        assert scores["mrr"] == 0.5

    def test_answer_scores_requires_tools(self):
        scores = answer_scores("答案是 15", "15", ["calculator"], ["calculator"])
        assert scores["passed"] is True

    def test_answer_fails_when_tool_missing(self):
        scores = answer_scores("答案是 15", "15", ["calculator"], [])
        assert scores["passed"] is False

    def test_negative_assertion_blocks_reversed_answer(self):
        """回归：答案明确否定期望值时，不能因为"包含关键词"就判通过。"""
        scores = answer_scores("这个结论不对，不是 15，正确值是 16", "15|!16", [], [])
        assert scores["passed"] is False
        assert scores["must_not_violations"] == ["16"]

    def test_negative_assertion_passes_correct_answer(self):
        scores = answer_scores("答案是 15", "15|!16", [], [])
        assert scores["passed"] is True
        assert scores["must_not_violations"] == []

    def test_aggregate(self):
        metrics = aggregate(
            [
                {"passed": True, "latency_ms": 100, "scores": {"hit": True, "mrr": 1.0}},
                {"passed": False, "latency_ms": 300, "scores": {"hit": False, "mrr": 0.0}},
            ]
        )
        assert metrics["pass_rate"] == 0.5
        assert metrics["hit_rate"] == 0.5
        assert metrics["latency_p95_ms"] >= 300

    def test_aggregate_top1_metric(self):
        metrics = aggregate(
            [
                {"passed": True, "latency_ms": 10, "scores": {"hit": True, "hit_top1": True, "mrr": 1.0}},
                {"passed": True, "latency_ms": 20, "scores": {"hit": True, "hit_top1": False, "mrr": 0.5}},
            ]
        )
        assert metrics["hit_rate"] == 1.0
        assert metrics["hit_rate_top1"] == 0.5


class TestNoEvidenceGate:
    """回归：无证据时不得调用模型生成答案（防止幻觉）。"""

    def _rt(self, observations):
        from app.agent.graph import Runtime

        return Runtime(observations=observations)

    def _obs(self, ok: bool, evidence: bool | None = None):
        from app.agent.graph import Observation

        return Observation(
            tool="calculator",
            ok=ok,
            content="x",
            latency_ms=1.0,
            evidence=ok if evidence is None else evidence,
        )

    def test_all_failed_triggers_gate(self):
        from app.agent.graph import has_no_evidence

        assert has_no_evidence(self._rt([self._obs(False), self._obs(False)])) is True

    def test_success_observation_bypasses_gate(self):
        from app.agent.graph import has_no_evidence

        assert has_no_evidence(self._rt([self._obs(True)])) is False

    def test_success_but_empty_result_triggers_gate(self):
        """调用成功但结果为空（如检索 0 命中）同样视为无证据。"""
        from app.agent.graph import has_no_evidence

        assert has_no_evidence(self._rt([self._obs(True, evidence=False)])) is True

    def test_citations_bypass_gate(self):
        from app.agent.graph import has_no_evidence

        rt = self._rt([self._obs(False)])
        rt.citations = [{"chunk_id": 1, "doc_name": "a.md", "score": 1.0, "snippet": "x", "index": 1}]
        assert has_no_evidence(rt) is False

    def test_no_observation_bypasses_gate(self):
        from app.agent.graph import has_no_evidence

        assert has_no_evidence(self._rt([])) is False

    def test_observation_is_evidence_rules(self):
        from app.agent.graph import observation_is_evidence
        from app.tools.executor import ToolResult

        empty_hits = ToolResult(tool="knowledge_search", ok=True, data={"hit_count": 0, "hits": []})
        with_hits = ToolResult(tool="knowledge_search", ok=True, data={"hit_count": 2, "hits": [{}, {}]})
        calc = ToolResult(tool="calculator", ok=True, data={"result": 15})
        failed = ToolResult(tool="calculator", ok=False, error="boom")

        assert observation_is_evidence("knowledge_search", empty_hits) is False
        assert observation_is_evidence("knowledge_search", with_hits) is True
        assert observation_is_evidence("calculator", calc) is True
        assert observation_is_evidence("calculator", failed) is False


class TestParser:
    """回归：PDF 真实文件解析（此前只有代码路径，没有真实文件验证）。

    两个夹具都是真实生成的 PDF：`sample_text.pdf` 含中文文本层（2 页），
    `sample_scanned.pdf` 只有图形、没有文本层，用于模拟扫描件——
    后者必须被 `empty_document` 明确拦下，而不是静默入库一个空文档。
    """

    FIXTURES = Path(__file__).parent / "fixtures"

    def test_pdf_with_text_layer_extracts_chinese(self):
        from app.rag.parser import extract_text

        doc = extract_text("sample.pdf", (self.FIXTURES / "sample_text.pdf").read_bytes())
        assert "向量检索评估方法说明" in doc.text
        assert "平均倒数排名" in doc.text
        assert doc.meta["pages"] == 2
        assert len(doc.text) > 300

    def test_scanned_pdf_raises_empty_document(self):
        from app.core.exceptions import AppError
        from app.rag.parser import extract_text

        with pytest.raises(AppError) as exc:
            extract_text("scan.pdf", (self.FIXTURES / "sample_scanned.pdf").read_bytes())
        assert exc.value.code == "empty_document"

    def test_plain_text_passthrough(self):
        from app.rag.parser import extract_text

        doc = extract_text("note.txt", "纯文本内容".encode("utf-8"))
        assert doc.text == "纯文本内容"

    def test_docx_extracts_paragraphs_and_tables(self):
        from app.rag.parser import extract_text

        doc = extract_text("sample.docx", (self.FIXTURES / "sample_docx.docx").read_bytes())
        assert "检索参数调优手册" in doc.text
        assert "top_k" in doc.text  # 表格里的单元格
        assert doc.meta["tables"] == 1
        assert doc.meta["table_rows"] == 3

    def test_docx_table_stays_in_its_section(self):
        """回归：表格不能被排到文末。

        早期实现分别遍历 paragraphs 与 tables，所有表格都堆在文末，脱离了
        它所属的小节；切分后表格与标题错位，检索和引用都会失真。
        """
        from app.rag.parser import extract_text

        doc = extract_text("sample.docx", (self.FIXTURES / "sample_docx.docx").read_bytes())
        table_head = doc.text.find("参数 | 默认值")
        next_section = doc.text.find("三、调参禁忌")
        assert table_head > 0
        assert next_section > table_head, "表格应出现在其所属小节内，而不是文末"


class TestMasking:
    """回归：日志脱敏。

    样例使用公开惯用的「测试占位号码」写法（如 138 0013 8000 这类文档示例），
    仅用于验证正则，不指向任何真实个人。
    """

    def test_phone_masked_but_keeps_edges(self):
        from app.core.masking import mask_text

        masked = mask_text("联系电话 13800138000")
        assert "13800138000" not in masked
        assert "138****8000" in masked

    def test_id_card_masked(self):
        from app.core.masking import mask_text

        raw = "110101199003074567"
        masked = mask_text("证件 " + raw)
        assert raw not in masked
        assert masked.startswith("证件 110101")

    def test_id_card_not_treated_as_bank_card(self):
        """18 位身份证不能被 16~19 位的银行卡规则重复处理。"""
        from app.core.masking import mask_text

        masked = mask_text("110101199003074567")
        assert masked.count("*") == 8

    def test_email_and_secret_masked(self):
        from app.core.masking import mask_text

        masked = mask_text("邮箱 someone@example.com 密钥 sk-abcdefgh00000078")
        assert "someone@example.com" not in masked
        assert "sk-abcdefgh00000078" not in masked
        assert "example.com" in masked  # 域名保留，便于判断是哪个服务

    def test_key_value_secret_masked(self):
        from app.core.masking import mask_text

        assert "abc123xyz" not in mask_text("password=abc123xyz")
        assert "[已脱敏]" in mask_text("token: abc123xyz")

    def test_mapping_masks_sensitive_keys_and_nested(self):
        from app.core.masking import mask_any

        data = {
            "query": "用户电话 13800138000",
            "password": "p@ssw0rd",
            "nested": {"token": "tk-000000"},
            "items": ["13900139000"],
        }
        out = mask_any(data)
        assert out["password"] == "[已脱敏]"
        assert out["nested"]["token"] == "[已脱敏]"
        assert "13800138000" not in out["query"]
        assert "13900139000" not in out["items"][0]

    def test_filter_masks_log_record(self):
        """关键回归：Filter 层兜底——调用点忘记脱敏也不会泄露。"""
        import logging

        from app.core.logging import MaskingFilter

        record = logging.LogRecord(
            "app.test", logging.INFO, __file__, 1, "用户问了：我的号码 13800138000", None, None
        )
        MaskingFilter().filter(record)
        assert "13800138000" not in record.getMessage()

    def test_filter_masks_extra_fields(self):
        """关键回归：extra 自定义字段同样要脱敏。

        早期只擦 msg/args —— 纯文本格式下 extra 不进输出，看不出问题；
        改成 JSON 后 extra 会原样带出，实测泄露手机号与密钥。
        """
        import logging

        from app.core.logging import JsonFormatter, MaskingFilter, TraceIdFilter

        record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "检索请求", None, None)
        record.query = "用户手机 13800138000"
        record.payload = {"token": "sk-abcdefgh00000078"}
        TraceIdFilter().filter(record)
        MaskingFilter().filter(record)
        out = JsonFormatter().format(record)

        assert "13800138000" not in out
        assert "sk-abcdefgh00000078" not in out
        assert "138****8000" in out  # 保留可定位的前后位
        assert "[已脱敏]" in out  # 字段名命中敏感键则整值擦除

    def test_json_formatter_emits_parseable_line(self):
        import logging

        from app.core.logging import JsonFormatter, TraceIdFilter, bind_trace_id

        bind_trace_id("t-1")
        record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "done %s", (1,), None)
        TraceIdFilter().filter(record)
        payload = json.loads(JsonFormatter().format(record))
        assert payload["level"] == "INFO"
        assert payload["logger"] == "app.test"
        assert payload["trace"] == "t-1"
        assert payload["msg"] == "done 1"
        assert "ts" in payload


class TestSplitterHeading:
    """回归：标题不能单独成块。

    单独成块的标题语义相似度虚高，会把含答案的正文块挤出首位（实测发生过），
    且引用展示只剩一行标题、用户读不到内容。
    """

    def test_heading_merged_into_following_paragraph(self):
        from app.rag.splitter import split_text

        # 每段都要超过 min_chars(20)，否则会被「短块合并」规则并进上一块，
        # 那样测的就不是标题合并而是短块合并了
        text = (
            "# 一级标题\n\n这是标题下的第一段正文，内容足够长以超过二十个字符的阈值。\n\n"
            "## 小节标题\n\n小节正文内容也需要足够长，才能形成独立于上一块的片段。"
        )
        chunks = split_text(text)
        assert len(chunks) == 2
        assert chunks[0].content.startswith("# 一级标题")
        assert "第一段正文" in chunks[0].content
        assert chunks[1].content.startswith("## 小节标题")
        assert "小节正文内容" in chunks[1].content

    def test_short_first_block_without_hash_merged(self):
        from app.rag.splitter import split_text

        text = "文档标题\n\n这是一段足够长的正文内容，用来验证短标题块会被并入后一段。"
        chunks = split_text(text)
        assert len(chunks) == 1
        assert chunks[0].content.startswith("文档标题")

    def test_document_ending_with_heading_does_not_dangle(self):
        from app.rag.splitter import split_text

        text = "# 标题\n\n正文段落，内容需要足够长一些才能形成独立块。\n\n## 结尾小节"
        chunks = split_text(text)
        assert all(len(c.content) > 0 for c in chunks)
        assert "## 结尾小节" in chunks[-1].content

    def test_plain_text_heading_merged_forward(self):
        """回归：没有 # 前缀的小节标题（DOCX/PDF 常见）必须并入**后**一段。

        早期它与「短段落」共用一条规则，被追加到**前**一段末尾，
        结果标题和它引出的正文被拆进两个切片。
        """
        from app.rag.splitter import split_text

        text = (
            "本文说明检索参数的含义与调整原则，所有调整都应以评测集为准。\n\n"
            "一、权重配置\n\n"
            "向量权重与 BM25 权重之和应当为一，向量偏重语义近似，BM25 偏重精确术语。"
        )
        chunks = split_text(text)
        assert len(chunks) == 2
        assert chunks[0].content.startswith("本文说明")
        assert "权重配置" not in chunks[0].content, "标题不该挂在前一段末尾"
        assert chunks[1].content.startswith("一、权重配置")
        assert "向量权重" in chunks[1].content

    def test_sentence_ending_short_block_merged_backward(self):
        """短段落（以句末标点结尾）仍并入前一段——方向与标题相反。"""
        from app.rag.splitter import split_text

        text = (
            "这是一段足够长的正文内容，用来验证短段落会被并入前一块的行为。\n\n"
            "补充一句。"
        )
        chunks = split_text(text)
        assert len(chunks) == 1
        assert chunks[0].content.endswith("补充一句。")


class TestLocalEmbedderGuard:
    """回归：本地模型缺失时必须给出可操作的报错，而不是抛底层异常。"""

    def test_missing_model_dir_raises_embed_model_missing(self, tmp_path):
        from app.core.exceptions import AppError
        from app.rag.embedder import LocalBgeEmbedder

        emb = LocalBgeEmbedder(path=str(tmp_path / "not_exist"))
        with pytest.raises(AppError) as exc:
            emb._load()
        assert exc.value.code == "embed_model_missing"

    def test_collection_name_keeps_legacy_name_for_hashing(self, monkeypatch):
        """hashing 必须沿用旧集合名，否则不换模型的部署会突然检索不到数据。"""
        from app.rag import vector_store

        class _Fake:
            name = "hashing"

        monkeypatch.setattr("app.rag.embedder.get_embedder", lambda: _Fake())
        assert vector_store.collection_name(2) == "af_kb_2"

        _Fake.name = "bge-m3"
        assert vector_store.collection_name(2) == "af_kb_2_bge-m3"
