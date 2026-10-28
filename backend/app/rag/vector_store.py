"""向量库抽象与 Chroma 实现。

抽象的意义不在于「现在支持多个后端」，而在于：
1. 业务层不出现任何 chromadb 类型，升级或替换只影响本文件；
2. 单元测试可注入内存实现，避免测试依赖磁盘状态。
当前仅实现 Chroma；未实现的后端在工厂处显式报错，不留静默降级。
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.core.exceptions import VectorStoreError
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class VectorHit:
    id: str
    score: float
    document: str
    metadata: dict[str, Any]


class VectorStore(ABC):
    @abstractmethod
    def add(
        self,
        collection: str,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None: ...

    @abstractmethod
    def query(
        self,
        collection: str,
        *,
        embedding: list[float],
        top_k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[VectorHit]: ...

    @abstractmethod
    def delete(
        self,
        collection: str,
        *,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> None: ...

    @abstractmethod
    def count(self, collection: str) -> int: ...

    @abstractmethod
    def drop(self, collection: str) -> None: ...

    @abstractmethod
    def health(self) -> dict[str, Any]: ...


class ChromaVectorStore(VectorStore):
    """Chroma 持久化实现。

    注意事项：
    - 传入自定义 embedding，因此不依赖 Chroma 默认的 onnx 模型，避免首次调用时联网下载；
    - 距离度量固定 cosine，query 返回的 distance 转成 similarity = 1 - distance；
    - Chroma 客户端非线程安全写入，此处加进程内锁串行化写操作。
    """

    def __init__(self, path: str | None = None) -> None:
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
        except ImportError as exc:  # pragma: no cover
            raise VectorStoreError("未安装 chromadb，请执行 uv pip install chromadb") from exc

        self._path = str(path or settings.chroma_path)
        self._lock = threading.RLock()
        self._client = chromadb.PersistentClient(
            path=self._path,
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
        )
        logger.info("Chroma 已初始化: %s", self._path)

    def _collection(self, name: str):
        try:
            return self._client.get_or_create_collection(
                name=name, metadata={"hnsw:space": "cosine"}
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"获取集合失败: {name}", detail=str(exc)[:200]) from exc

    def add(
        self,
        collection: str,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        if not ids:
            return
        coll = self._collection(collection)
        # Chroma 的 metadata 只接受标量，统一做类型收敛
        clean = [{k: _scalar(v) for k, v in (meta or {}).items()} for meta in metadatas]
        with self._lock:
            coll.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=clean)

    def query(
        self,
        collection: str,
        *,
        embedding: list[float],
        top_k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[VectorHit]:
        coll = self._collection(collection)
        try:
            result = coll.query(
                query_embeddings=[embedding],
                n_results=max(1, top_k),
                where=where or None,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError("向量检索失败", detail=str(exc)[:200]) from exc

        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]

        hits: list[VectorHit] = []
        for i, doc_id in enumerate(ids):
            distance = float(dists[i]) if i < len(dists) else 1.0
            hits.append(
                VectorHit(
                    id=str(doc_id),
                    score=max(0.0, 1.0 - distance),
                    document=docs[i] if i < len(docs) else "",
                    metadata=dict(metas[i] or {}) if i < len(metas) else {},
                )
            )
        return hits

    def delete(
        self,
        collection: str,
        *,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> None:
        coll = self._collection(collection)
        with self._lock:
            if ids:
                coll.delete(ids=ids)
            elif where:
                coll.delete(where=where)

    def count(self, collection: str) -> int:
        try:
            return int(self._collection(collection).count())
        except Exception:  # noqa: BLE001
            return 0

    def drop(self, collection: str) -> None:
        with self._lock:
            try:
                self._client.delete_collection(collection)
            except Exception as exc:  # noqa: BLE001
                logger.warning("删除集合失败（可能不存在）: %s %s", collection, exc)

    def health(self) -> dict[str, Any]:
        try:
            collections = [c.name for c in self._client.list_collections()]
            return {"backend": "chroma", "path": self._path, "ok": True, "collections": collections}
        except Exception as exc:  # noqa: BLE001
            return {"backend": "chroma", "path": self._path, "ok": False, "error": str(exc)[:200]}


def _scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        if settings.vector_store == "chroma":
            _store = ChromaVectorStore()
        else:  # pragma: no cover - 配置层已限制取值
            raise VectorStoreError(f"尚未实现的向量库后端: {settings.vector_store}")
    return _store


def collection_name(kb_id: int) -> str:
    """集合名携带向量化实现标识，避免换模型后向量混写。

    换嵌入模型意味着维度变化（hashing 512 → bge-m3 1024），往同一集合写入
    会直接报错。带上后缀后新旧索引可以并存：重建期间旧集合仍能服务，重建
    完成后才清理，出问题也能把 provider 切回去。

    hashing 保持历史命名（无后缀），使已部署的集合继续可用，不必强制重建。
    """
    from app.rag.embedder import get_embedder  # 延迟导入，避免模块级循环依赖

    name = get_embedder().name
    suffix = "" if name == "hashing" else f"_{name}"
    return f"{settings.chroma_collection_prefix}_{kb_id}{suffix}"
