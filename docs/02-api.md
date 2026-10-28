# 接口说明（API Reference）

> 完整机器可读定义见运行中的 `http://127.0.0.1:8010/openapi.json`，交互式调试见 `/docs`。
> 本文只记录**契约层面的约定**与调用要点，不重复枚举全部字段。

## 1. 通用约定

### 1.1 响应信封

成功：

```json
{ "code": 0, "message": "ok", "data": { }, "trace_id": "40687588efc04ba6" }
```

失败（HTTP 状态码同时生效）：

```json
{ "code": "conflict", "message": "文档内容已存在", "detail": {"document_id": 3}, "trace_id": "..." }
```

- `data` 是唯一有效载荷，前端不要读取顶层其他字段；
- `trace_id` 同时写入响应头 `X-Trace-Id`，可与服务端日志对齐；
- SSE 接口不套信封，直接以事件流返回。

### 1.2 错误码

| code | HTTP | 场景 |
|---|---|---|
| `validation_error` | 422 | 参数校验失败（含工具入参） |
| `not_found` | 404 | 资源不存在 |
| `conflict` | 409 | 名称重复、文档内容重复 |
| `llm_error` | 502 | 模型调用重试后仍失败 |
| `llm_unavailable` | 503 | 熔断中 |
| `tool_error` | 502 | 工具执行失败 |
| `vector_store_error` | 503 | 向量库不可用 |
| `internal_error` | 500 | 未捕获异常（细节只落日志，不回传） |

### 1.3 分页

列表接口统一 `?page=1&size=20`（size 上限 100/200，视接口而定），返回 `{items, total, page, size}`。

## 2. 系统 `/api/system`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/system/health` | 健康检查：数据库、向量库、向量化、模型四类依赖状态；任一异常返回 `status=degraded` |
| GET | `/system/config` | 非敏感运行配置（模型、RAG 参数、切片参数）。**密钥类字段永不返回** |
| GET | `/system/overview` | 平台总览计数（会话/消息/运行/知识库/切片/数据集/工具/评测） |

## 3. 对话与运行 `/api`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/chat` | 同步对话，内部走完整状态机，聚合后返回 |
| POST | `/chat/stream` | **SSE 流式对话**（事件见 §3.1） |
| GET | `/conversations` | 会话列表（含消息数） |
| POST | `/conversations` | 新建会话 |
| PATCH | `/conversations/{id}` | 改标题/状态 |
| DELETE | `/conversations/{id}` | 级联删除消息、运行与轨迹 |
| GET | `/conversations/{id}/messages` | 消息列表（含引用） |
| GET | `/runs` | 运行记录分页 |
| GET | `/runs/{id}` | 运行详情 + 完整执行轨迹（含每个节点的 thought/observation） |

### 3.1 SSE 事件契约

`POST /api/chat/stream`，`Content-Type: text/event-stream`，`data` 为 JSON 字符串。

| event | data 关键字段 | 出现时机 |
|---|---|---|
| `conversation` | `conversation_id` | 首个事件，用于前端绑定会话 |
| `run_started` | `run_id`, `trace_id`, `mock_mode`, `max_steps` | 状态机启动 |
| `plan` | `intent`, `need_tools`, `tasks[]`, `reasoning`, `latency_ms` | 规划完成 |
| `node` | `node`, `label` | 进入 plan/act/reflect/answer 节点 |
| `thought` | `content` | 模型的推理片段或校验结论 |
| `tool_call` | `name`, `arguments`, `call_id` | 决定调用工具 |
| `tool_result` | `name`, `ok`, `latency_ms`, `retries`, `error`, `preview` | 工具返回 |
| `token` | `delta` | 答案流式增量 |
| `citations` | `citations[]`（index/chunk_id/doc_name/score/snippet） | 有检索命中时 |
| `usage` | `prompt_tokens`, `completion_tokens`, `steps`, `tool_calls`, `duration_ms`, `mock_mode` | 收尾统计 |
| `run_finished` | `run_id`, `status`, `error` | 流结束 |
| `error` | `code`, `message` | 运行或流式异常 |

> 前端须用 `fetch` + `ReadableStream` 解析（浏览器 `EventSource` 不支持 POST）。

## 4. 知识库 `/api/knowledge`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/knowledge/bases` | 知识库列表 |
| POST | `/knowledge/bases` | 新建（名称唯一，可指定 chunk_size / overlap） |
| PATCH | `/knowledge/bases/{id}` | 修改描述与切片参数（不影响已入库切片） |
| DELETE | `/knowledge/bases/{id}` | 删除知识库并 drop 对应向量集合 |
| POST | `/knowledge/bases/{id}/upload` | **上传文件**（multipart，字段名 `file`），支持 txt/md/pdf/docx/csv/json，默认上限 20MB |
| POST | `/knowledge/ingest-text` | 直接写入文本（便于脚本灌库） |
| GET | `/knowledge/bases/{id}/documents` | 文档列表（含状态与失败原因） |
| DELETE | `/knowledge/documents/{id}` | 删除文档及其切片与向量 |
| GET | `/knowledge/documents/{id}/chunks` | 查看切片（调试切分质量） |
| POST | `/knowledge/search` | **混合检索调试**：返回 score / vector_rank / bm25_rank，可直接对比两路召回 |
| POST | `/knowledge/bases/{id}/rebuild` | 从 MySQL 重建向量索引 |
| GET | `/knowledge/stats` | 全局计数 |

