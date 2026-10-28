"""AgentForge 全局配置。

所有配置项均可通过 `.env` 或环境变量覆盖，命名规则为大写字段名。
相对路径统一以 `backend/` 目录为基准解析，避免不同工作目录下行为漂移。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 应用 ----------
    app_name: str = "AgentForge"
    app_version: str = "0.1.0"
    env: Literal["dev", "test", "prod"] = "dev"
    debug: bool = True
    api_prefix: str = "/api"
    # 逗号分隔存储：复杂类型字段在 pydantic-settings 中会被提前 JSON 解码，
    # 用字符串承载再在属性里解析，可避免不同版本行为差异导致的启动失败。
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ---------- 日志 ----------
    # json：每行一个 JSON，便于按字段筛选统计；text：本地临时排障的可读格式
    log_format: Literal["json", "text"] = "json"
    # 脱敏开关默认开启：宁可日志少一点信息，也不能把用户隐私落到磁盘
    log_mask: bool = True

    # ---------- 数据库 ----------
    database_url: str = "mysql+aiomysql://root:root@127.0.0.1:3306/agentforge?charset=utf8mb4"
    # SQL 工具专用只读连接：留空则回退到 database_url（此时仅靠表名白名单防护）
    readonly_database_url: str = ""
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ---------- LLM ----------
    llm_provider: Literal["deepseek", "mock"] = "deepseek"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    llm_timeout: float = 60.0
    llm_max_retries: int = 2
    llm_temperature: float = 0.3
    llm_max_tokens: int = 2048
    llm_circuit_threshold: int = 5
    llm_circuit_cooldown: float = 30.0

    # ---------- 向量化 ----------
    embed_provider: Literal["hashing", "openai", "bge-m3"] = "hashing"
    embed_model: str = "text-embedding-3-small"
    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_dim: int = 512
    # 本地模型（embed_provider=bge-m3）的权重目录，相对路径以 backend/ 为基准
    embed_model_path: str = "./data/models/bge-m3"
    # CPU 推理的批大小：太大内存峰值高，太小吞吐低；8 在 16G 内存机器上较稳
    embed_batch_size: int = 8
    embed_max_tokens: int = 512

    # ---------- 向量库 ----------
    # 当前仅实现 chroma。VectorStore 协议已抽象，新增后端只需加一个实现类 + 注册，
    # 业务代码零改动；此处故意只暴露已实现选项，避免配置了却不可用的静默失败。
    vector_store: Literal["chroma"] = "chroma"
    chroma_dir: str = "./data/chroma"
    chroma_collection_prefix: str = "af_kb"

    # ---------- Agent ----------
    agent_max_steps: int = 8
    agent_max_plan_tasks: int = 6
    agent_tool_timeout: float = 20.0
    agent_tool_max_retries: int = 1

    # ---------- RAG ----------
    rag_chunk_size: int = 600
    rag_chunk_overlap: int = 80
    rag_top_k: int = 6
    rag_vector_weight: float = 0.6
    rag_bm25_weight: float = 0.4

    # ---------- 文件 ----------
    upload_dir: str = "./data/uploads"
    export_dir: str = "./data/exports"
    max_upload_mb: int = 20

    @property
    def cors_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def readonly_url(self) -> str:
        """SQL 工具使用的连接串；未单独配置时回退到主库（仅剩应用层防护）。"""
        return self.readonly_database_url.strip() or self.database_url

    @property
    def readonly_isolated(self) -> bool:
        """是否已配置独立的只读账号。健康检查与日志据此提示防护等级。"""
        return bool(self.readonly_database_url.strip())

    # ---------- 派生属性 ----------
    def _abs(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else (BACKEND_ROOT / path).resolve()

    @property
    def chroma_path(self) -> Path:
        return self._abs(self.chroma_dir)

    @property
    def upload_path(self) -> Path:
        return self._abs(self.upload_dir)

    @property
    def export_path(self) -> Path:
        return self._abs(self.export_dir)

    @property
    def embed_model_dir(self) -> Path:
        return self._abs(self.embed_model_path)

    @property
    def llm_ready(self) -> bool:
        """真实模型是否可用；未配置 Key 时自动降级到 mock，保证系统可离线运行。"""
        if self.llm_provider == "mock":
            return False
        return bool(self.deepseek_api_key)

    def ensure_dirs(self) -> None:
        for path in (self.chroma_path, self.upload_path, self.export_path):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
