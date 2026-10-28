"""端到端接口测试（ASGI + 独立 MySQL 测试库 + Mock 模型）。

覆盖主链路：健康检查 → 知识库 → 检索 → 对话（工具调用 + 引用）→
数据流水线（清洗/去重/标注/质检/导出）→ 评测（检索 + 端到端）。
"""

from __future__ import annotations

import json


def _ok(resp):
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]


class TestSystem:
    def test_health_reports_dependencies(self, app_client):
        data = _ok(app_client.get("/api/system/health"))
        assert data["database"]["ok"] is True
        assert data["vector_store"]["ok"] is True
        assert data["llm"]["mock"] is True

    def test_overview(self, app_client):
        data = _ok(app_client.get("/api/system/overview"))
        assert "agent_runs" in data

    def test_config_hides_secrets(self, app_client):
        data = _ok(app_client.get("/api/system/config"))
        assert "api_key" not in json.dumps(data).lower()


class TestKnowledge:
    def test_upload_search_and_delete(self, app_client, sample_doc):
        kb = _ok(
            app_client.post(
                "/api/knowledge/bases", json={"name": "测试知识库", "description": "e2e"}
            )
        )
        kb_id = kb["id"]

        ingested = _ok(
            app_client.post(
                "/api/knowledge/ingest-text",
                json={"kb_id": kb_id, "filename": "intro.md", "text": sample_doc},
            )
        )
        assert ingested["chunk_count"] >= 1

        docs = _ok(app_client.get(f"/api/knowledge/bases/{kb_id}/documents"))
        assert len(docs) == 1
        doc_id = docs[0]["id"]

        chunks = _ok(app_client.get(f"/api/knowledge/documents/{doc_id}/chunks"))
        assert len(chunks) == docs[0]["chunk_count"]

        result = _ok(
            app_client.post(
                "/api/knowledge/search",
                json={"query": "混合检索用什么融合", "kb_id": kb_id, "top_k": 3},
            )
        )
        assert result["hit_count"] >= 1
        top = result["hits"][0]
        assert top["vector_rank"] is not None
        assert "RRF" in result["hits"][0]["content"] or top["bm25_rank"] is not None

        _ok(app_client.delete(f"/api/knowledge/documents/{doc_id}"))
        assert _ok(app_client.get(f"/api/knowledge/bases/{kb_id}/documents")) == []

    def test_duplicate_document_conflict(self, app_client, sample_doc):
        kb = _ok(app_client.post("/api/knowledge/bases", json={"name": "重复检测库"}))
        payload = {"kb_id": kb["id"], "filename": "dup.md", "text": sample_doc}
        _ok(app_client.post("/api/knowledge/ingest-text", json=payload))
        resp = app_client.post("/api/knowledge/ingest-text", json=payload)
        assert resp.status_code == 409
        assert resp.json()["code"] == "conflict"


    def test_kb_scope_enforced(self, app_client):
        """会话绑定知识库后，检索不得越界到其他知识库。"""
        kb_a = _ok(app_client.post("/api/knowledge/bases", json={"name": "范围库A"}))
        kb_b = _ok(app_client.post("/api/knowledge/bases", json={"name": "范围库B"}))
        _ok(
            app_client.post(
                "/api/knowledge/ingest-text",
                json={
                    "kb_id": kb_a["id"],
                    "filename": "a-only.md",
                    "text": "A 库专属内容：蓝色协议的默认端口是 7001，仅用于内部服务。",
                },
            )
        )
        _ok(
            app_client.post(
                "/api/knowledge/ingest-text",
                json={
                    "kb_id": kb_b["id"],
                    "filename": "b-only.md",
                    "text": "B 库专属内容：绿色协议的默认端口是 9002，对外网开放。",
                },
            )
        )

        data = _ok(
            app_client.post(
                "/api/chat",
                json={"query": "知识库里蓝色协议的端口是多少", "kb_id": kb_a["id"], "stream": False},
            )
        )
        citations = data["citations"]
        assert citations, "应命中 A 库内容"
        assert all(c["doc_name"] == "a-only.md" for c in citations), "不得检索到其他知识库的文档"

    def test_knowledge_requires_valid_kb(self, app_client):
        resp = app_client.post(
            "/api/knowledge/ingest-text", json={"kb_id": 99999, "filename": "x.md", "text": "内容"}
        )
        assert resp.status_code == 404


