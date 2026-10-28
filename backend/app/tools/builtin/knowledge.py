"""知识检索工具：把 RAG 能力暴露给 Agent 的工具面。

采用延迟导入避免循环依赖（RAG 服务依赖数据库与会话，工具层不做前置初始化）。
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from app.tools.registry import tool


@tool(
    name="knowledge_search",
    display_name="知识库检索",
    description="在指定或默认知识库中做混合检索（向量 + BM25），返回带出处的高相关片段。",
    category="knowledge",
    timeout_s=25.0,
    idempotent=True,
    tags=["rag", "retrieval"],
)
async def knowledge_search(
    query: Annotated[str, Field(description="检索问题，使用自然语言即可")],
    kb_id: Annotated[int | None, Field(description="知识库 ID，留空则检索全部知识库")] = None,
    top_k: Annotated[int, Field(ge=1, le=15, description="返回片段数量")] = 5,
) -> dict[str, Any]:
    from app.rag.service import search_knowledge

    hits = await search_knowledge(query=query, kb_id=kb_id, top_k=top_k)
    return {
        "query": query,
        "hit_count": len(hits),
        "hits": [
            {
                "chunk_id": h["chunk_id"],
                "doc_name": h["doc_name"],
                "score": round(h["score"], 4),
                "content": h["content"],
                "vector_rank": h.get("vector_rank"),
                "bm25_rank": h.get("bm25_rank"),
            }
            for h in hits
        ],
    }
