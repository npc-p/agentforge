"""嵌入模型效果对比：hashing vs 本地 BGE-M3（纯向量，不含 BM25）。

为什么单独写脚本而不直接跑评测套件：评测走的是向量 + BM25 + RRF 的混合
检索，BM25 会掩盖向量本身的语义能力差异。这里只做「查询 × 切片」的纯余弦
排序，把变量控制到最小，才能说清「换模型到底改善了什么」。

用法：
    .venv/Scripts/python.exe scripts/compare_embedder.py --kb 2
    .venv/Scripts/python.exe scripts/compare_embedder.py --kb 2 --provider bge-m3
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql  # noqa: E402

from app.config import settings  # noqa: E402

# 前两条是「零字面重叠」困难用例：查询与目标片段没有任何连续字面重合，
# 只有理解语义才能命中；后两条是常规用例，用作对照。
CASES = [
    {"query": "循环最多能跑几圈", "expect": "8 轮", "kind": "零重叠"},
    {"query": "轻微问题会不会整条作废", "expect": "扣减", "kind": "零重叠"},
    {"query": "混合检索怎么融合", "expect": "RRF", "kind": "常规"},
    {"query": "去重的阈值怎么定", "expect": "SimHash", "kind": "常规"},
]


def load_chunks(kb_id: int) -> list[dict]:
    # 用 SQLAlchemy 解析 DSN：手写 split 遇到密码中的 : / @ 会直接崩
    from sqlalchemy.engine import make_url

    url = make_url(settings.database_url)
    conn = pymysql.connect(
        host=url.host,
        port=url.port or 3306,
        user=url.username,
        password=url.password,
        database=url.database,
    )
    cur = conn.cursor(pymysql.cursors.DictCursor)
    cur.execute(
        "SELECT id, content FROM chunks WHERE kb_id=%s ORDER BY id", (kb_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


async def run(kb_id: int, providers: list[str]) -> None:
    from app.rag.embedder import HashingEmbedder, LocalBgeEmbedder

    chunks = load_chunks(kb_id)
    if not chunks:
        print("知识库 %s 没有切片" % kb_id)
        return
    print("知识库 %s：%d 个切片\n" % (kb_id, len(chunks)))

    for provider in providers:
        emb = LocalBgeEmbedder() if provider == "bge-m3" else HashingEmbedder()
        chunk_vecs = await emb.embed([c["content"] for c in chunks])
        print("=" * 72)
        print("嵌入：%s  dim=%s  切片=%s" % (emb.name, emb.dim, len(chunks)))
        print("=" * 72)
        hits = 0
        for case in CASES:
            qv = await emb.embed_one(case["query"])
            scored = sorted(
                (
                    (cosine(qv, vec), chunk)
                    for vec, chunk in zip(chunk_vecs, chunks)
                ),
                key=lambda x: x[0],
                reverse=True,
            )
            rank = next(
                (i + 1 for i, (_, c) in enumerate(scored) if case["expect"] in c["content"]),
                None,
            )
            top1_hit = bool(rank == 1)
            hits += 1 if top1_hit else 0
            print(
                "\n[%s] %s → 期望含「%s」的目标片段排名: %s %s"
                % (case["kind"], case["query"], case["expect"], rank,
                   "✓ top1" if top1_hit else "✗")
            )
            for score, chunk in scored[:3]:
                mark = " ← 目标" if case["expect"] in chunk["content"] else ""
                print(
                    "   %.4f  chunk#%-3s %s%s"
                    % (score, chunk["id"], chunk["content"][:42].replace("\n", " "), mark)
                )
        print("\n>>> %s 的 hit@1 = %d/%d\n" % (emb.name, hits, len(CASES)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", type=int, default=2)
    ap.add_argument(
        "--provider", default="hashing,bge-m3",
        help="逗号分隔，可选 hashing / bge-m3",
    )
    args = ap.parse_args()
    asyncio.run(run(args.kb, [p.strip() for p in args.provider.split(",") if p.strip()]))


if __name__ == "__main__":
    main()
