"""按当前切分策略重新入库 seed 文档。

用途：切分逻辑（或块长、overlap）改动后需要重切时使用。

为什么需要专门脚本：`chunks` 是切分产物，策略一变必须重切；而文档原文没有
落盘（`documents` 只存 sha256），无法从库里还原，所以只能以 `seed.DOCS`
为源重新灌入。删旧 + 建新，向量随之重建，`kb_id` 不变，不影响评测配置。

用法：
    .venv/Scripts/python.exe scripts/resplit.py --kb 2
    .venv/Scripts/python.exe scripts/resplit.py --kb 2 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import func, select  # noqa: E402

from app.db.session import AsyncSessionLocal  # noqa: E402
from app.models.knowledge import Chunk, Document, KnowledgeBase  # noqa: E402
from app.rag import service as rag_service  # noqa: E402
from seed import DOCS  # noqa: E402


async def run(kb_id: int, dry_run: bool) -> None:
    async with AsyncSessionLocal() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        if kb is None:
            print("知识库不存在: %s" % kb_id)
            return

        for filename, text in DOCS:
            old = (
                await session.execute(
                    select(Document).where(
                        Document.kb_id == kb_id, Document.filename == filename
                    )
                )
            ).scalars().first()
            old_count = old.chunk_count if old else 0

            chunks = rag_service.split_text(text) if hasattr(rag_service, "split_text") else None
            preview = len(chunks) if chunks else "?"
            print("%-24s 旧 %s 块 -> 新 %s 块" % (filename, old_count, preview))

            if dry_run:
                continue
            if old is not None:
                await rag_service.delete_document(session, old.id)
            doc = await rag_service.ingest_text(
                session, kb_id, filename=filename, text=text, ext="md"
            )
            print("   已重入 doc=%s chunks=%s chars=%s"
                  % (doc.id, doc.chunk_count, doc.char_count))

        if not dry_run:
            doc_count = (
                await session.execute(
                    select(func.count()).select_from(Document).where(Document.kb_id == kb_id)
                )
            ).scalar()
            chunk_count = (
                await session.execute(
                    select(func.count()).select_from(Chunk).where(Chunk.kb_id == kb_id)
                )
            ).scalar()
            print("\n知识库 %s 当前: 文档 %s / 切片 %s" % (kb_id, doc_count, chunk_count))

    if not dry_run:
        _ask_service_rebuild(kb_id)


def _ask_service_rebuild(kb_id: int) -> None:
    """让**服务进程**重建向量索引。

    这一步不能省：Chroma 是内嵌的 SQLite，一个进程写入的数据，另一个进程
    （本脚本 vs 服务）不一定能立刻看到——实测脚本写完向量后，服务仍按旧
    segment 检索，结果为空。所以向量的最终写入必须发生在服务进程内，
    这里通过 HTTP 触发它的 rebuild 接口。
    """
    import httpx

    url = "http://127.0.0.1:8010/api/knowledge/bases/%d/rebuild" % kb_id
    try:
        resp = httpx.post(url, timeout=300)
        data = resp.json()
        if data.get("code") == 0:
            print("向量索引已由服务进程重建: chunks=%s" % data["data"].get("chunks"))
        else:
            print("重建返回异常: %s" % data.get("message"))
    except Exception as exc:  # noqa: BLE001
        print(
            "⚠ 无法调用服务重建向量（%s）。\n"
            "  Chroma 跨进程不可见，请启动后端后手动执行：\n"
            "  curl -X POST %s" % (exc, url)
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(args.kb, args.dry_run))


if __name__ == "__main__":
    main()
