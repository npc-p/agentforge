"""可插拔向量化。

默认实现 `HashingEmbedder` 是纯 Python 的字符 n-gram 哈希向量：
- 零依赖、离线可用、结果可复现（用 blake2b 而非内置 hash，避免进程间随机化）；
- 语义能力弱于神经嵌入，但配合 BM25 混合检索足以支撑中小知识库；
- 需要更强语义时把 EMBED_PROVIDER 切到 openai，接任意 OpenAI 兼容 embeddings 端点。

这是明确的权衡：用少量召回质量换取「零外部依赖、装完即跑」。
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import threading
import unicodedata
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[a-zA-Z0-9_]+")


class Embedder(ABC):
    name: str = "base"
    dim: int = 0

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    async def embed_one(self, text: str) -> list[float]:
        vectors = await self.embed([text])
        return vectors[0]

    async def warmup(self) -> None:
        """预加载模型 / 建立连接。默认无操作，由需要预热的实现覆盖。

        存在的意义：把「首次调用的重初始化」从用户请求路径挪到启动阶段，
        否则第一个查询要替服务付这笔启动成本。
        """
        return None


class HashingEmbedder(Embedder):
    """字符 n-gram 哈希 + 次线性 TF 加权 + L2 归一化。

    等价于固定维度、固定哈希种子的 TF 向量：既能保留词面相似度，
    又不需要构建全局词表，便于增量写入。
    """

    name = "hashing"

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.embed_dim

    def _features(self, text: str) -> list[str]:
        normalized = unicodedata.normalize("NFKC", text or "").lower()
        features: list[str] = []

        for word in _LATIN.findall(normalized):
            features.append(f"w:{word}")

        cjk_chars = _CJK.findall(normalized)
        features.extend(f"c:{ch}" for ch in cjk_chars)
        for i in range(len(cjk_chars) - 1):
            features.append(f"b:{cjk_chars[i]}{cjk_chars[i + 1]}")

        for token in _LATIN.findall(normalized.replace("_", " ")):
            for i in range(len(token) - 2):
                features.append(f"t:{token[i : i + 3]}")
        return features

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        counts: dict[str, int] = {}
        for feat in self._features(text):
            counts[feat] = counts.get(feat, 0) + 1

        for feat, tf in counts.items():
            digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[bucket] += sign * (1.0 + math.log(tf))

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class OpenAICompatEmbedder(Embedder):
    """任意 OpenAI 兼容 embeddings 端点（BGE-M3、text-embedding-3-small 等）。"""

    name = "openai"

    def __init__(self) -> None:
        self.model = settings.embed_model
        self.base_url = settings.embed_base_url.rstrip("/") or "https://api.openai.com/v1"
        self.api_key = settings.embed_api_key
        self.dim = settings.embed_dim
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(30.0, connect=10.0),
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise AppError("未配置 EMBED_API_KEY，无法使用远程嵌入", code="embed_not_configured")
        resp = await self._http().post(
            "/embeddings", json={"model": self.model, "input": texts}
        )
        if resp.status_code >= 400:
            raise AppError(
                f"嵌入服务返回 {resp.status_code}",
                code="embed_failed",
                detail=resp.text[:200],
            )
        data = resp.json().get("data") or []
        vectors = [item.get("embedding") or [] for item in data]
        if len(vectors) != len(texts) or not vectors:
            raise AppError("嵌入服务返回数量不匹配", code="embed_failed")
        self.dim = len(vectors[0])
        return vectors


class LocalBgeEmbedder(Embedder):
    """本地 BGE-M3（transformers + torch，CPU 推理）。

    只取 dense 向量：BGE-M3 的句向量 = 最后一层 CLS 位置输出 + L2 归一化
    （见模型自带 `1_Pooling/config.json` 的 `pooling_mode_cls_token=true`），
    因此不必引入 sentence-transformers 及其 scikit-learn / scipy 依赖。

    两个工程要点：
    - **懒加载**：2.2G 权重加载约 30s，放在首次调用而非启动时，否则每次重启
      都要多等半分钟；
    - **to_thread**：torch 推理同步阻塞，直接在协程里跑会卡死整个事件循环，
      必须丢到线程池。
    """

    name = "bge-m3"

    def __init__(self, path: str | None = None) -> None:
        self.path = path or str(settings.embed_model_dir)
        self.dim = 1024
        self.batch_size = settings.embed_batch_size
        self._tok: Any = None
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:  # 双检锁：并发首调只加载一次
                return
            path = Path(self.path)
            if not (path / "config.json").exists():
                raise AppError(
                    f"本地向量模型不存在或缺少 config.json: {path}",
                    code="embed_model_missing",
                )
            try:
                from transformers import AutoModel, AutoTokenizer
                from transformers.utils import logging as hf_logging
            except ImportError as exc:  # pragma: no cover - 依赖缺失时的引导
                raise AppError(
                    "未安装本地向量化依赖（torch / transformers），请执行："
                    'uv pip install -p .venv/Scripts/python.exe -e ".[local-embed]"',
                    code="embed_dependency_missing",
                ) from exc
            # 关掉 HF 的进度条与 INFO 噪音：它们是直接写 stderr 的非 JSON 行，
            # 会破坏日志的结构化采集（本项目日志统一输出 JSON）
            hf_logging.set_verbosity_error()
            hf_logging.disable_progress_bar()
            logger.info("加载本地向量模型: %s", path)
            self._tok = AutoTokenizer.from_pretrained(str(path))
            model = AutoModel.from_pretrained(str(path))
            model.eval()
            self._model = model
            logger.info("向量模型就绪 dim=%s", self.dim)

    def _encode(self, texts: list[str]) -> list[list[float]]:
        self._load()
        import torch

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            inputs = self._tok(
                batch,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=settings.embed_max_tokens,
            )
            with torch.no_grad():
                hidden = self._model(**inputs).last_hidden_state
            # CLS pooling：归一化后内积即余弦相似度，与 Chroma 的 cosine 空间一致
            cls = hidden[:, 0]
            vec = torch.nn.functional.normalize(cls, p=2, dim=-1)
            vectors.extend(vec.tolist())
        return vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._encode, list(texts))

    async def warmup(self) -> None:
        if self._model is None:
            await asyncio.to_thread(self._load)


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        if settings.embed_provider == "bge-m3":
            _embedder = LocalBgeEmbedder()
        elif settings.embed_provider == "openai" and settings.embed_api_key:
            _embedder = OpenAICompatEmbedder()
        else:
            _embedder = HashingEmbedder()
        logger.info("向量化实现: %s dim=%s", _embedder.name, _embedder.dim)
    return _embedder


def reset_embedder() -> None:
    """配置变更后重建（测试用）。"""
    global _embedder
    _embedder = None


def embedder_info() -> dict[str, Any]:
    emb = get_embedder()
    return {"provider": emb.name, "dim": emb.dim}