class TestChat:
    def test_chat_with_knowledge_tool(self, app_client, sample_doc):
        kb = _ok(app_client.post("/api/knowledge/bases", json={"name": "对话测试库"}))
        _ok(
            app_client.post(
                "/api/knowledge/ingest-text",
                json={"kb_id": kb["id"], "filename": "chat.md", "text": sample_doc},
            )
        )

        data = _ok(
            app_client.post(
                "/api/chat",
                json={
                    "query": "知识库里说混合检索用什么融合算法？",
                    "kb_id": kb["id"],
                    "stream": False,
                },
            )
        )
        assert data["status"] == "succeeded"
        assert data["answer"]
        assert data["conversation_id"] > 0
        assert "knowledge_search" in data["tool_calls"]
        assert data["citations"], "应产生引用溯源信息"

    def test_conversation_history_persisted(self, app_client):
        first = _ok(app_client.post("/api/chat", json={"query": "你好", "stream": False}))
        conv_id = first["conversation_id"]
        messages = _ok(app_client.get(f"/api/conversations/{conv_id}/messages"))
        roles = [m["role"] for m in messages]
        assert roles[0] == "user"
        assert "assistant" in roles

    def test_run_trace_available(self, app_client):
        data = _ok(app_client.post("/api/chat", json={"query": "帮我算 7*8", "stream": False}))
        run = _ok(app_client.get(f"/api/runs/{data['run_id']}"))
        assert run["status"] == "succeeded"
        assert isinstance(run["steps"], list) and run["steps"]
        nodes = {s["node"] for s in run["steps"]}
        assert {"plan", "answer"}.issubset(nodes)

    def test_no_evidence_gate_skips_model(self, app_client):
        """回归：工具全部失败且无引用时，走硬闸门返回固定话术，不调用模型生成。"""
        data = _ok(
            app_client.post(
                "/api/chat", json={"query": "帮我算 2**9999 等于多少", "stream": False}
            )
        )
        assert "calculator" in data["tool_calls"], "应确实调用了工具"
        assert "无法确认" in data["answer"], "应命中硬闸门话术"
        assert data["status"] == "succeeded"

    def test_sse_stream_events(self, app_client):
        with app_client.stream(
            "POST", "/api/chat/stream", json={"query": "帮我算 3+4", "stream": True}
        ) as resp:
            assert resp.status_code == 200
            events = []
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
        assert "run_started" in events
        assert "token" in events
        assert "run_finished" in events


