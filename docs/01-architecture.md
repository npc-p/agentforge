# AgentForge 架构设计

## 1. 项目定位

AgentForge 是一个**智能体研发平台**，覆盖岗位职责中的五条主线：

| JD 职责 | 本项目的落地物 |
|---|---|
| Agent 核心架构、业务拆解、技术方案、原型迭代 | `app/agent/` 显式状态机（Plan → Act ⇄ Reflect → Answer），可观测、可复现 |
| 训练数据全流程（采集/清洗/标注/质检）+ 数据规范 + 质量指标 | `app/pipeline/` 五阶段流水线 + `docs/06-data-spec.md` + 质量指标落库 |
| 工具调用、第三方 API 集成、自主决策、任务拆分 | `app/tools/` 注册中心 + 执行器（超时/重试/幂等）+ 5 个内置工具 |
| Prompt 工程、RAG 知识库、场景化测试、问题排查 | `app/agent/prompts.py` 版本化模板 + `app/rag/` 混合检索 + `app/evaluation/` |
| 规范编写技术文档 | `docs/` 六份文档 + OpenAPI 自动生成 |

## 2. 技术选型与理由

| 组件 | 选型 | 理由 | 备选与代价 |
|---|---|---|---|
| Web 框架 | FastAPI | 异步原生、OpenAPI 自动生成、SSE 支持好 | Flask：无原生异步，SSE 需额外适配 |
| ORM / 数据库 | SQLAlchemy 2.0 + MySQL 8 | 对齐 JD 的 MySQL 要求；2.0 类型化 ORM 支持 `Mapped[]` | PostgreSQL：参考项目所用，但偏离 JD |
| Agent 编排 | **手写状态机** | 终止条件、重复调用抑制、轨迹落库必须由业务强制；框架黑盒会丢失可观测性 | LangGraph：开发快，但调试与配额控制不透明 |
| 模型接入 | DeepSeek（OpenAI 兼容）+ Mock 降级 | 兼容协议可平移到任意 OpenAI 兼容端点；Mock 保证无网可跑 | 直连各家 SDK：耦合厂商 |
| 向量库 | Chroma（内嵌） | 零运维、pip 即用；1e5 量级下与 Milvus 延迟差异 <10ms | Milvus：需 Docker 与 4-8GB 内存，本机 Docker 未启动 |
| 向量化 | 可插拔：哈希向量（默认）/ OpenAI 兼容 | 零依赖离线可用；切 `EMBED_PROVIDER=openai` 即可换 BGE-M3 等 | 直接依赖 torch：Python 3.14 无 wheel，且首启需下载模型 |
| 检索 | 向量 + BM25 + RRF | 向量管语义、BM25 管精确术语与编号；RRF 只用排名，规避量纲不可比 | 纯向量：漏精确匹配；纯 BM25：漏同义改写 |
| 前端 | Next.js App Router + Tailwind 4 | 与后端解耦，SSE 可手动解析，构建产物静态可托管 | 服务端模板：交互能力弱 |

### 向量库的决策依据（含反向条件）

选 Chroma 不是因为它最强，而是在当前约束下的 Pareto 最优点：

1. **量级不匹配**：本项目知识库规模在 1e4~1e5，Milvus 的分布式能力一项都用不上，却要付 2-3GB 镜像、数 GB 内存与启动等待的固定成本；
2. **职责分离**：MySQL 保存切片原文与元数据（唯一真相源），Chroma 仅是**可随时重建的索引**（`POST /api/knowledge/bases/{id}/rebuild`）。索引损坏不丢业务数据；
3. **失败模式温和**：Chroma 异常表现为检索质量下降或单接口报错；Milvus 挂掉会让整条 RAG 链路不可用；
4. **决策可逆**：`VectorStore` 协议 + 工厂，新增后端只需实现 5 个方法并注册，业务层零改动。

**出现以下情况应换 Milvus 或 Qdrant**：需要多副本水平扩展、检索 QPS > 数百、向量规模 > 1e6、需要原生稀疏向量混合检索、需要租户级硬隔离。当前有意**不提供**未实现的适配器，避免「配置了却不可用」的静默失败。

