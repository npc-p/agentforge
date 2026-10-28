'use client';

import { useCallback, useEffect, useState } from 'react';
import Banner from '@/components/Banner';
import Badge from '@/components/Badge';
import Button from '@/components/Button';
import Card from '@/components/Card';
import LoadingDots from '@/components/LoadingDots';
import PageHeader from '@/components/PageHeader';
import StatCard from '@/components/StatCard';
import { apiGet } from '@/lib/api';
import { formatMs, formatNumber, toMessage } from '@/lib/format';
import type { HealthInfo, OverviewInfo, SystemConfig } from '@/lib/types';

interface LightProps {
  label: string;
  ok: boolean;
  value: string;
  hint?: string;
}

function Light({ label, ok, value, hint }: LightProps) {
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-line bg-[#0d131a] px-3 py-2.5">
      <span className="relative mt-1 flex h-2.5 w-2.5 shrink-0">
        <span
          className={`absolute inset-0 rounded-full ${ok ? 'bg-ok' : 'bg-bad'}`}
          style={{ boxShadow: ok ? '0 0 8px rgba(63,185,80,0.65)' : '0 0 8px rgba(248,81,73,0.6)' }}
        />
      </span>
      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-[12px] text-fg">{label}</span>
          <Badge tone={ok ? 'ok' : 'bad'}>{ok ? '正常' : '异常'}</Badge>
        </div>
        <div className="mt-0.5 truncate font-mono text-[11px] text-muted">{value}</div>
        {hint && <div className="mt-0.5 truncate text-[10px] text-muted/70">{hint}</div>}
      </div>
    </div>
  );
}

