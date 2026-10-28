"""知识库服务：入库、检索、删除、重建索引。

事务边界：文档行先落库（拿到自增 ID 供向量库引用），再批量写入切片与向量；
任一步失败则把文档置为 failed 并保留错误原因，保证状态机不会停在中间态。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import Timer, get_logger
from app.core.text import sha256_text
from app.db.session import AsyncSessionLocal
from app.models.knowledge import Chunk, Document, KnowledgeBase
from app.rag.embedder import get_embedder
from app.rag.parser import extract_text
from app.rag.retriever import bump_generation, hybrid_search
from app.rag.splitter import split_text
from app.rag.vector_store import collection_name, get_vector_store

logger = get_logger(__name__)

EMBED_BATCH = 32


async def create_kb(
    session: AsyncSession,
    *,
    name: str,
    description: str = "",
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> KnowledgeBase:
    exists = (
        await session.execute(select(KnowledgeBase).where(KnowledgeBase.name == name))
    ).scalar_one_or_none()
    if exists:
        raise ConflictError(f"知识库名称已存在: {name}")

    embedder = get_embedder()
    kb = KnowledgeBase(
        name=name,
        description=description,
        embed_provider=embedder.name,
        embed_model=getattr(embedder, "model", embedder.name),
        embed_dim=embedder.dim,
        chunk_size=chunk_size or settings.rag_chunk_size,
        chunk_overlap=chunk_overlap or settings.rag_chunk_overlap,
    )
    session.add(kb)
    await session.commit()
    return kb


async def ingest_text(
    session: AsyncSession,
    kb_id: int,
    *,
    filename: str,
    text: str,
    ext: str = "txt",
    meta: dict[str, Any] | None = None,
) -> Document:
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None:
        raise NotFoundError(f"知识库不存在: {kb_id}")

    digest = sha256_text(text)
    duplicated = (
        await session.execute(
            select(Document).where(Document.kb_id == kb_id, Document.sha256 == digest)
        )
    ).scalar_one_or_none()
    if duplicated is not None:
        raise ConflictError(
            f"文档内容已存在（document_id={duplicated.id}）", detail={"document_id": duplicated.id}
        )

    doc = Document(
        kb_id=kb_id,
        filename=filename,
        ext=ext,
        size_bytes=len(text.encode("utf-8")),
        sha256=digest,
        status="chunking",
        char_count=len(text),
        meta=meta or {},
    )
    session.add(doc)
    await session.commit()

    try:
        with Timer() as timer:
            pieces = split_text(
                text, chunk_size=kb.chunk_size, chunk_overlap=kb.chunk_overlap
            )
            if not pieces:
                raise ConflictError("切分结果为空，请检查文档内容")

            embedder = get_embedder()
            store = get_vector_store()
            coll = collection_name(kb_id)

            for start in range(0, len(pieces), EMBED_BATCH):
                batch = pieces[start : start + EMBED_BATCH]
                vectors = await embedder.embed([p.content for p in batch])
                rows = [
                    Chunk(
                        doc_id=doc.id,
                        kb_id=kb_id,
                        seq=piece.seq,
                        content=piece.content,
                        char_count=piece.char_count,
                        tokens=piece.tokens_estimate,
                        meta={"filename": filename, "ext": ext},
                    )
                    for piece in batch
                ]
                session.add_all(rows)
                await session.flush()  # 拿到自增 ID 才能写入向量库

                store.add(
                    coll,
                    ids=[str(row.id) for row in rows],
                    embeddings=vectors,
                    documents=[row.content for row in rows],
                    metadatas=[
                        {
                            "kb_id": kb_id,
                            "doc_id": doc.id,
                            "doc_name": filename,
                            "seq": row.seq,
                        }
                        for row in rows
                    ],
                )
                for row in rows:
                    row.vector_id = str(row.id)
                await session.commit()

        doc.status = "ready"
        doc.chunk_count = len(pieces)
        doc.error = None
        kb.chunk_count = int(
            (
                await session.execute(
                    select(func.count(Chunk.id)).where(Chunk.kb_id == kb_id)
                )
            ).scalar_one()
        )
        kb.doc_count = int(
            (
                await session.execute(
                    select(func.count(Document.id)).where(Document.kb_id == kb_id)
                )
            ).scalar_one()
        )
        await session.commit()
        bump_generation(kb_id)
        logger.info(
            "文档入库完成 doc=%s chunks=%s 耗时=%sms", doc.id, len(pieces), timer.elapsed_ms
        )
        return doc

    except Exception as exc:
        await session.rollback()
        doc = await session.get(Document, doc.id)
        if doc is not None:
            doc.status = "failed"
            doc.error = str(exc)[:500]
            await session.commit()
        logger.error("文档入库失败 doc=%s: %s", doc.id if doc else "?", exc)
        raise


async def ingest_file(
    session: AsyncSession, kb_id: int, *, filename: str, data: bytes
) -> Document:
    parsed = extract_text(filename, data)
    return await ingest_text(
        session,
        kb_id,
        filename=filename,
        text=parsed.text,
        ext=parsed.ext,
        meta=parsed.meta,
    )


async def search_knowledge(
    query: str,
    *,
    kb_id: int | None = None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """供工具层调用：自带会话，返回纯字典，避免跨层泄漏 ORM 对象。"""
    async with AsyncSessionLocal() as session:
        hits = await hybrid_search(session, query, kb_id=kb_id, top_k=top_k)
        return [hit.to_dict() for hit in hits]


async def delete_document(session: AsyncSession, doc_id: int) -> dict[str, Any]:
    doc = await session.get(Document, doc_id)
    if doc is None:
        raise NotFoundError(f"文档不存在: {doc_id}")

    chunk_ids = list(
        (
            await session.execute(select(Chunk.id).where(Chunk.doc_id == doc_id))
        ).scalars().all()
    )
    if chunk_ids:
        get_vector_store().delete(collection_name(doc.kb_id), ids=[str(c) for c in chunk_ids])

    await session.execute(delete(Chunk).where(Chunk.doc_id == doc_id))
    kb_id = doc.kb_id
    await session.delete(doc)
    await session.commit()

    kb = await session.get(KnowledgeBase, kb_id)
    if kb is not None:
        kb.chunk_count = int(
            (await session.execute(select(func.count(Chunk.id)).where(Chunk.kb_id == kb_id))).scalar_one()
        )
        kb.doc_count = int(
            (
                await session.execute(
                    select(func.count(Document.id)).where(Document.kb_id == kb_id)
                )
            ).scalar_one()
        )
        await session.commit()
    bump_generation(kb_id)
    return {"document_id": doc_id, "deleted_chunks": len(chunk_ids)}


async def delete_kb(session: AsyncSession, kb_id: int) -> dict[str, Any]:
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None:
        raise NotFoundError(f"知识库不存在: {kb_id}")
    get_vector_store().drop(collection_name(kb_id))
    await session.execute(delete(Chunk).where(Chunk.kb_id == kb_id))
    await session.execute(delete(Document).where(Document.kb_id == kb_id))
    await session.delete(kb)
    await session.commit()
    bump_generation(kb_id)
    return {"knowledge_base_id": kb_id, "dropped": True}


async def rebuild_index(session: AsyncSession, kb_id: int) -> dict[str, Any]:
    """从 MySQL 重建向量索引——向量库被视为可丢弃的派生数据。"""
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None:
        raise NotFoundError(f"知识库不存在: {kb_id}")

    rows = (
        await session.execute(
            select(Chunk, Document.filename)
            .join(Document, Chunk.doc_id == Document.id)
            .where(Chunk.kb_id == kb_id)
            .order_by(Chunk.id)
        )
    ).all()
    store = get_vector_store()
    coll = collection_name(kb_id)
    store.drop(coll)

    embedder = get_embedder()
    for start in range(0, len(rows), EMBED_BATCH):
        batch = rows[start : start + EMBED_BATCH]
        vectors = await embedder.embed([row[0].content for row in batch])
        store.add(
            coll,
            ids=[str(row[0].id) for row in batch],
            embeddings=vectors,
            documents=[row[0].content for row in batch],
            metadatas=[
                {"kb_id": kb_id, "doc_id": row[0].doc_id, "doc_name": row[1], "seq": row[0].seq}
                for row in batch
            ],
        )
    bump_generation(kb_id)
    logger.info("知识库索引重建完成 kb=%s chunks=%s", kb_id, len(rows))
    return {"knowledge_base_id": kb_id, "chunks": len(rows), "rebuilt_at": datetime.now().isoformat()}
