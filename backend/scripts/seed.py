"""一键灌入演示数据：知识库、数据集、评测套件。

用途：让评审/接手者无需手工点击即可看到一个「有数据、可问答、可评测」的系统。
幂等设计：同名知识库/数据集/套件已存在则跳过创建，重复执行不会产生脏数据。

用法：
    ./.venv/Scripts/python.exe scripts/seed.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.db.session import AsyncSessionLocal, create_all, dispose, ping  # noqa: E402
from app.models.dataset import Dataset  # noqa: E402
from app.models.evaluation import EvalCase, EvalSuite  # noqa: E402
from app.models.knowledge import Document, KnowledgeBase  # noqa: E402
from app.pipeline import service as pipeline  # noqa: E402
from app.rag import service as rag_service  # noqa: E402
from app.services.tool_service import sync_registry  # noqa: E402

KB_NAME = "AgentForge 平台知识库"
DATASET_NAME = "Agent 指令样本 v1"
SUITE_RETRIEVAL = "检索质量回归"
SUITE_AGENT = "Agent 任务回归"

DOCS: list[tuple[str, str]] = [
    (
        "agent-architecture.md",
        """# Agent 状态机设计

AgentForge 的智能体采用显式状态机，依次经过任务规划、工具执行、结果校验与答案生成四个阶段。
规划节点把用户请求拆解为可执行任务，并判断是否需要调用外部工具；执行节点根据工具返回的观察结果
决定继续调用工具还是作答；校验节点判断信息是否充分，避免在证据不足时编造答案。

## 终止条件

状态机由三类硬约束保证收敛：最大执行轮次默认为 8 轮；同一工具与同一参数成功执行后不再重复调用；
最近三次工具调用全部失败时提前终止，防止在不可用工具上反复消耗。

## 可观测性

每个节点的思考内容、动作、入参与观察结果都会写入执行轨迹表，
因此任意一次回答都可以通过运行记录精确还原，便于定位是规划错误还是工具返回异常。
""",
    ),
    (
        "rag-design.md",
        """# 检索增强设计

知识库采用混合检索方案。向量召回负责语义近似表达，BM25 召回负责精确术语、编号与专有名词，
两路结果通过 RRF 融合。RRF 只使用排名而不使用分数，从而规避余弦相似度与 BM25 分量纲不可比的问题。

## 切分策略

切分遵循结构优先原则：先按标题与空行切分为语义块，超长段落再按句子递归切分，
相邻块之间保留 80 个字符的重叠，避免答案被切断在边界上。切片正文以 MySQL 为唯一真相源，
向量库只保存可重建的索引。

## 检索参数

默认返回前 6 个片段，向量权重 0.6，BM25 权重 0.4。权重调整需要在评测集上验证，
不能凭直觉修改。
""",
    ),
    (
        "data-pipeline.md",
        """# 数据流水线规范

训练数据依次经过采集、清洗、标注与质检四个阶段，每个阶段只处理上一阶段完成的样本，
因此整条流水线可以重复执行而不会重复加工。

## 清洗

清洗包含 Unicode 归一、控制字符移除、HTML 标签剥离、空白压缩、手机号与身份证脱敏。
所有变更记录在样本的清洗变更列表中，可逐条回溯。

## 去重

去重使用 SimHash 做近重复检测，汉明距离小于等于阈值即判定为重复。阈值需要按语料长度标定，
中文短文本实测改一字距离约为 10 到 12，主题无关的句子约为 24 到 30。

## 质检

