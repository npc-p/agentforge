#!/bin/sh
# 容器启动前置：准备本地向量模型 + 演示数据。
#
# 设计原则是「宁可降级也不要起不来」：模型下载失败会自动切到 hashing，
# 而不是让整个容器退出——否则网络稍差的人就完全跑不起来。
set -e

MODEL_DIR="${EMBED_MODEL_PATH:-/app/data/models/bge-m3}"
PROVIDER="${EMBED_PROVIDER:-bge-m3}"

if [ "$PROVIDER" = "bge-m3" ] && [ ! -f "$MODEL_DIR/config.json" ]; then
    echo "[init] 未检测到本地向量模型（BGE-M3），开始下载（约 2.2 GB，通常 3~10 分钟）"
    echo "[init] 想跳过下载可用轻量模式：EMBED_PROVIDER=hashing（docker compose up 时加 -e）"
    if python scripts/fetch_model.py --dir "$MODEL_DIR"; then
        echo "[init] 向量模型就绪"
    else
        echo "[init] 下载失败，自动降级为 EMBED_PROVIDER=hashing：检索能跑，但语义召回变弱"
        export EMBED_PROVIDER=hashing
    fi
fi

# 演示数据：seed 本身幂等（按知识库名与文件名去重），重复启动不会产生重复数据
if [ "${SKIP_SEED:-0}" != "1" ]; then
    python scripts/seed.py || echo "[init] 演示数据初始化跳过"
fi

exec "$@"