export default function OverviewPage() {
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [overview, setOverview] = useState<OverviewInfo | null>(null);
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string>('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const results = await Promise.allSettled([
      apiGet<HealthInfo>('/system/health'),
      apiGet<OverviewInfo>('/system/overview'),
      apiGet<SystemConfig>('/system/config'),
    ]);

    const [healthRes, overviewRes, configRes] = results;
    if (healthRes.status === 'fulfilled') setHealth(healthRes.value);
    else setHealth(null);
    if (overviewRes.status === 'fulfilled') setOverview(overviewRes.value);
    else setOverview(null);
    if (configRes.status === 'fulfilled') setConfig(configRes.value);
    else setConfig(null);

    const failed = results.find((item) => item.status === 'rejected');
    if (failed && failed.status === 'rejected') {
      setError(toMessage(failed.reason, '加载概览数据失败'));
    }
    setUpdatedAt(new Date().toLocaleTimeString('zh-CN'));
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const mockMode = health?.llm.mock ?? config?.llm.mock_mode ?? overview?.llm_mock_mode ?? false;

  return (
    <div className="mx-auto max-w-[1180px] space-y-4">
      <PageHeader
        title="概览"
        description="平台运行状态、数据规模与模型配置一览。数据来源于 /api/system/*。"
        actions={
          <>
            {updatedAt && <span className="text-[11px] text-muted">更新于 {updatedAt}</span>}
            <Button variant="secondary" size="sm" onClick={() => void load()} loading={loading}>
              刷新
            </Button>
          </>
        }
      />

      {error && <Banner tone="error" onClose={() => setError(null)}>{error}</Banner>}

      {mockMode && (
        <Banner tone="warn">
          当前运行在 <span className="font-semibold">Mock 模式</span>
          ：未配置真实 LLM Key，对话与评测结果由本地模拟器生成，仅用于链路验证。
        </Banner>
      )}

      {loading && !health && !overview && (
        <Card>
          <LoadingDots label="正在拉取系统状态…" />
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card
          className="lg:col-span-2"
          title="系统健康"
          subtitle="数据库、向量库与模型服务连通性"
          actions={
            health && (
              <Badge tone={health.status === 'ok' ? 'ok' : 'warn'} dot>
                {health.status === 'ok' ? '全部正常' : '降级运行'}
              </Badge>
            )
          }
        >
          {health ? (
            <div className="grid gap-2.5 sm:grid-cols-3">
              <Light
                label="数据库"
                ok={health.database?.ok ?? false}
                value={health.database?.dialect ?? 'unknown'}
              />
              <Light
                label="向量库"
                ok={health.vector_store?.ok ?? false}
                value={health.vector_store?.backend ?? 'unknown'}
                hint={health.vector_store?.error ?? health.vector_store?.path}
              />
              <Light
                label="模型服务"
                ok={health.llm?.ready ?? false}
                value={`${health.llm?.provider ?? '-'} / ${health.llm?.model ?? '-'}`}
                hint={`Embedder ${health.embedder?.provider ?? '-'} · ${health.embedder?.dim ?? '-'} 维`}
              />
            </div>
          ) : (
            <div className="text-[12px] text-muted">健康检查不可用，请确认后端与数据库状态。</div>
          )}
        </Card>

        <Card title="模型与检索配置" subtitle="只读展示，修改请调整后端环境变量">
          {config ? (
            <dl className="space-y-2 text-[12px]">
              <div className="flex items-center justify-between gap-2 border-b border-line-soft pb-1.5">
                <dt className="text-muted">LLM</dt>
                <dd className="truncate font-mono text-[11px] text-fg">
                  {config.llm.provider} / {config.llm.model}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-2 border-b border-line-soft pb-1.5">
                <dt className="text-muted">运行模式</dt>
                <dd>
                  <Badge tone={mockMode ? 'warn' : 'ok'}>{mockMode ? 'Mock' : '在线'}</Badge>
                </dd>
              </div>
              <div className="flex items-center justify-between gap-2 border-b border-line-soft pb-1.5">
                <dt className="text-muted">最大步数</dt>
                <dd className="font-mono text-[11px]">{formatNumber(config.llm.max_steps)}</dd>
              </div>
              <div className="flex items-center justify-between gap-2 border-b border-line-soft pb-1.5">
                <dt className="text-muted">向量库</dt>
                <dd className="font-mono text-[11px]">{config.vector_store.backend}</dd>
              </div>
              <div className="flex items-center justify-between gap-2">
                <dt className="text-muted">TopK</dt>
                <dd className="font-mono text-[11px]">{formatNumber(config.rag.top_k)}</dd>
              </div>
            </dl>
          ) : (
            <div className="text-[12px] text-muted">配置接口不可用。</div>
          )}
        </Card>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="会话总数" value={formatNumber(overview?.conversations)} hint="conversations" />
        <StatCard label="消息总数" value={formatNumber(overview?.messages)} hint="messages" />
        <StatCard
          label="Agent 运行"
          value={formatNumber(overview?.agent_runs)}
          hint="agent_runs"
          accent="accent"
        />
        <StatCard
          label="含工具运行"
          value={formatNumber(overview?.runs_with_tools)}
          hint="runs_with_tools"
          accent="accent"
        />
        <StatCard label="知识库" value={formatNumber(overview?.knowledge_bases)} hint="knowledge_bases" />
        <StatCard label="文档" value={formatNumber(overview?.documents)} hint="documents" />
        <StatCard label="切片" value={formatNumber(overview?.chunks)} hint="chunks" />
        <StatCard
          label="评测次数"
          value={formatNumber(overview?.eval_runs)}
          hint="eval_runs"
          accent="ok"
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card title="平均运行耗时" subtitle="所有已完成 Agent 运行的平均时延">
          <div className="flex items-end gap-3">
            <span className="font-mono text-[30px] font-semibold leading-none text-accent">
              {formatMs(overview?.avg_run_latency_ms)}
            </span>
            <span className="pb-1 text-[11px] text-muted">avg_run_latency_ms</span>
          </div>
          <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-panel-soft">
            <div
              className="h-full rounded-full bg-accent/80"
              style={{
                width: `${Math.min(100, Math.max(2, ((overview?.avg_run_latency_ms ?? 0) / 10000) * 100))}%`,
              }}
            />
          </div>
          <div className="mt-1.5 text-[10px] text-muted/70">进度条上限按 10s 归一化</div>
        </Card>

        <Card title="RAG 参数" subtitle="切片与混合检索权重（向量 + BM25）">
          {config ? (
            <div className="grid grid-cols-2 gap-2.5 text-[12px]">
              <div className="rounded-lg border border-line bg-[#0d131a] px-3 py-2">
                <div className="text-[10px] text-muted">切片大小</div>
                <div className="mt-0.5 font-mono text-[13px]">{config.rag.chunk_size}</div>
              </div>
              <div className="rounded-lg border border-line bg-[#0d131a] px-3 py-2">
                <div className="text-[10px] text-muted">切片重叠</div>
                <div className="mt-0.5 font-mono text-[13px]">{config.rag.chunk_overlap ?? '-'}</div>
              </div>
              <div className="rounded-lg border border-line bg-[#0d131a] px-3 py-2">
                <div className="text-[10px] text-muted">向量权重</div>
                <div className="mt-0.5 font-mono text-[13px] text-accent">
                  {config.rag.vector_weight}
                </div>
              </div>
              <div className="rounded-lg border border-line bg-[#0d131a] px-3 py-2">
                <div className="text-[10px] text-muted">BM25 权重</div>
                <div className="mt-0.5 font-mono text-[13px] text-ok">{config.rag.bm25_weight}</div>
              </div>
              <div className="col-span-2 flex gap-2">
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-panel-soft">
                  <div
                    className="h-full bg-accent"
                    style={{ width: `${(config.rag.vector_weight / (config.rag.vector_weight + config.rag.bm25_weight || 1)) * 100}%` }}
                  />
                </div>
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-panel-soft">
                  <div
                    className="h-full bg-ok"
                    style={{ width: `${(config.rag.bm25_weight / (config.rag.vector_weight + config.rag.bm25_weight || 1)) * 100}%` }}
                  />
                </div>
              </div>
            </div>
          ) : (
            <div className="text-[12px] text-muted">配置接口不可用。</div>
          )}
        </Card>
      </div>

      {overview && (
        <div className="flex flex-wrap gap-x-5 gap-y-1 px-1 text-[11px] text-muted">
          <span>数据集 {formatNumber(overview.datasets)}</span>
          <span>样本 {formatNumber(overview.dataset_items)}</span>
          <span>工具 {formatNumber(overview.tools)}</span>
          <span>llm_mock_mode: {String(overview.llm_mock_mode)}</span>
        </div>
      )}
    </div>
  );
}