质检规则分为 error 与 warning 两级。error 命中直接拒收，warning 只扣减质量分，
避免一条弱规则把整批好样本拦掉。质量分等于通过规则的权重和除以总权重。
""",
    ),
]

SAMPLES: list[dict[str, str]] = [
    {"instruction": "AgentForge 的智能体包含哪几个阶段？", "output": "任务规划、工具执行、结果校验与答案生成四个阶段。"},
    {"instruction": "状态机如何保证收敛？", "output": "通过最大轮次、重复调用抑制与连续失败熔断三类硬约束保证收敛。"},
    {"instruction": "混合检索为什么使用 RRF 融合？", "output": "RRF 只依赖排名不依赖分数，可规避余弦相似度与 BM25 分量纲不可比的问题。"},
    {"instruction": "切片的重叠长度默认是多少？", "output": "相邻切片默认保留 80 个字符的重叠。"},
    {"instruction": "为什么向量库中的正文不作为最终结果？", "output": "切片正文以 MySQL 为唯一真相源，向量库只保存可重建的索引，避免两处不一致。"},
    {"instruction": "质检规则为什么区分 error 与 warning？", "output": "避免一条弱规则把整批好样本全部拦掉，warning 只扣分不否决。"},
    {"instruction": "SimHash 的阈值如何确定？", "output": "按语料实测标定，中文短文本改一字约 10 到 12，主题无关约 24 到 30，默认取 14。"},
    {"instruction": "帮我算 (12+8)*3/4 等于多少", "output": "15"},
    {"instruction": "现在几点？", "output": "需要调用当前时间工具获取，不能凭空回答。"},
    {"instruction": "知识库里说混合检索的权重是多少？", "output": "向量权重 0.6，BM25 权重 0.4。"},
    {"instruction": "占位样本", "output": "TODO"},
    {"instruction": "答案过短的样本", "output": "好"},
]

RETRIEVAL_CASES = [
    {"input": "RRF 融合解决了什么问题", "expected": "RRF|融合|排名"},
    {"input": "切片重叠长度是多少", "expected": "80|重叠"},
    {"input": "质检规则分几级", "expected": "error|warning|两级"},
]

AGENT_CASES = [
    {"input": "帮我算 (12+8)*3/4 等于多少", "expected": "15", "expect_tools": ["calculator"]},
    {"input": "知识库里的切片重叠长度是多少", "expected": "80", "expect_tools": ["knowledge_search"]},
]


async def _seed_knowledge(session) -> tuple[int, int]:
    kb = (
        await session.execute(select(KnowledgeBase).where(KnowledgeBase.name == KB_NAME))
    ).scalar_one_or_none()
    if kb is None:
        kb = await rag_service.create_kb(session, name=KB_NAME, description="平台自身设计与规范文档")
        print(f"  + 知识库已创建: id={kb.id}")

    existing = {
        row.filename
        for row in (
            await session.execute(select(Document).where(Document.kb_id == kb.id))
        ).scalars().all()
    }
    added = 0
    for filename, text in DOCS:
        if filename in existing:
            continue
        doc = await rag_service.ingest_text(session, kb.id, filename=filename, text=text, ext="md")
        added += 1
        print(f"  + 文档入库: {filename} -> {doc.chunk_count} 切片")
    return kb.id, added


async def _seed_dataset(session, reset: bool = False) -> tuple[int, int]:
    dataset = (
        await session.execute(select(Dataset).where(Dataset.name == DATASET_NAME))
    ).scalar_one_or_none()
    if dataset is None:
        dataset = Dataset(
            name=DATASET_NAME, task_type="sft", description="Agent 指令微调示例样本（含脏数据用于演示流水线）"
        )
        session.add(dataset)
        await session.commit()
        print(f"  + 数据集已创建: id={dataset.id}")

    if reset and dataset.item_count:
        from sqlalchemy import delete as sa_delete

        from app.models.dataset import DatasetItem, DatasetVersion, QCRecord

        await session.execute(sa_delete(QCRecord).where(QCRecord.dataset_id == dataset.id))
        await session.execute(sa_delete(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id))
        await session.execute(sa_delete(DatasetItem).where(DatasetItem.dataset_id == dataset.id))
        dataset.item_count = 0
        dataset.passed_count = 0
        dataset.status = "draft"
        await session.commit()
        print("  - 已清空原样本与版本（--reset）")

    if dataset.item_count:
        print(f"  = 数据集已有 {dataset.item_count} 条样本，跳过导入")
        return dataset.id, 0

    result = await pipeline.add_items(session, dataset.id, SAMPLES, source="seed", split="train")
    print(f"  + 导入样本 {result['added']} 条")
    stages = await pipeline.run_full_pipeline(session, dataset.id)
    print(
        "  + 流水线: 清洗丢弃 {d} / 去重 {u} / 质检通过 {p}".format(
            d=stages["clean"]["dropped"],
            u=stages["dedup"]["duplicates"],
            p=stages["qc"].get("passed", 0),
        )
    )
    return dataset.id, result["added"]


async def _seed_suites(session, kb_id: int) -> int:
    created = 0
    for name, suite_type, cases in (
        (SUITE_RETRIEVAL, "retrieval", RETRIEVAL_CASES),
        (SUITE_AGENT, "agent", AGENT_CASES),
    ):
        suite = (
            await session.execute(select(EvalSuite).where(EvalSuite.name == name))
        ).scalar_one_or_none()
        if suite is not None:
            print(f"  = 套件已存在: {name}")
            continue
        suite = EvalSuite(name=name, type=suite_type, description=f"{suite_type} 评测")
        session.add(suite)
        await session.flush()
        for case in cases:
            meta = {"kb_id": kb_id} if suite_type == "retrieval" else None
            session.add(
                EvalCase(
                    suite_id=suite.id,
                    input=case["input"],
                    expected=case.get("expected"),
                    expect_tools=case.get("expect_tools"),
                    meta=meta,
                )
            )
        suite.case_count = len(cases)
        await session.commit()
        created += 1
        print(f"  + 套件已创建: {name} ({len(cases)} 用例)")
    return created


async def main() -> None:
    reset = "--reset" in sys.argv
    setup_logging("WARNING")
    settings.ensure_dirs()
    if not await ping():
        print("数据库不可用，请检查 .env 中的 DATABASE_URL")
        raise SystemExit(1)

    await create_all()
    async with AsyncSessionLocal() as session:
        await sync_registry(session)
        print("[1/3] 知识库")
        kb_id, docs = await _seed_knowledge(session)
        print("[2/3] 数据集")
        ds_id, items = await _seed_dataset(session, reset=reset)
        print("[3/3] 评测套件")
        suites = await _seed_suites(session, kb_id)

    await dispose()
    print(
        f"\n完成：知识库 id={kb_id}（新增文档 {docs}）｜数据集 id={ds_id}（新增样本 {items}）｜"
        f"新增套件 {suites}。\n启动服务后访问 http://127.0.0.1:8010/docs 或前端控制台。"
    )


if __name__ == "__main__":
    asyncio.run(main())