## 3. 分层结构

```
┌──────────────────────────────────────────────────────────┐
│ 前端控制台 (Next.js)  /  /chat  /knowledge  /dataset ...   │
└───────────────────────────┬──────────────────────────────┘
                            │ HTTP + SSE
┌───────────────────────────▼──────────────────────────────┐
│ 接口层 app/api/routes/  统一信封 {code,message,data,trace}│
│ 异常处理 → AppError 映射状态码；trace_id 全链路透传        │
├──────────────────────────────────────────────────────────┤
│ 服务层 app/services/  app/rag/service  app/pipeline/service│
├──────────────────────────────────────────────────────────┤
│ 内核层                                                    │
│  agent/ 状态机      tools/ 注册+执行      pipeline/ 五阶段 │
│  rag/ 检索          evaluation/ 评测                       │
├──────────────────────────────────────────────────────────┤
│ 基础层 llm/ 供应商抽象   db/ 会话   core/ 日志异常响应      │
└───────────────────────────┬──────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
    MySQL 8            Chroma (索引)      DeepSeek / Mock
```

## 4. Agent 状态机

### 4.1 状态与迁移

```
START ──► PLAN ──► ACT ──┬─ 有 tool_calls ──► 执行工具 ──► REFLECT ─┬─ 充分 ──► ANSWER ──► END
                          │                                         └─ 不足 ──► ACT (下一轮)
                          └─ 无 tool_calls ──► ANSWER ──► END
```

### 4.2 强制约束（写在代码里，不依赖模型自觉）

| 约束 | 实现位置 | 说明 |
|---|---|---|
| 最大轮次 | `settings.agent_max_steps`（默认 8） | 超限强制终止，防无限循环 |
| 重复调用抑制 | `tool_signature(tool, args)` + 历史观察签名比对 | 同工具同参数且已成功 → 直接拦截并告知模型 |
| 连续失败熔断 | `_all_failed()`：最近 3 次观察全失败即终止 | 避免在不可用工具上反复重试烧钱 |
| 规划结果校验 | `parse_plan()`：未知工具名改写为 `answer` | 模型虚构工具是常见失效模式，在此层拦截 |
| 反思结果兜底 | `_reflect()` 调用失败按「信息充分」处理 | 校验节点是增强项，不应成为单点故障 |
| 简单问答省调用 | 决策阶段即产出答案且无工具观察时直接复用 | 单轮问答从 2 次模型调用降为 1 次 |

### 4.3 事件契约

事件类型定义在 `app/agent/graph.py` 顶部常量，SSE 与评测共用同一条执行路径：

`run_started → plan → node → thought → tool_call → tool_result → node(reflect) → token* → citations → usage → run_finished`

### 4.4 可观测性

每个节点写入 `agent_steps`（node / thought / action / action_input / observation / status / latency_ms），
`agent_runs` 汇总 step_count、tool_call_count、tokens、duration_ms。
任意一次回答都能通过 `GET /api/runs/{id}` 还原「模型想了什么、调了什么、拿到了什么」。

## 5. 工具调用链路

```
@tool 装饰器注册
   │ 签名 → pydantic 模型 → JSON Schema（模型侧与执行侧同源，杜绝 schema 漂移）
   ▼
DECIDE 节点：把 registry.openai_schemas() 传给模型 → 模型返回 tool_calls
   ▼
ToolExecutor.execute()
   1) spec.validate()  —— 参数校验失败直接返回失败结果，不抛异常、不重试
   2) asyncio.wait_for —— 强制超时（默认 20s）
   3) 有限重试         —— 仅超时/连接/5xx，指数退避；参数错误不重试
   4) 结果归一化       —— ToolResult → observation 文本回灌模型
   5) 流水落库         —— tool_invocations 记录 args/result/status/retries/idempotency_key
```

