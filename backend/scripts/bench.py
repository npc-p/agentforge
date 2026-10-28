"""并发压测脚本（零外部依赖，仅用 httpx + asyncio）。

为什么自己写：Windows 上 hey/wrk 需要额外安装或编译，而压测本身只需要
「固定并发 + 采样延迟」，标准库 + httpx 足够，还能直接复用到 CI。

用法：
    # 检索接口，20 并发共 400 请求
    .venv/Scripts/python.exe scripts/bench.py -c 20 -n 400

    # 自定义路径与请求体
    .venv/Scripts/python.exe scripts/bench.py -c 50 -n 1000 \
        --path /api/knowledge/search --body '{"query":"混合检索如何融合","kb_id":2,"top_k":5}'

指标口径：延迟为端到端（含服务端处理与传输），p95 用最近秩法。
注意：uvicorn 默认单 worker，本脚本测的是单进程上限，不是集群容量。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from typing import Any

import httpx

DEFAULT_BODY = {"query": "混合检索使用什么融合算法", "kb_id": 2, "top_k": 5}


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1)))))
    return round(ordered[idx], 2)


async def worker(
    client: httpx.AsyncClient,
    url: str,
    body: dict[str, Any] | None,
    sem: asyncio.Semaphore,
    results: list[dict[str, Any]],
) -> None:
    async with sem:
        started = time.perf_counter()
        try:
            if body is None:
                resp = await client.get(url)
            else:
                resp = await client.post(url, json=body)
            elapsed = (time.perf_counter() - started) * 1000
            payload = resp.json()
            ok = resp.status_code == 200 and payload.get("code") == 0
            results.append({"ok": ok, "ms": elapsed, "status": resp.status_code})
        except Exception as exc:  # noqa: BLE001
            elapsed = (time.perf_counter() - started) * 1000
            results.append({"ok": False, "ms": elapsed, "status": 0, "error": str(exc)[:120]})


async def run(base: str, path: str, body: dict[str, Any] | None, concurrency: int, total: int) -> dict[str, Any]:
    # 防御：Git Bash 可能把 "/health" 转成 Windows 路径，这里统一保证单一前导斜杠
    clean_path = "/" + path.lstrip("/")
    url = base.rstrip("/") + clean_path
    sem = asyncio.Semaphore(concurrency)
    results: list[dict[str, Any]] = []

    limits = httpx.Limits(max_connections=concurrency + 10, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(timeout=60.0, limits=limits) as client:
        started = time.perf_counter()
        await asyncio.gather(
            *(worker(client, url, body, sem, results) for _ in range(total))
        )
        wall = time.perf_counter() - started

    latencies = [r["ms"] for r in results]
    ok_count = sum(1 for r in results if r["ok"])
    errors = [r for r in results if not r["ok"]]

    return {
        "target": url,
        "concurrency": concurrency,
        "requests": total,
        "duration_s": round(wall, 2),
        "rps": round(total / wall, 1) if wall else 0.0,
        "success": ok_count,
        "failed": len(errors),
        "error_rate": round(len(errors) / total, 4) if total else 0.0,
        "latency_ms": {
            "avg": round(statistics.fmean(latencies), 2) if latencies else 0.0,
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "p99": percentile(latencies, 99),
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
        "sample_errors": [e.get("error") or f"HTTP {e.get('status')}" for e in errors[:3]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentForge 接口压测")
    parser.add_argument("-u", "--base", default="http://127.0.0.1:8010", help="服务地址")
    parser.add_argument("-p", "--path", default="/api/knowledge/search", help="请求路径")
    parser.add_argument("-c", "--concurrency", type=int, default=20, help="并发数")
    parser.add_argument("-n", "--requests", type=int, default=400, help="总请求数")
    parser.add_argument("--method", choices=["GET", "POST"], default="POST")
    parser.add_argument("--body", default=json.dumps(DEFAULT_BODY, ensure_ascii=False), help="POST 请求体 JSON")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args()

    body = None if args.method == "GET" else json.loads(args.body)
    result = asyncio.run(
        run(args.base, args.path, body, args.concurrency, args.requests)
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    lat = result["latency_ms"]
    print(f"\n目标      {result['target']}")
    print(f"并发/请求 {result['concurrency']} / {result['requests']}")
    print(f"总耗时    {result['duration_s']}s   吞吐 {result['rps']} req/s")
    print(f"成功率    {result['success']}/{result['requests']}   错误率 {result['error_rate'] * 100:.2f}%")
    print(f"延迟(ms)  avg {lat['avg']}  p50 {lat['p50']}  p95 {lat['p95']}  p99 {lat['p99']}  max {lat['max']}")
    if result["sample_errors"]:
        print(f"错误样例  {result['sample_errors']}")


if __name__ == "__main__":
    main()
