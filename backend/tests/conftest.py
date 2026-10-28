"""pytest 全局配置。

关键点：所有环境变量必须在导入 app 之前设置，因为 Settings 在模块导入期即被实例化。
测试使用独立数据库 agentforge_test 与独立 Chroma 目录，避免污染开发数据。
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote

import pymysql
import pytest

TEST_DB = "agentforge_test"
MYSQL_HOST = os.environ.get("TEST_MYSQL_HOST", "127.0.0.1")
MYSQL_USER = os.environ.get("TEST_MYSQL_USER", "root")

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _detect_mysql_password() -> str:
    """取测试用数据库口令，**不把口令写进仓库**。

    优先级：环境变量 → backend/.env 里的 DATABASE_URL → 空串。
    早先这里直接写了默认密码，仓库一公开等于把本地 MySQL 口令公布了。
    """
    from_env = os.environ.get("TEST_MYSQL_PASSWORD")
    if from_env:
        return from_env

    env_file = BACKEND_ROOT / ".env"
    if env_file.exists():
        pattern = re.compile(r"DATABASE_URL=mysql\+aiomysql://[^:]+:([^@]*)@")
        for line in env_file.read_text(encoding="utf-8").splitlines():
            found = pattern.match(line.strip())
            if found:
                return unquote(found.group(1))
    return ""


MYSQL_PASSWORD = _detect_mysql_password()
# 每次测试用独立临时目录，天然隔离且无需删除操作
TEST_CHROMA = Path(tempfile.mkdtemp(prefix="af_chroma_test_"))


def _bootstrap_database() -> None:
    conn = pymysql.connect(host=MYSQL_HOST, user=MYSQL_USER, password=MYSQL_PASSWORD, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        cur.execute(
            f"CREATE DATABASE {TEST_DB} DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
    conn.commit()
    conn.close()


_bootstrap_database()

os.environ["ENV"] = "test"
os.environ["DEBUG"] = "false"
os.environ["DATABASE_URL"] = (
    f"mysql+aiomysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:3306/{TEST_DB}?charset=utf8mb4"
)
# 必须显式清空：否则会读到 .env 里指向开发库的只读串，导致测试跨库查询
os.environ["READONLY_DATABASE_URL"] = ""
os.environ["LLM_PROVIDER"] = "mock"  # 确定性输出，测试不依赖外部模型
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["EMBED_PROVIDER"] = "hashing"
os.environ["CHROMA_DIR"] = str(TEST_CHROMA)
os.environ["AGENT_MAX_STEPS"] = "4"


@pytest.fixture(scope="session", autouse=True)
def _prepare_schema():
    import asyncio

    from app.db.session import create_all, dispose

    asyncio.run(create_all())
    yield
    asyncio.run(dispose())


@pytest.fixture(scope="session")
def app_client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture()
def sample_doc() -> str:
    return (
        "# 智能体平台说明\n\n"
        "AgentForge 采用显式状态机，包含任务规划、工具执行、结果校验与答案生成四个阶段。\n\n"
        "## 检索增强\n\n"
        "知识库采用混合检索，向量召回负责语义近似，BM25 负责精确术语。"
        "两路结果通过 RRF 融合，只依赖排名不依赖分数。\n\n"
        "## 数据流水线\n\n"
        "训练数据依次经过采集、清洗、标注与质检四个阶段，质检规则分为 error 与 warning 两级。"
    )
