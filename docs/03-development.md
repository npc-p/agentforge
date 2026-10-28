# 开发流程与工程约定

## 1. 环境要求

| 组件 | 版本 | 校验命令 |
|---|---|---|
| Python | **3.12.x**（必须，3.14 缺 torch/chromadb 部分依赖 wheel） | `py -0` |
| uv | ≥ 0.5 | `uv --version` |
| MySQL | 8.0+ | `mysql --version` |
| Node.js | ≥ 20（前端） | `node --version` |

## 2. 首次启动（5 步）

```bash
# 1) 建库
mysql -uroot -p -e "CREATE DATABASE agentforge DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 2) 后端依赖（必须指定 3.12）
cd agentforge/backend
uv venv --python 3.12
uv pip install -p .venv/Scripts/python.exe -e .
uv pip install -p .venv/Scripts/python.exe pytest pytest-asyncio anyio

# 3) 配置
cp .env.example .env      # 填 DATABASE_URL 与 DEEPSEEK_API_KEY（不填则自动降级 Mock）

# 3.5) 下载本地嵌入模型 BGE-M3（约 2.2 GB）
#      MODEL 默认走本地，不下载则启动会在首次检索时报 embed_model_missing
mkdir -p data/models/bge-m3 && cd data/models/bge-m3
mkdir -p 1_Pooling
for f in pytorch_model.bin config.json tokenizer.json tokenizer_config.json \
         special_tokens_map.json sentencepiece.bpe.model modules.json \
         sentence_bert_config.json config_sentence_transformers.json; do
  curl -sL -O "https://modelscope.cn/models/BAAI/bge-m3/resolve/master/$f"
done
curl -sL -o 1_Pooling/config.json \
  "https://modelscope.cn/models/BAAI/bge-m3/resolve/master/1_Pooling/config.json"
cd ../../..

# 4) 建表 + 灌演示数据
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8010   # 启动时自动建表
# 或直接：.venv/Scripts/python.exe scripts/seed.py

# 5) 启动服务
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
```

前端：

```bash
cd agentforge/frontend
npm install
npm run dev     # http://localhost:3000
```

访问 `http://127.0.0.1:8010/docs` 可直接调接口。

## 3. 数据库迁移（Alembic）

开发环境启动时 `create_all()` 会自动建表，但**生产必须走迁移**——schema 变更要有版本、可回滚。

```bash
cd agentforge/backend

# 生成新迁移（对比 ORM 模型与当前库结构）
.venv/Scripts/python.exe -m alembic revision --autogenerate -m "add_xxx_field"

# 应用
.venv/Scripts/python.exe -m alembic upgrade head

# 回退一版
.venv/Scripts/python.exe -m alembic downgrade -1
```

连接串由 `alembic/env.py` 从 `app.config.settings` 读取，**不要在 `alembic.ini` 里重复配置**。
已提供初始迁移 `alembic/versions/ea8515dfee3e_initial_schema.py`，在空库上 `upgrade head` 可建出全部 18 张表（已验证）。

**接管已有 `create_all` 库（不丢数据）**：开发库若由 `create_all()` 建出、且没有
`alembic_version` 表，直接 `upgrade` 会从空库重建、与现有表冲突。正确顺序是先
`stamp` 标记现状，再走后续迁移：

```bash
# 1. 备份（务必先做）
mysqldump -h127.0.0.1 -uroot -p agentforge --single-transaction > data/backup_db.sql

# 2. 把当前库标记为「已是初始迁移后的状态」（只写版本号，不动 DDL/数据）
.venv/Scripts/python.exe -m alembic stamp head

# 3. 此后即可正常 revision --autogenerate / upgrade / downgrade
```

变更演练（2026-10-28 已实测）：给 `conversations` 加 `remark` 列 →
`upgrade head`（列出现、数据保留）→ `downgrade -1`（列消失、数据保留），
`alembic_version` 版本号同步正确回退。演练迁移与样本数据已清理，库回到 `head`。

### 3.1 Chroma 的跨进程可见性（必须遵守）

Chroma 以内嵌 SQLite 运行，**一个进程写入的向量，另一个进程不一定能立刻看到**。
2026-10-28 实测：脚本重切文档并写入向量后，服务进程仍按旧 segment 检索，返回 0 命中；
重启服务才恢复。同一次重切中 BM25 却能立即感知变化（它从 MySQL 重建），反差很明显。

