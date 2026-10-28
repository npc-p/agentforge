# AgentForge 后端镜像
#
# 两个刻意的取舍：
# 1. torch 走 CPU 专用 index —— PyPI 上的 Linux 版默认带 CUDA，会把镜像撑到 3 GB+，
#    而本项目只用 CPU 推理；
# 2. 依赖层先于源码层拷贝 —— 改业务代码时不必重装 100+ 个包。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl 用于容器健康检查与模型下载的连通性判断
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# ---- 依赖层 ----
COPY pyproject.toml ./
RUN pip install --no-cache-dir uv \
    && uv pip install --system -r pyproject.toml \
    && pip uninstall -y uv

# 本地向量化依赖（体积大，单独一层便于缓存）。
# WITH_LOCAL_EMBED=0 可构建轻量镜像，配合 EMBED_PROVIDER=hashing 使用。
#
# 这里必须用 `--no-deps` 装 torch：pytorch 的 CPU index 会同时代理 sympy、networkx
# 这类依赖，而它们的下载只有 10~20 kB/s（实测），能把构建拖到十几分钟。
# torch 本体走该 index 有 3.9 MB/s，所以只让本体走它，依赖留给 PyPI。
ARG WITH_LOCAL_EMBED=1
RUN if [ "$WITH_LOCAL_EMBED" = "1" ]; then \
        pip install --no-cache-dir --no-deps \
                --index-url https://download.pytorch.org/whl/cpu torch \
        && pip install --no-cache-dir sympy networkx jinja2 setuptools \
        && pip install --no-cache-dir transformers ; \
    fi

# ---- 源码层 ----
COPY app ./app
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh && mkdir -p data/models data/chroma data/uploads data/exports

EXPOSE 8010

# 健康检查：/api/system/health 会如实报告数据库与向量库状态
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=6 \
    CMD curl -fsS http://127.0.0.1:8010/api/system/health || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