**调用顺序约束**：`upload` / `ingest-text` 会阻塞至向量写入完成，返回的 `chunk_count` 可作为后续检索的可用性判据。

## 5. 数据集 `/api/datasets`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/datasets` | 数据集列表 |
| POST | `/datasets` | 新建（名称唯一） |
| POST | `/datasets/{id}/items` | 批量导入样本（单次 ≤500 条），自动归一字段命名 |
| GET | `/datasets/{id}/items` | 样本分页，可按 `status` 过滤 |
| POST | `/datasets/{id}/pipeline` | **执行流水线**。`stages: null` 表示 clean+dedup+label+qc；`groundedness` 需显式指定（会逐条调用模型），可选 `groundedness_limit` / `groundedness_threshold` |
| GET | `/datasets/{id}/stats` | 状态分布、通过率、平均质量、意图分布 |
| GET | `/datasets/{id}/qc` | 质检记录（逐条规则证据） |
| POST | `/datasets/{id}/export` | 导出（chat_jsonl / sharegpt / alpaca），生成版本快照 |
| GET | `/datasets/{id}/versions` | 版本历史（含冻结的质量指标） |

## 6. 工具 `/api/tools`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/tools` | 工具列表（含 Schema、超时、启停、调用统计） |
| GET | `/tools/schemas` | OpenAI function calling 形态，便于第三方直接接入 |
| PATCH | `/tools/{name}/enabled` | 启停（以数据库为准，重启不回滚） |
| POST | `/tools/{name}/invoke` | **在线调试**：传 `args`，返回 data/error/latency/retries/observation |
| GET | `/tools/invocations` | 调用流水分页（可按 tool_name 过滤） |

## 7. Prompt `/api/prompts`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/prompts` | 列模板（可按 scene 过滤） |
| POST | `/prompts` | 新增版本：同名自动 `version+1`，并可将旧版本置为不启用 |
| GET | `/prompts/{id}` | 详情 |
| PATCH | `/prompts/{id}` | 修改内容/描述/启用状态 |
| POST | `/prompts/{id}/activate` | 切换启用版本（同名的其他版本自动停用） |

## 8. 评测 `/api/eval`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET / POST | `/eval/suites` | 套件列表 / 新建（`type=retrieval` 或 `agent`；`config.judge=true` 启用 LLM-as-judge，默认关闭） |
| POST | `/eval/suites/{id}/cases` | 批量加用例（`expected` 用 `\|` 分隔多个关键词，`expect_tools` 断言工具调用） |
| GET | `/eval/suites/{id}/cases` | 用例列表 |
| POST | `/eval/suites/{id}/run` | 执行套件，返回聚合指标 |
| GET | `/eval/runs` | 历史运行 |
| GET | `/eval/runs/{id}/report` | 报告：指标 + 失败用例明细 |

**指标口径**：
- `hit_rate`：TopK 内至少命中一条（宽松，仅说明「召回到」）；
- `hit_rate_top1`：首位即命中，衡量排序质量的主指标；
- `mrr`：首个命中位置的倒数均值；
- `pass_rate`：任务通过率；
- `latency_p95_ms`：单例延迟 p95。

**`expected` 字段语法**（评测用例）：

| 写法 | 含义 |
|---|---|
| `15\|80` | 必须**全部包含**这些关键词 |
| `15\|!16` | 必须包含 `15`，且**不得包含** `16`（`!` 前缀为负例断言） |
| `!不是` | 仅负例：答案中不得出现否定表述 |

负例断言用于拦截「答反」——例如期望 `15` 而模型回答「不是 15，是 16」，无负例时会被误判为通过。

## 9. 典型调用序列

### 9.1 从零到可问答（5 步）

```bash
# 1. 建知识库
curl -X POST http://127.0.0.1:8010/api/knowledge/bases \
  -H 'Content-Type: application/json' \
  -d '{"name":"产品文档","description":"demo","chunk_size":600,"chunk_overlap":80}'

# 2. 上传文档
curl -X POST http://127.0.0.1:8010/api/knowledge/bases/1/upload -F "file=@./docs/01-architecture.md"

# 3. 验证检索
curl -X POST http://127.0.0.1:8010/api/knowledge/search \
  -H 'Content-Type: application/json' -d '{"query":"混合检索如何融合","kb_id":1,"top_k":3}'

# 4. 提问（SSE）
curl -N -X POST http://127.0.0.1:8010/api/chat/stream \
  -H 'Content-Type: application/json' -d '{"query":"混合检索用什么融合算法？","kb_id":1}'

# 5. 查看执行轨迹
curl http://127.0.0.1:8010/api/runs/1
```

### 9.2 数据闭环

```bash
# 建数据集 → 导样本 → 跑全流水线 → 看质检证据 → 导出并生成版本
curl -X POST .../api/datasets -d '{"name":"sft-v1","task_type":"sft"}'
curl -X POST .../api/datasets/1/items -d '{"items":[{"instruction":"...","output":"..."}],"split":"train"}'
curl -X POST .../api/datasets/1/pipeline -d '{"stages":null}'
curl ".../api/datasets/1/qc?limit=100"
curl -X POST .../api/datasets/1/export -d '{"format":"chat_jsonl"}'
```
