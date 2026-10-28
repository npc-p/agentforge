"""知识库接口：知识库 CRUD、文档上传、检索、重建索引。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session
from app.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import Timer
from app.core.response import ok
from app.models.knowledge import Chunk, Document, KnowledgeBase
from app.rag import service as rag_service
from app.rag.retriever import hybrid_search
from app.schemas.knowledge import KBCreate, KBUpdate, SearchRequest, TextIngest

router = APIRouter(tags=["knowledge"])


@router.get("/knowledge/bases")
async def list_bases(session: AsyncSession = Depends(db_session)):
    rows = (
        await session.execute(select(KnowledgeBase).order_by(KnowledgeBase.id))
    ).scalars().all()
    return ok(
        [
            {
                "id": kb.id,
                "name": kb.name,
                "description": kb.description,
                "embed_provider": kb.embed_provider,
                "embed_model": kb.embed_model,
                "embed_dim": kb.embed_dim,
                "chunk_size": kb.chunk_size,
                "chunk_overlap": kb.chunk_overlap,
                "doc_count": kb.doc_count,
                "chunk_count": kb.chunk_count,
                "status": kb.status,
                "created_at": kb.created_at.isoformat() if kb.created_at else None,
            }
            for kb in rows
        ]
    )


@router.post("/knowledge/bases")
async def create_base(payload: KBCreate, session: AsyncSession = Depends(db_session)):
    kb = await rag_service.create_kb(
        session,
        name=payload.name,
        description=payload.description,
        chunk_size=payload.chunk_size,
        chunk_overlap=payload.chunk_overlap,
    )
    return ok({"id": kb.id, "name": kb.name, "embed_dim": kb.embed_dim})


@router.patch("/knowledge/bases/{kb_id}")
async def update_base(kb_id: int, payload: KBUpdate, session: AsyncSession = Depends(db_session)):
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None:
        raise NotFoundError(f"知识库不存在: {kb_id}")
    if payload.description is not None:
        kb.description = payload.description
    if payload.chunk_size is not None:
        kb.chunk_size = payload.chunk_size
    if payload.chunk_overlap is not None:
        kb.chunk_overlap = payload.chunk_overlap
    await session.commit()
    return ok({"id": kb.id, "updated": True})


@router.delete("/knowledge/bases/{kb_id}")
async def delete_base(kb_id: int, session: AsyncSession = Depends(db_session)):
    return ok(await rag_service.delete_kb(session, kb_id))


@router.post("/knowledge/bases/{kb_id}/upload")
async def upload_document(
    kb_id: int,
    file: UploadFile = File(..., description="支持 txt/md/pdf/docx/csv/json"),
    session: AsyncSession = Depends(db_session),
):
    data = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise ValidationError(f"文件超过 {settings.max_upload_mb}MB 限制")
    if not data:
        raise ValidationError("上传文件为空")

    doc = await rag_service.ingest_file(
        session, kb_id, filename=file.filename or "upload.txt", data=data
    )
    return ok(
        {
            "document_id": doc.id,
            "filename": doc.filename,
            "status": doc.status,
            "chunk_count": doc.chunk_count,
            "char_count": doc.char_count,
        }
    )


@router.post("/knowledge/ingest-text")
async def ingest_text(payload: TextIngest, session: AsyncSession = Depends(db_session)):
    doc = await rag_service.ingest_text(
        session, payload.kb_id, filename=payload.filename, text=payload.text
    )
    return ok(
        {"document_id": doc.id, "status": doc.status, "chunk_count": doc.chunk_count}
    )


@router.get("/knowledge/bases/{kb_id}/documents")
async def list_documents(kb_id: int, session: AsyncSession = Depends(db_session)):
    rows = (
        await session.execute(
            select(Document).where(Document.kb_id == kb_id).order_by(Document.id.desc())
        )
    ).scalars().all()
    return ok(
        [
            {
                "id": d.id,
                "kb_id": d.kb_id,
                "filename": d.filename,
                "ext": d.ext,
                "size_bytes": d.size_bytes,
                "status": d.status,
                "chunk_count": d.chunk_count,
                "char_count": d.char_count,
                "error": d.error,
                "meta": d.meta,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in rows
        ]
    )


@router.delete("/knowledge/documents/{doc_id}")
async def delete_document(doc_id: int, session: AsyncSession = Depends(db_session)):
    return ok(await rag_service.delete_document(session, doc_id))


@router.get("/knowledge/documents/{doc_id}/chunks")
async def list_chunks(doc_id: int, session: AsyncSession = Depends(db_session)):
    rows = (
        await session.execute(
            select(Chunk).where(Chunk.doc_id == doc_id).order_by(Chunk.seq)
        )
    ).scalars().all()
    return ok(
        [
            {
                "id": c.id,
                "seq": c.seq,
                "content": c.content,
                "char_count": c.char_count,
                "tokens": c.tokens,
                "vector_id": c.vector_id,
            }
            for c in rows
        ]
    )


@router.post("/knowledge/search")
async def search(payload: SearchRequest, session: AsyncSession = Depends(db_session)):
    with Timer() as timer:
        hits = await hybrid_search(
            session, payload.query, kb_id=payload.kb_id, top_k=payload.top_k
        )
    return ok(
        {
            "query": payload.query,
            "hit_count": len(hits),
            "latency_ms": timer.elapsed_ms,
            "hits": [h.to_dict() for h in hits],
        }
    )


@router.post("/knowledge/bases/{kb_id}/rebuild")
async def rebuild(kb_id: int, session: AsyncSession = Depends(db_session)):
    return ok(await rag_service.rebuild_index(session, kb_id))


@router.get("/knowledge/stats")
async def knowledge_stats(session: AsyncSession = Depends(db_session)):
    kb_count = int((await session.execute(select(func.count(KnowledgeBase.id)))).scalar_one())
    doc_count = int((await session.execute(select(func.count(Document.id)))).scalar_one())
    chunk_count = int((await session.execute(select(func.count(Chunk.id)))).scalar_one())
    return ok(
        {"knowledge_bases": kb_count, "documents": doc_count, "chunks": chunk_count}
    )