由此两条约定：

1. **向量的最终写入必须发生在服务进程内**。脚本改完 MySQL 后，要通过
   `POST /api/knowledge/bases/{id}/rebuild` 让服务重建向量——`scripts/resplit.py`
   已内置这一步，直接跑脚本即可。
2. **不要指望「多 worker 共享同一 Chroma 目录」来扩吞吐**。写入路径会冲突，
   这也是容量分析里把并发上限归因于 HTTP 层、而非检索层的原因之一。

顺带说明 BM25 缓存为什么不用进程内计数器：早前用 `_generation[kb_id]` 自增，
只在**本进程**写入时失效，脚本或其他 worker 的写入完全感知不到，表现为「数据已更新
但检索仍返回旧结果」。现在改成基于库内真实状态的指纹（切片数 + 最大 id），
任何进程的写入都能被下一个请求看到。局限：原地 UPDATE 切片内容不改变这两个值，
因此不会触发失效——正常写入路径是「删 + 建」，不受影响。

## 3.2 Docker 部署（一条命令）

```bash
cp .env.example .env      # 只需填 DEEPSEEK_API_KEY，留空则以 Mock 模式运行
docker compose up -d
```

### 三个踩过的坑（都已在配置里修掉，改配置时别改回去）

**1. torch 必须用 `--no-deps` 装**

`pip install torch --index-url https://download.pytorch.org/whl/cpu` 会让**依赖也走**
这个 index：torch 本体有 3.9 MB/s，但 `sympy` 12 kB/s、`networkx` 18 kB/s，
实测把构建从 3 分钟拖到 16 分钟还没完成。改成：

```dockerfile
pip install --no-deps --index-url https://download.pytorch.org/whl/cpu torch
pip install sympy networkx jinja2 setuptools   # 依赖走 PyPI
```

**2. healthcheck 的 `start_period` 必须够长**

首次启动要下 2.2 GB 模型。`start_period: 90s` 会让容器在下载期间被判 unhealthy，
而 frontend 是 `depends_on: backend: condition: service_healthy`，于是**前端永远起不来**。
现设为 300s。

**3. `BACKEND_ORIGIN` 是构建时变量，不是运行时**

`next.config` 的 rewrites 把目标地址写进了构建产物，运行时改环境变量不生效。
前端镜像的 `ARG BACKEND_ORIGIN` 必须与 compose 的 `build.args` 同值。

### 自动化的三件事

`backend/docker-entrypoint.sh` 在 uvicorn 启动前依次做：

1. 检查 `/app/data/models/bge-m3/config.json`，缺失则调 `scripts/fetch_model.py` 下载；
   **下载失败自动切 `EMBED_PROVIDER=hashing`**——宁可检索质量降级，也不能让容器起不来；
2. 跑 `scripts/seed.py` 灌演示数据（脚本本身幂等，重复启动不会重复写）；
3. `exec` 到 CMD 启动服务。

### 目录与数据卷

| 卷 | 内容 | 说明 |
|---|---|---|
| `model_data` | 向量模型 2.2 GB | 只下这一次，`docker compose down -v` 才会清 |
| `chroma_data` | 向量库 | 与 MySQL 的 chunks 对应，重建用 `POST /knowledge/bases/{id}/rebuild` |
| `mysql_data` | 全部业务数据 | |
| `upload_data` | 上传的原始文件 | |

## 4. 目录职责

```
backend/app/
├── agent/        状态机（graph）、规划器（planner）、Prompt（prompts）
├── api/routes/   HTTP 入口，只做参数校验与编排，不写业务逻辑
├── core/         日志、异常、响应信封、文本工具
├── db/           引擎、会话、ORM 基类
├── evaluation/   指标（metrics）与执行器（runner）
├── llm/          供应商抽象（base）、DeepSeek、Mock、网关（router）
├── models/       18 张表的 ORM 定义
├── pipeline/     数据流水线五阶段
├── rag/          解析、切分、向量化、向量库、BM25、检索、服务
├── schemas/      请求/响应 DTO
├── services/     跨模块业务（会话、工具元数据）
└── tools/        注册中心、执行器、内置工具
```