**安全边界**（对应威胁建模）：
- `calculator`：AST 白名单求值，禁止名称访问与函数调用，指数上限 1000；
- `sql_query`：仅 SELECT/SHOW/DESCRIBE/EXPLAIN，禁多语句、DDL/DML、文件读写，结果限量 50 行；
- `http_request`：仅 http/https，DNS 解析后拒绝私网/回环/链路本地地址（SSRF 防护），响应截断 200KB；
- 生产环境应把 `sql_query` 绑定到独立只读账号，白名单只是第二道防线。

## 6. RAG 链路

```
文档 → 解析(parser: txt/md/pdf/docx/csv/json) 
     → 切分(splitter: 结构优先，超长按句递归，块间 80 字符重叠)
     → 向量化(embedder: 哈希 512 维 / OpenAI 兼容可切换)
     → 双写：MySQL(chunks 真相源) + Chroma(可重建索引)
     ↓ 查询
向量召回 Top3k ─┐
               ├─ RRF 融合(60 + rank，加权 0.6/0.4) → TopK → 正文取自 MySQL → 引用溯源
BM25 召回 Top3k ┘
```

**三个关键决策**：
1. 返回正文一律取自 MySQL 而非向量库副本，避免两处不一致时模型引用到过期内容；
2. RRF 只用排名不用分数，规避「余弦相似度」与「BM25 分」量纲不可比；
3. BM25 索引按「知识库集合 + 代数」缓存，写入后 `bump_generation()` 失效，避免每次查询全量重建。

## 7. 数据流水线

```
raw ──clean──► cleaned ──dedup──► cleaned ──label──► labeled ──qc──► passed / rejected
```

每个阶段幂等：只处理上一阶段完成的样本，重复调用不会重复加工。统计与证据（`qc_records`）逐条落库，
导出时按 `split` 过滤并冻结为 `dataset_versions` 快照。详见 `docs/06-data-spec.md`。

## 8. 数据模型（18 张表）

| 域 | 表 | 用途 |
|---|---|---|
| 对话 | `conversations` `messages` `agent_runs` `agent_steps` | 会话、消息、运行汇总、执行轨迹 |
| 工具 | `tools` `tool_invocations` | 工具元数据与启停、调用流水 |
| 知识 | `knowledge_bases` `documents` `chunks` | 知识库、文档状态机、切片（真相源） |
| 数据 | `datasets` `dataset_items` `qc_records` `dataset_versions` | 数据集、样本状态机、质检证据、版本快照 |
| Prompt | `prompt_templates` | 版本化模板与启用开关 |
| 评测 | `eval_suites` `eval_cases` `eval_runs` `eval_case_results` | 套件、用例、运行、单例结果 |

## 9. 可靠性设计

| 场景 | 处理 |
|---|---|
| 模型超时 / 429 / 5xx | 指数退避重试（0.5s/1s/2s），最多 `llm_max_retries` 次 |
| 模型连续失败 | 熔断器打开（默认 5 次），冷却 30s 内快速失败，半开探测恢复 |
| 未配置 API Key | 自动降级 MockProvider，全链路可运行并在 UI 显式标注 mock |
| 流式中断 | 降级为一次性返回，答案不丢失 |
| 工具调用失败 | 转为观察文本回灌模型，由模型决定换路或如实告知用户 |
| 数据库不可用 | 服务仍启动，`/api/system/health` 如实报告 `degraded` |
| 向量库损坏 | 从 MySQL 重建索引（rebuild 接口），业务数据不受影响 |

## 10. 已知限制

1. **哈希向量语义能力有限**：字符 n-gram 哈希对同义改写召回弱于神经嵌入，依赖 BM25 补足；生产应切 BGE-M3。
2. **Chroma 单进程写锁**：并发写入吞吐低，不适合多副本部署。
3. **SimHash 阈值为短文本标定**：长文档需下调阈值（见 `dedup.py` 文档），生产应按语料重新标定。
4. **反思节点为启发式**：仅判断「观察结果是否覆盖问题」，不做事实一致性校验；后者应由评测集的忠实度指标兜底。
5. **评测关键词匹配偏粗**：`expected` 用 `|` 分隔关键词做包含判断，无法评估语义等价；接入 LLM-as-judge 后可改善，但成本上升。