class TestToolsApi:
    def test_list_and_invoke(self, app_client):
        tools = _ok(app_client.get("/api/tools"))
        names = {t["name"] for t in tools}
        assert {"calculator", "current_time", "knowledge_search", "sql_query"} <= names

        result = _ok(
            app_client.post("/api/tools/calculator/invoke", json={"args": {"expression": "12*12"}})
        )
        assert result["ok"] is True and result["data"]["result"] == 144

    def test_sql_tool_rejects_write(self, app_client):
        result = _ok(
            app_client.post(
                "/api/tools/sql_query/invoke", json={"args": {"sql": "DELETE FROM conversations"}}
            )
        )
        assert result["ok"] is False
        assert "只读" in (result["error"] or "")

    def test_sql_tool_blocks_system_tables(self, app_client):
        """回归：不得读取 MySQL 系统库（曾实测可读 mysql.user）。"""
        result = _ok(
            app_client.post(
                "/api/tools/sql_query/invoke",
                json={"args": {"sql": "SELECT user, host FROM mysql.user"}},
            )
        )
        assert result["ok"] is False

    def test_sql_tool_blocks_non_business_table(self, app_client):
        """表名白名单：非本平台业务表一律拒绝。"""
        result = _ok(
            app_client.post(
                "/api/tools/sql_query/invoke", json={"args": {"sql": "SELECT * FROM orders"}}
            )
        )
        assert result["ok"] is False
        assert "不允许访问表" in (result["error"] or "")

    def test_sql_tool_allows_business_table(self, app_client):
        result = _ok(
            app_client.post(
                "/api/tools/sql_query/invoke",
                json={"args": {"sql": "SELECT COUNT(*) AS c FROM conversations"}},
            )
        )
        assert result["ok"] is True
        assert result["data"]["columns"] == ["c"]

    def test_sql_tool_blocks_metadata_queries(self, app_client):
        """回归：SHOW DATABASES / 系统变量属于信息泄露，应拒绝；SHOW TABLES 放行。"""
        for sql in ("SHOW DATABASES", "SELECT @@version", "SHOW VARIABLES LIKE 'datadir'"):
            result = _ok(
                app_client.post("/api/tools/sql_query/invoke", json={"args": {"sql": sql}})
            )
            assert result["ok"] is False, f"{sql} 应被拒绝"

        allowed = _ok(
            app_client.post("/api/tools/sql_query/invoke", json={"args": {"sql": "SHOW TABLES"}})
        )
        assert allowed["ok"] is True

    def test_openai_schema_shape(self, app_client):
        data = _ok(app_client.get("/api/tools/schemas"))
        assert data["count"] >= 5
        first = data["tools"][0]
        assert first["type"] == "function"
        assert "parameters" in first["function"]


class TestPromptWiring:
    """回归：数据库中的启用模板必须真正生效（曾因未传 session 而永不读取）。"""

    async def test_db_prompt_overrides_code_default(self):
        from sqlalchemy import delete

        from app.agent.prompts import load_template
        from app.db.session import AsyncSessionLocal
        from app.models.prompt import PromptTemplate

        async with AsyncSessionLocal() as session:
            session.add(
                PromptTemplate(
                    name="agent.decide",
                    scene="agent",
                    version=1,
                    is_active=True,
                    content="[NODE:DECIDE]数据库覆盖版本，提示={extra_hint}",
                )
            )
            await session.commit()
            try:
                rendered = await load_template(
                    "decide", session=session, variables={"extra_hint": "HINT-OK"}
                )
                assert "数据库覆盖版本" in rendered
                assert "HINT-OK" in rendered
            finally:
                await session.execute(
                    delete(PromptTemplate).where(PromptTemplate.name == "agent.decide")
                )
                await session.commit()

    async def test_missing_session_falls_back_to_default(self):
        from app.agent.prompts import load_template

        rendered = await load_template("reflect")
        assert "[NODE:REFLECT]" in rendered

    def test_prompt_create_update_activate(self, app_client):
        """回归：PATCH 模板曾因 updated_at 过期触发 MissingGreenlet 而 500。"""
        created = _ok(
            app_client.post(
                "/api/prompts",
                json={"name": "temp.crud", "scene": "agent", "content": "初始内容", "is_active": True},
            )
        )
        pid = created["id"]

        patched = _ok(
            app_client.patch(
                f"/api/prompts/{pid}", json={"content": "修改后内容", "is_active": False}
            )
        )
        assert patched["content"] == "修改后内容"
        assert patched["is_active"] is False
        assert patched["updated_at"] is not None

        activated = _ok(app_client.post(f"/api/prompts/{pid}/activate"))
        assert activated["is_active"] is True