**分层纪律**：
- `api/routes` 不直接 `session.commit()` 之外的业务逻辑，复杂流程下沉到 `services` 或领域包；
- `models` 只描述表结构，不含业务方法；
- 跨层传递用 DTO（schemas）或 dict，不把 ORM 对象漏给工具层与 Agent 层。

## 4. 常见改造的 SOP

### 4.1 新增一个工具（3 分钟）

```python
# app/tools/builtin/weather.py
from typing import Annotated, Any
from pydantic import Field
from app.tools.registry import tool

@tool(name="weather", display_name="天气查询", description="查询城市天气",
      category="network", timeout_s=10.0)
async def weather(city: Annotated[str, Field(description="城市名，如 北京")]) -> dict[str, Any]:
    return {"city": city, "temp": 26}
```

再在 `app/tools/builtin/__init__.py` 加一行 import 即完成注册；重启后 `sync_registry` 自动写入 `tools` 表。

> **必须遵守**：参数注解用 `Annotated[T, Field(description=...)]` + 普通默认值。
> 不要把 `Field(...)` 写在默认值位置——参数被省略时函数体会拿到 `FieldInfo` 哨兵，
> 这是一个已经踩过并修复的坑（见 §6）。

### 4.2 新增模型供应商

继承 `app/llm/base.py:LLMProvider` 实现 `chat` / `stream` / `ready`，在 `build_provider()` 注册分支即可。
业务层只依赖 `LLMGateway`，无需改动。

### 4.3 调整 Agent 行为

- 改推理策略 → 编辑 `app/agent/prompts.py` 的节点模板（或通过 `/api/prompts` 写库覆盖，无需发版）；
- 改收敛条件 → `settings.agent_max_steps`、`graph.py:_all_failed`；
- 新增节点 → 在 `_execute` 中插入状态分支，并同步前端 `node` 标签映射。

### 4.4 新增检索后端

实现 `app/rag/vector_store.py:VectorStore` 的 5 个方法，在 `get_vector_store()` 增加分支。
**注意**：当前只允许配置已实现的后端（`Literal["chroma"]`），新增后需同步修改该 Literal 与 `.env.example`。

## 5. 调试手册

| 现象 | 排查路径 |
|---|---|
| 回答质量差 | 1) `/api/runs/{id}` 看 plan 是否误判 → 2) 看 tool_result 是否为空 → 3) `/api/knowledge/search` 单独验证召回 |
| 工具没被调用 | 看 `plan.need_tools`；模型可能「心算」了，检查 decide 模板中的强制工具清单 |
| 检索无结果 | 确认 `chunk_count > 0`；`POST /knowledge/bases/{id}/rebuild` 重建索引；检查 `bump_generation` 是否失效了缓存 |
| 数据全部被判重复 | 检查去重是否把自身纳入参照集（历史缺陷，见 §6） |
| 启动即 500 | 检查 `.env` 的 `DATABASE_URL`；`/api/system/health` 会指出具体哪项依赖不可用 |
| 模型一直降级 | `llm.mode == mock` 说明未读到 Key，检查 `.env` 路径与变量名 |

**日志**：默认输出单行 JSON，字段固定为 `ts / level / logger / trace / msg`，
自定义字段（如 `extra={"tool": "calculator"}`）会追加在后面。`trace_id` 在 HTTP 响应头
与所有日志行中透传，一次请求一条链路：

```json
{"ts":"2026-10-28T10:26:38.242+08:00","level":"INFO","logger":"app.rag.service","trace":"b730ded0","msg":"文档入库完成 doc=1 chunks=5"}
```

排查时可直接按字段筛选，不必再 Ctrl+F：

```bash
# 只看某个 trace 的全链路
python -m uvicorn app.main:app 2>&1 | grep '"trace": "b730ded0"'
# 只看 WARNING 及以上
python -m uvicorn app.main:app 2>&1 | grep -E '"level": "(WARNING|ERROR)"'
```

本地想看得更顺眼可切回文本格式：`LOG_FORMAT=text`（`.env`）。

**脱敏**：`MaskingFilter` 在写出前扫描 msg 与 args，手机号、身份证、邮箱、
银行卡、`sk-` 密钥、`password=xxx` 一律打码（手机号保留前 3 后 4，便于定位
是哪条记录）。这一步在**日志基础设施层**完成，不依赖调用点自觉——新增代码
即使直接把用户 query 写进日志，也不会把隐私落到磁盘。用 `LOG_MASK=false` 可关，
但不建议。

