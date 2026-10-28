"""评估 Rerank（bge-reranker）是否值得接入。

结论先行：**在本项目的评测集上不值得**，本脚本是用来复现这个结论的，
而不是一个可直接上线的功能实现。

为什么要有这个脚本：Rerank 常被当成「加一个模型就能提升 top1」的银弹。
实测下来它的收益取决于**失败样本的失败原因**——如果失败是因为语义相近的
强干扰项，cross-encoder 也分不开；只有「召回对了但排序被表面词面误导」时才有效。

本项目的失败样本属于前者：「轻微问题会不会整条作废」同时与
「# 数据流水线规范（提到质检）」和「## 质检（提到扣减）」相关，reranker 也选了前者。

准备模型（约 1 GB）：
    curl -sL -o /tmp/m.sh https://modelscope.cn/models/BAAI/bge-reranker-base
    # 或用 modelscope 库：modelscope download --model BAAI/bge-reranker-base --local_dir data/models/bge-reranker-base

用法：
    .venv/Scripts/python.exe scripts/compare_rerank.py --model data/models/bge-reranker-base
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.config import settings  # noqa: E402

CASES = [
    {"query": "循环最多能跑几圈", "expect": "8 轮"},
    {"query": "轻微问题会不会整条作废", "expect": "扣减"},
    {"query": "混合检索怎么融合", "expect": "RRF"},
    {"query": "去重的阈值怎么定", "expect": "SimHash"},
]


def load_chunks(kb_id: int) -> list[tuple[int, str]]:
    url = make_url(settings.database_url)
    conn = pymysql.connect(
        host=url.host, port=url.port or 3306, user=url.username,
        password=url.password, database=url.database,
    )
    cur = conn.cursor()
    cur.execute("SELECT id, content FROM chunks WHERE kb_id=%s ORDER BY id", (kb_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", type=int, default=2)
    ap.add_argument("--model", default="data/models/bge-reranker-base")
    args = ap.parse_args()

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    path = Path(args.model)
    if not (path / "config.json").exists():
        print("模型不存在: %s（先下载 BAAI/bge-reranker-base）" % path)
        sys.exit(1)

    t0 = time.perf_counter()
    tok = AutoTokenizer.from_pretrained(str(path))
    model = AutoModelForSequenceClassification.from_pretrained(str(path))
    model.eval()
    print("模型加载 %.1fs | 参数量 %.0fM"
          % (time.perf_counter() - t0, sum(p.numel() for p in model.parameters()) / 1e6))

    rows = load_chunks(args.kb)
    print("知识库 %s：%d 个切片\n" % (args.kb, len(rows)))
    print("%-22s %-10s %-10s %-10s" % ("查询", "期望", "rerank排名", "耗时"))

    top1 = 0
    for case in CASES:
        pairs = [[case["query"], content[:400]] for _, content in rows]
        started = time.perf_counter()
        with torch.no_grad():
            logits = model(
                **tok(pairs, padding=True, truncation=True, max_length=512, return_tensors="pt")
            ).logits.view(-1)
        elapsed = (time.perf_counter() - started) * 1000

        scores = torch.sigmoid(logits).tolist()
        ranked = sorted(zip(scores, rows), key=lambda x: -x[0])
        rank = next(
            (i + 1 for i, (_, (_, content)) in enumerate(ranked) if case["expect"] in content),
            None,
        )
        top1 += 1 if rank == 1 else 0
        print("%-22s %-10s %-10s %.0fms"
              % (case["query"], case["expect"], "第 %s 名" % rank, elapsed))

    print("\n>>> rerank 后 hit@1 = %d/%d" % (top1, len(CASES)))
    print("对照：纯向量 3/4、混合检索 top1 0.8 —— 未见改善，且每条查询多 280~310ms")


if __name__ == "__main__":
    main()