class TestDataPipeline:
    def test_full_pipeline_and_export(self, app_client):
        dataset = _ok(
            app_client.post("/api/datasets", json={"name": "e2e 数据集", "task_type": "sft"})
        )
        ds_id = dataset["id"]

        items = [
            {
                "instruction": "什么是混合检索",
                "output": "混合检索结合向量召回与 BM25 召回，通过 RRF 融合两路排名。",
            },
            {
                "instruction": "什么是混合检索",
                "output": "混合检索结合向量召回与 BM25 召回，通过 RRF 融合两路排名。",
            },
            {"instruction": "占位样本", "output": "TODO"},
            {"instruction": "空答案样本", "output": ""},
            {
                "instruction": "<p>如何统计运行耗时</p>",
                "output": "通过 AgentRun.duration_ms 字段统计每次运行耗时。",
            },
            # 该样本可过清洗与去重，但答案过短会在质检阶段被判 error，用于验证质检留痕
            {"instruction": "请简短回答这个问题", "output": "好的"},
        ]
        _ok(
            app_client.post(
                f"/api/datasets/{ds_id}/items", json={"items": items, "split": "train"}
            )
        )

        result = _ok(
            app_client.post(f"/api/datasets/{ds_id}/pipeline", json={"stages": None})
        )
        assert result["clean"]["total"] == 6
        assert result["clean"]["dropped"] >= 2  # 占位与空答案被丢弃
        assert result["dedup"]["duplicates"] >= 1
        assert result["qc"]["total"] >= 2
        assert result["qc"]["passed"] >= 1
        assert result["stats"]["pass_rate"] > 0

        exported = _ok(
            app_client.post(f"/api/datasets/{ds_id}/export", json={"format": "chat_jsonl"})
        )
        assert exported["count"] >= 1
        assert exported["version"] == 1

        versions = _ok(app_client.get(f"/api/datasets/{ds_id}/versions"))
        assert len(versions) == 1 and versions[0]["item_count"] == exported["count"]

        stats = _ok(app_client.get(f"/api/datasets/{ds_id}/stats"))
        assert stats["by_status"]["passed"] >= 1

        # 回归：autoflush=False 下统计前未 flush 会导致计数恒为 0、状态停在 draft
        datasets_after = _ok(app_client.get("/api/datasets"))
        current = next(d for d in datasets_after if d["id"] == ds_id)
        assert current["passed_count"] == stats["by_status"]["passed"]
        assert current["status"] == "ready"

    def test_dedup_is_idempotent(self, app_client):
        """回归：二次执行去重不得把样本与自身比对而整批判为重复。"""
        ds = _ok(app_client.post("/api/datasets", json={"name": "去重幂等性数据集"}))
        _ok(
            app_client.post(
                f"/api/datasets/{ds['id']}/items",
                json={
                    "items": [
                        {"instruction": "甲问题内容", "output": "甲的答案内容足够长一些"},
                        {"instruction": "乙问题内容", "output": "乙的答案内容同样足够长"},
                        {"instruction": "丙问题内容", "output": "丙的答案内容也不短啊"},
                    ]
                },
            )
        )
        first = _ok(
            app_client.post(f"/api/datasets/{ds['id']}/pipeline", json={"stages": ["clean", "dedup"]})
        )
        assert first["dedup"]["duplicates"] == 0
        assert first["dedup"]["kept"] == 3

        second = _ok(
            app_client.post(f"/api/datasets/{ds['id']}/pipeline", json={"stages": ["dedup"]})
        )
        assert second["dedup"]["duplicates"] == 0, "二次执行不应产生重复判定"
        assert second["dedup"]["kept"] == 3

    def test_qc_records_persisted(self, app_client):
        datasets = _ok(app_client.get("/api/datasets"))
        ds_id = next(d["id"] for d in datasets if d["name"] == "e2e 数据集")
        records = _ok(app_client.get(f"/api/datasets/{ds_id}/qc"))
        assert records, "存在被拒样本时应留下质检证据"
        assert all(r["rule"] for r in records)
        assert any(r["severity"] == "error" for r in records)


