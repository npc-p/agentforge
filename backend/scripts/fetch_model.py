"""下载本地向量模型 BGE-M3 到 `data/models/bge-m3`。

为什么单独写脚本：默认配置用本地 BGE-M3，新环境克隆仓库后若没下模型，
首次检索会报 `embed_model_missing`。手工 curl 十几个文件容易漏、也不好断点续传。

特性：
- 默认走 ModelScope（国内可达性明显好于 HuggingFace），可用 `--mirror hf` 切换；
- 已存在且大小一致的文件直接跳过，可重复执行；
- 断点续传（`Range` 请求），中断后重跑不会从头下 2.2 GB；
- 不带第三方依赖，只用 httpx（项目已依赖）。

用法：
    .venv/Scripts/python.exe scripts/fetch_model.py
    .venv/Scripts/python.exe scripts/fetch_model.py --mirror hf --dir data/models/bge-m3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

FILES = [
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
    "modules.json",
    "sentence_bert_config.json",
    "config_sentence_transformers.json",
    "1_Pooling/config.json",
    "pytorch_model.bin",  # 约 2.2 GB，放最后下，前面的小文件先就位
]

MIRRORS = {
    "modelscope": "https://modelscope.cn/models/BAAI/bge-m3/resolve/master",
    "hf": "https://huggingface.co/BAAI/bge-m3/resolve/main",
}


def _remote_size(client: httpx.Client, url: str) -> int:
    """探测远端文件大小。

    ModelScope 的 CDN 对 HEAD 常常不返回 content-length，此时用 1 字节的流式
    Range 请求读 `Content-Range: bytes 0-0/总长`。必须用 stream 而不是 get：
    万一服务器忽略 Range，`get` 会把 2.2 GB 全读进内存。
    """
    resp = client.head(url, follow_redirects=True, timeout=30)
    size = int(resp.headers.get("content-length") or 0)
    if size:
        return size

    with client.stream(
        "GET", url, headers={"Range": "bytes=0-0"}, follow_redirects=True, timeout=30
    ) as probe:
        content_range = probe.headers.get("content-range") or ""
    if "/" in content_range:
        try:
            return int(content_range.rsplit("/", 1)[1])
        except ValueError:
            return 0
    return 0


def _download(client: httpx.Client, url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    start = dest.stat().st_size if dest.exists() else 0
    total = _remote_size(client, url)

    if total and start >= total:
        print("  跳过（已完整 %.1f MB）" % (start / 1048576))
        return

    headers = {"Range": f"bytes={start}-"} if start > 0 else {}
    mode = "ab" if start > 0 else "wb"
    if start > 0:
        print("  断点续传：已有 %.1f MB / %.1f MB" % (start / 1048576, total / 1048576))

    with client.stream("GET", url, headers=headers, follow_redirects=True, timeout=120) as resp:
        if resp.status_code == 416:
            # 请求的起点已越过文件尾 —— 说明本地这份就是完整的
            print("  跳过（服务器判定已完整）")
            return
        resp.raise_for_status()
        written = start
        last_mark = -1
        with dest.open(mode) as fh:
            for chunk in resp.iter_bytes(1 << 20):
                fh.write(chunk)
                written += len(chunk)
                if total:
                    pct = int(written * 100 / total)
                    if pct >= last_mark + 5:  # 每 5% 打一次，避免刷屏
                        last_mark = pct
                        print("  %3d%%  %.1f/%.1f MB" % (pct, written / 1048576, total / 1048576))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mirror", default="modelscope", choices=sorted(MIRRORS))
    ap.add_argument("--dir", default="data/models/bge-m3")
    args = ap.parse_args()

    base = MIRRORS[args.mirror].rstrip("/")
    root = Path(args.dir)
    root.mkdir(parents=True, exist_ok=True)

    print("模型: BAAI/bge-m3 | 镜像: %s | 目标目录: %s" % (args.mirror, root))
    with httpx.Client(headers={"User-Agent": "agentforge-fetch/1.0"}) as client:
        for name in FILES:
            url = "%s/%s" % (base, name)
            print("[%s]" % name)
            try:
                _download(client, url, root / name)
            except Exception as exc:  # noqa: BLE001
                print("  失败: %s" % exc)
                print("\n下载中断。重跑同一命令会从断点继续。")
                sys.exit(1)

    missing = [f for f in FILES if not (root / f).exists()]
    if missing:
        print("\n仍有缺失文件: %s" % missing)
        sys.exit(1)
    size = sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) / 1048576
    print("\n完成，共 %.0f MB。可直接启动服务（EMBED_PROVIDER=bge-m3）。" % size)


if __name__ == "__main__":
    main()