## 6. 已踩过的坑（必须遵守的约定）

| # | 问题 | 根因 | 约定 |
|---|---|---|---|
| 1 | 全部接口 500 | `async_sessionmaker()` 返回对象本身，多写了一个 `await` | `AsyncSessionLocal()` 不加 await |
| 2 | 向量集合名出现 `af_kb_Annotation(...)` | 工具参数默认值写成 `Field(...)`，省略参数时 `FieldInfo` 被当值传入 | 注解用 `Annotated`，默认值写真实值 |
| 3 | 工具 Schema 构建报 `class-not-fully-defined` | 模块使用 `from __future__ import annotations`，注解是字符串 | 用 `get_type_hints(include_extras=True)` 求值 |
| 4 | `/api/tools` 返回空、注册表为 0 | `app.tools` 包未导入 builtin 子包 | 在 `app/tools/__init__.py` 导入 builtin |
| 5 | 二次执行去重后样本全被判重复 | 参照集包含本批次自身指纹 | 历史指纹只取 `status != cleaned` 的样本 |
| 6 | 真实模型下标注阶段 KeyError；计划模板变量不生效 | Prompt 内含 JSON 示例花括号，被 `str.format` 当作占位符（异常还被 try/except 吞掉） | 一律用 `prompts.render()` / `render_label_prompt()` 做显式占位符替换 |
| 7 | 启动报 cors_origins 解析失败 | pydantic-settings 对复杂类型字段先做 JSON 解码 | 复杂类型用字符串承载 + 属性解析 |
| 8 | 数据集 passed_count 恒为 0 | 会话 `autoflush=False`，统计前未 flush，读到旧值 | 任何「先改状态再统计」的查询前显式 `await session.flush()` |
| 9 | 编辑 Prompt 模板必然 500（`MissingGreenlet`） | `onupdate=func.now()` 的字段在 UPDATE 后被标记过期，异步会话里同步读属性会尝试惰性加载 | 提交后 `await session.refresh(row)` 再序列化；异步代码中禁止依赖隐式惰性加载 |
| 10 | 异步测试报 `attached to a different loop` | 连接池的连接绑定在首个事件循环，用例换循环后复用即失败 | `ENV=test` 时使用 `NullPool`，每次取连接都新建 |
| 9 | 近重复检测形同虚设 | 阈值 3 与实测距离分布（改一字 10~12）不匹配；分带索引在阈值 14 下不保证召回 | 阈值标定为 14，改精确线性比对 |

## 7. 提交与质量门禁

- 提交前必须：`pytest tests -q` 全绿（当前 100 条）；
- 涉及接口变更同步更新 `docs/02-api.md`；
- 涉及数据字段/规则变更同步更新 `docs/06-data-spec.md`；
- 新增依赖需在 `pyproject.toml` 声明并在本文档 §1 更新版本要求；
- 迁移脚本（Alembic）与模型变更同批提交，禁止只改 ORM 不改迁移。

## 8. 测试与压测

```bash
pytest tests -q                     # 全量（单元 + 接口）
pytest tests -q --cov=app --cov-report=term          # 带覆盖率（当前 75%）
pytest tests -q --cov=app --cov-report=html          # 生成 htmlcov/index.html 逐行查看
pytest tests/test_units.py -q       # 纯函数，无需数据库
pytest tests/test_api.py -q         # E2E，使用 agentforge_test 库 + Mock 模型
pytest tests -q -k "dedup"          # 定向回归

# 并发压测（零外部依赖，Windows 上不必装 hey/wrk）
.venv/Scripts/python.exe scripts/bench.py -c 20 -n 400
.venv/Scripts/python.exe scripts/bench.py -c 50 -n 600 --json   # 机器可读
```

> Git Bash 会把 `/health` 这类参数误转成 Windows 路径，测非 `/api` 路径时加
> `MSYS_NO_PATHCONV=1` 前缀；脚本本身也做了单前导斜杠归一化作为兜底。

测试库 `agentforge_test` 与 Chroma 临时目录每次运行前重建，不污染开发数据。