class TestGroundednessStage:
    """阶段级回归：能拦住「服务层调用参数与 judge_sample 签名不匹配」这类错误。"""

    async def test_contradicted_item_is_rejected(self, monkeypatch):
        from sqlalchemy import delete

        from app.models.dataset import Dataset, DatasetItem
        from app.pipeline import groundedness as gd
        from app.pipeline import service as pipeline

        hits = [
            {
                "chunk_id": 1,
                "doc_name": "doc.md",
                "score": 0.95,
                "content": "相邻切片保留 80 个字符重叠。",
            }
        ]

        async def fake_search(query, *, kb_id=None, top_k=3, **kwargs):
            return hits

        monkeypatch.setattr(gd, "_default_search", fake_search)
        monkeypatch.setattr(gd, "LLMGateway", gd.LLMGateway)  # 保持类型引用

        class _Stub:
            async def chat(self, messages, **kwargs):
                from types import SimpleNamespace

                return SimpleNamespace(content='{"verdict":"contradicted","reason":"与片段冲突"}')

        monkeypatch.setattr(pipeline, "get_gateway", lambda: _Stub())

        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            ds = Dataset(name="groundedness 测试集", task_type="sft")
            session.add(ds)
            await session.flush()
            item = DatasetItem(
                dataset_id=ds.id,
                status="passed",
                split="train",
                raw={"instruction": "切片重叠多少", "output": "重叠是 20 个字符"},
                cleaned={"instruction": "切片重叠多少", "output": "重叠是 20 个字符"},
                label={"intent": "knowledge_qa"},
                quality_score=1.0,
            )
            session.add(item)
            await session.commit()
            ds_id, item_id = ds.id, item.id

            try:
                result = await pipeline.stage_groundedness(session, ds_id)
                assert result["judged"] == 1
                assert result["verdicts"]["contradicted"] == 1

                refreshed = await session.get(DatasetItem, item_id)
                assert refreshed.status == "rejected"
                assert refreshed.reject_reason == "groundedness_contradicted"
                assert refreshed.label["groundedness"] == "contradicted"

                # 幂等：再次执行应跳过已判定样本
                again = await pipeline.stage_groundedness(session, ds_id)
                assert again["judged"] == 0 and again["skipped"] == 0  # 已被拒收，不在 passed 集合
            finally:
                await session.execute(
                    delete(DatasetItem).where(DatasetItem.dataset_id == ds_id)
                )
                await session.execute(delete(Dataset).where(Dataset.id == ds_id))
                await session.commit()


class TestEvaluation:
    def test_retrieval_suite(self, app_client, sample_doc):
        kb = _ok(app_client.post("/api/knowledge/bases", json={"name": "评测知识库"}))
        _ok(
            app_client.post(
                "/api/knowledge/ingest-text",
                json={"kb_id": kb["id"], "filename": "eval.md", "text": sample_doc},
            )
        )

        suite = _ok(
            app_client.post(
                "/api/eval/suites", json={"name": "检索回归", "type": "retrieval"}
            )
        )
        _ok(
            app_client.post(
                f"/api/eval/suites/{suite['id']}/cases",
                json={
                    "cases": [
                        {"input": "RRF 融合的作用是什么", "expected": "RRF|融合", "meta": {"kb_id": kb["id"]}},
                        {"input": "数据流水线包含哪些阶段", "expected": "清洗|质检", "meta": {"kb_id": kb["id"]}},
                    ]
                },
            )
        )

        run = _ok(app_client.post(f"/api/eval/suites/{suite['id']}/run", json={"top_k": 5}))
        assert run["status"] == "succeeded"
        assert run["metrics"]["hit_rate"] == 1.0
        assert run["metrics"]["mrr"] > 0

        report = _ok(app_client.get(f"/api/eval/runs/{run['run_id']}/report"))
        assert report["metrics"]["total"] == 2
        assert isinstance(report["failures"], list)

    def test_agent_suite_with_tool_expectation(self, app_client):
        suite = _ok(app_client.post("/api/eval/suites", json={"name": "Agent 回归", "type": "agent"}))
        _ok(
            app_client.post(
                f"/api/eval/suites/{suite['id']}/cases",
                json={
                    "cases": [
                        {
                            "input": "帮我算 (12+8)*3/4 等于多少",
                            "expected": "15",
                            "expect_tools": ["calculator"],
                        }
                    ]
                },
            )
        )
        run = _ok(app_client.post(f"/api/eval/suites/{suite['id']}/run", json={"top_k": 3}))
        assert run["status"] == "succeeded"
        assert run["metrics"]["total"] == 1
