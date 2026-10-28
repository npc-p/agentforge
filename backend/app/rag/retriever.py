"""混合检索：向量召回 + BM25 召回 + RRF 融合。

关键工程决策：
1. 返回给上层的正文一律取自 MySQL（真相源），而非向量库里的副本——避免两处数据
   不一致时模型引用到过期内容；
2. RRF 只使用排名不用分数，天然规避「向量相似度」与「BM25 分」量纲不可比的问题；
3. BM25 索引按「知识库集合 + 数据指纹」缓存：指纹取自库里的切片数与最大 id，
   因此**跨进程**的写入（脚本重建、其他 worker）也能让缓存失效——早期用进程内
   计数器做代数，多进程下永远不会失效，会导致改了数据却检索到旧结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.models.knowledge import Chunk, Document
from app.rag.bm25 import BM25Index
from app.rag.embedder import get_embedder
from app.rag.vector_store import collection_name, get_vector_store

logger = get_logger(__name__)

RRF_K = 60

# (kb_id 集合) -> (数据指纹, BM25 索引)
_bm25_cache: dict[tuple[int, ...], tuple[tuple[tuple[int, int, int], ...], BM25Index]] = {}


def bump_generation(kb_id: int) -> None:
    """显式失效：清掉涉及该知识库的缓存条目。

    与 `_data_fingerprint` 的关系是「快路径」而非唯一手段：本进程写入后立即
    清缓存可以少做一次聚合查询；跨进程的写入则由指纹变化兜住。
    """
    for key in [k for k in _bm25_cache if kb_id in k]:
        _bm25_cache.pop(key, None)


async def _data_fingerprint(
    session: AsyncSession, kb_ids: list[int]
) -> tuple[tuple[int, int, int], ...]:
    """数据指纹：(kb_id, 切片数, 最大切片 id)。

    **为什么不能用进程内计数器**：计数器只在本进程写入时自增。脚本重建索引、
    另一个 worker、多副本部署都不会让它变化 —— 缓存于是永不失效，表现为
    「数据已经更新，但检索仍返回旧结果、甚至因为 id 已删除而返回空」。

    改用库里的真实状态做指纹后，任何进程的写入都能被下一个请求感知。代价是
    每次检索多一次 `group by` 聚合（走 `kb_id` 索引，14 条切片的实测开销 <1ms）。
    """
    rows = (
        await session.execute(
            select(Chunk.kb_id, func.count(Chunk.id), func.max(Chunk.id))
            .where(Chunk.kb_id.in_(kb_ids))
            .group_by(Chunk.kb_id)
        )
    ).all()
    found = {int(kb): (int(cnt), int(mx or 0)) for kb, cnt, mx in rows}
    return tuple((kb, *found.get(kb, (0, 0))) for kb in sorted(kb_ids))


@dataclass(slots=True)
class RetrievalHit:
    chunk_id: int
    doc_id: int
    doc_name: str
    kb_id: int
    content: str
    score: float
    vector_score: float = 0.0
    bm25_score: float = 0.0
    vector_rank: int | None = None
    bm25_rank: int | None = None
    seq: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "doc_name": self.doc_name,
            "kb_id": self.kb_id,
            "content": self.content,
            "score": self.score,
            "vector_score": self.vector_score,
            "bm25_score": self.bm25_score,
            "vector_rank": self.vector_rank,
            "bm25_rank": self.bm25_rank,
            "seq": self.seq,
        }


async def _build_bm25(session: AsyncSession, kb_ids: list[int]) -> BM25Index:
    key = tuple(sorted(kb_ids))
    fingerprint = await _data_fingerprint(session, kb_ids)
    cached = _bm25_cache.get(key)
    if cached and cached[0] == fingerprint:
        return cached[1]

    index = BM25Index()
    stmt = select(Chunk.id, Chunk.content).where(Chunk.kb_id.in_(kb_ids))
    rows = (await session.execute(stmt)).all()
    for chunk_id, content in rows:
        index.add(str(chunk_id), content)
    _bm25_cache[key] = (fingerprint, index)
    logger.info("BM25 索引已重建: kbs=%s docs=%s", key, len(index))
    return index


async def _load_chunk_map(
    session: AsyncSession, chunk_ids: list[int]
) -> dict[int, tuple[Chunk, str]]:
    if not chunk_ids:
        return {}
    stmt = (
        select(Chunk, Document.filename)
        .join(Document, Chunk.doc_id == Document.id)
        .where(Chunk.id.in_(chunk_ids))
    )
    rows = (await session.execute(stmt)).all()
    return {row[0].id: (row[0], row[1]) for row in rows}


async def hybrid_search(
    session: AsyncSession,
    query: str,
    *,
    kb_id: int | None = None,
    top_k: int | None = None,
    vector_weight: float | None = None,
    bm25_weight: float | None = None,
) -> list[RetrievalHit]:
    top_k = top_k or settings.rag_top_k
    vw = settings.rag_vector_weight if vector_weight is None else vector_weight
    bw = settings.rag_bm25_weight if bm25_weight is None else bm25_weight

    if kb_id is not None:
        kb_ids = [kb_id]
    else:
        from app.models.knowledge import KnowledgeBase

        kb_ids = list((await session.execute(select(KnowledgeBase.id))).scalars().all())
    if not kb_ids:
        return []

    # ---- 向量召回
    embedder = get_embedder()
    store = get_vector_store()
    query_vec = await embedder.embed_one(query)
    vector_hits: list[tuple[int, float]] = []
    for kid in kb_ids:
        for hit in store.query(
            collection_name(kid), embedding=query_vec, top_k=max(top_k * 3, 10)
        ):
            try:
                vector_hits.append((int(hit.id), hit.score))
            except (TypeError, ValueError):
                continue
    vector_hits.sort(key=lambda x: x[1], reverse=True)
    vector_rank = {chunk_id: idx + 1 for idx, (chunk_id, _) in enumerate(vector_hits)}
    vector_score = dict(vector_hits)

    # ---- BM25 召回
    bm25_index = await _build_bm25(session, kb_ids)
    bm25_raw = bm25_index.search(query, top_k=max(top_k * 3, 10))
    bm25_rank = {int(chunk_id): idx + 1 for idx, (chunk_id, _) in enumerate(bm25_raw)}
    bm25_score = {int(chunk_id): score for chunk_id, score in bm25_raw}

    # ---- RRF 融合
    candidates = set(vector_rank) | set(bm25_rank)
    fused: list[tuple[int, float]] = []
    for chunk_id in candidates:
        score = 0.0
        if chunk_id in vector_rank:
            score += vw * (1.0 / (RRF_K + vector_rank[chunk_id]))
        if chunk_id in bm25_rank:
            score += bw * (1.0 / (RRF_K + bm25_rank[chunk_id]))
        fused.append((chunk_id, score))
    fused.sort(key=lambda x: x[1], reverse=True)
    top = fused[:top_k]
    if not top:
        return []

    chunk_map = await _load_chunk_map(session, [cid for cid, _ in top])
    max_score = top[0][1] or 1.0

    hits: list[RetrievalHit] = []
    for chunk_id, score in top:
        pair = chunk_map.get(chunk_id)
        if pair is None:  # 索引里有、库里已删：以库为准，跳过
            continue
        chunk, doc_name = pair
        hits.append(
            RetrievalHit(
                chunk_id=chunk.id,
                doc_id=chunk.doc_id,
                doc_name=doc_name,
                kb_id=chunk.kb_id,
                content=chunk.content,
                score=round(score / max_score, 4),
                vector_score=round(vector_score.get(chunk_id, 0.0), 4),
                bm25_score=round(bm25_score.get(chunk_id, 0.0), 4),
                vector_rank=vector_rank.get(chunk_id),
                bm25_rank=bm25_rank.get(chunk_id),
                seq=chunk.seq,
                meta=chunk.meta or {},
            )
        )
    return hits
