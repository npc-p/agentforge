'use client';

import { useCallback, useEffect, useState } from 'react';
import Badge, { statusTone } from '@/components/Badge';
import Banner from '@/components/Banner';
import Button from '@/components/Button';
import Card from '@/components/Card';
import EmptyState from '@/components/EmptyState';
import LoadingDots from '@/components/LoadingDots';
import PageHeader from '@/components/PageHeader';
import { apiGet, apiPost } from '@/lib/api';
import { cn } from '@/lib/cn';
import { formatBytes, formatNumber, formatPercent, formatTime, isRecord, toMessage } from '@/lib/format';
import { inputCls, labelCls, tableCls, tableWrapCls, tdCls, thCls, textareaCls } from '@/lib/ui';
import type {
  Dataset,
  DatasetItem,
  DatasetVersion,
  ExportResult,
  PipelineResult,
  PipelineStats,
  QcRecord,
} from '@/lib/types';

const SAMPLE_PLACEHOLDER = `[
  {
    "instruction": "如何重置管理员密码？",
    "input": "",
    "output": "进入后台「用户管理」，选择管理员账号后点击重置密码，系统会发送重置链接到绑定邮箱。"
  },
  {
    "instruction": "把下面这句话翻译成英文",
    "input": "今天的天气非常好",
    "output": "The weather is really nice today."
  }
]`;

const EXPORT_FORMATS: Array<{ value: 'chat_jsonl' | 'sharegpt' | 'alpaca'; label: string }> = [
  { value: 'chat_jsonl', label: 'chat_jsonl' },
  { value: 'sharegpt', label: 'sharegpt' },
  { value: 'alpaca', label: 'alpaca' },
];

function itemText(item: DatasetItem): string {
  const source = item.cleaned ?? item.raw;
  if (!isRecord(source)) return '(空)';
  const instruction = source.instruction ?? source.prompt ?? source.question;
  const input = source.input ?? source.context;
  const parts: string[] = [];
  if (typeof instruction === 'string') parts.push(instruction);
  if (typeof input === 'string' && input.trim()) parts.push(`补充：${input}`);
  return parts.join(' · ') || JSON.stringify(source).slice(0, 120);
}

function StatusBars({ stats }: { stats: PipelineStats }) {
  const entries = Object.entries(stats.by_status ?? {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, value]) => value));
  const colorOf = (status: string) => {
    const tone = statusTone(status);
    if (tone === 'ok') return 'bg-ok';
    if (tone === 'bad') return 'bg-bad';
    if (tone === 'warn') return 'bg-warn';
    if (tone === 'info') return 'bg-accent';
    return 'bg-muted';
  };

  if (entries.length === 0) {
    return <div className="text-[11px] text-muted">暂无状态分布数据，先导入样本并运行流水线。</div>;
  }

  return (
    <div className="space-y-2">
      {entries.map(([status, count]) => (
        <div key={status} className="flex items-center gap-2">
          <span className="w-16 shrink-0 text-[11px] text-muted">{status}</span>
          <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-panel-soft">
            <div
              className={cn('h-full rounded-full transition-all', colorOf(status))}
              style={{ width: `${Math.max(2, (count / max) * 100)}%` }}
            />
          </div>
          <span className="w-12 shrink-0 text-right font-mono text-[11px] text-fg/80">
            {formatNumber(count)}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function DatasetPage() {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');

  const [importText, setImportText] = useState('');
  const [items, setItems] = useState<DatasetItem[]>([]);
  const [itemsTotal, setItemsTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [itemsLoading, setItemsLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState('');

  const [stats, setStats] = useState<PipelineStats | null>(null);
  const [pipeline, setPipeline] = useState<PipelineResult | null>(null);
  const [qcRecords, setQcRecords] = useState<QcRecord[]>([]);
  const [versions, setVersions] = useState<DatasetVersion[]>([]);
  const [exportResult, setExportResult] = useState<ExportResult | null>(null);

  const size = 10;

  const loadDatasets = useCallback(async (keepSelection = true) => {
    setListLoading(true);
    try {
      const data = await apiGet<Dataset[]>('/datasets');
      const list = Array.isArray(data) ? data : [];
      setDatasets(list);
      setSelectedId((prev) => {
        if (keepSelection && prev !== null && list.some((item) => item.id === prev)) return prev;
        return list.length > 0 ? list[0].id : null;
      });
    } catch (err) {
      setError(toMessage(err, '加载数据集失败'));
    } finally {
      setListLoading(false);
    }
  }, []);

  const loadItems = useCallback(async (datasetId: number, nextPage: number, status: string) => {
    setItemsLoading(true);
    try {
      const query = new URLSearchParams({
        page: String(nextPage),
        size: String(size),
      });
      if (status) query.set('status', status);
      const data = await apiGet<{ items: DatasetItem[]; total: number }>(
        `/datasets/${datasetId}/items?${query.toString()}`,
      );
      setItems(Array.isArray(data?.items) ? data.items : []);
      setItemsTotal(typeof data?.total === 'number' ? data.total : 0);
    } catch (err) {
      setItems([]);
      setItemsTotal(0);
      setError(toMessage(err, '加载样本失败'));
    } finally {
      setItemsLoading(false);
    }
  }, []);

  const loadStats = useCallback(async (datasetId: number) => {
    try {
      const data = await apiGet<PipelineStats>(`/datasets/${datasetId}/stats`);
      setStats(data);
    } catch {
      setStats(null);
    }
  }, []);

  const loadQc = useCallback(async (datasetId: number) => {
    try {
      const data = await apiGet<QcRecord[]>(`/datasets/${datasetId}/qc?limit=100`);
      setQcRecords(Array.isArray(data) ? data : []);
    } catch {
      setQcRecords([]);
    }
  }, []);

  const loadVersions = useCallback(async (datasetId: number) => {
    try {
      const data = await apiGet<DatasetVersion[]>(`/datasets/${datasetId}/versions`);
      setVersions(Array.isArray(data) ? data : []);
    } catch {
      setVersions([]);
    }
  }, []);

  useEffect(() => {
    void loadDatasets(false);
  }, [loadDatasets]);

  useEffect(() => {
    if (selectedId === null) {
      setItems([]);
      setStats(null);
      return;
    }
    setPage(1);
    setStatusFilter('');
    setPipeline(null);
    setExportResult(null);
    void loadItems(selectedId, 1, '');
    void loadStats(selectedId);
    void loadQc(selectedId);
    void loadVersions(selectedId);
  }, [selectedId, loadItems, loadStats, loadQc, loadVersions]);

  const createDataset = async () => {
    if (!newName.trim()) {
      setError('请填写数据集名称');
      return;
    }
    setBusy('create');
    setError(null);
    setNotice(null);
    try {
      const created = await apiPost<{ id: number; name: string }>('/datasets', {
        name: newName.trim(),
        task_type: 'sft',
        description: newDesc.trim(),
      });
      setNewName('');
      setNewDesc('');
      setNotice(`数据集「${created.name}」创建成功`);
      await loadDatasets(false);
      setSelectedId(created.id);
    } catch (err) {
      setError(toMessage(err, '创建数据集失败'));
    } finally {
      setBusy(null);
    }
  };

  const importItems = async () => {
    if (selectedId === null) return;
    if (!importText.trim()) {
      setError('请粘贴 JSON 数组样本');
      return;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(importText) as unknown;
    } catch {
      setError('JSON 解析失败，请检查格式（必须是数组）');
      return;
    }
    if (!Array.isArray(parsed) || parsed.length === 0) {
      setError('样本必须是形如 [{instruction, input, output}] 的非空数组');
      return;
    }
    let normalized: Array<{ instruction: string; input: string; output: string }>;
    try {
      normalized = parsed.map((row, index) => {
        if (!isRecord(row)) throw new Error(`第 ${index + 1} 条样本不是对象`);
        const instruction = row.instruction;
        if (typeof instruction !== 'string' || instruction.trim() === '') {
          throw new Error(`第 ${index + 1} 条样本缺少 instruction 字段`);
        }
        return {
          instruction,
          input: typeof row.input === 'string' ? row.input : '',
          output: typeof row.output === 'string' ? row.output : '',
        };
      });
    } catch (err) {
      setError(toMessage(err, '样本格式不正确'));
      return;
    }

    setBusy('import');
    setError(null);
    setNotice(null);
    try {
      const result = await apiPost<{ added?: number; count?: number }>(
        `/datasets/${selectedId}/items`,
        { items: normalized, split: 'train' },
      );
      const count = result?.added ?? result?.count ?? normalized.length;
      setNotice(`已导入 ${count} 条样本`);
      setImportText('');
      await loadItems(selectedId, 1, statusFilter);
      await loadDatasets();
      await loadStats(selectedId);
    } catch (err) {
      setError(toMessage(err, '导入样本失败'));
    } finally {
      setBusy(null);
    }
  };

  const runPipeline = async () => {
    if (selectedId === null) return;
    setBusy('pipeline');
    setError(null);
    setNotice(null);
    try {
      const result = await apiPost<PipelineResult>(`/datasets/${selectedId}/pipeline`, { stages: null });
      setPipeline(result);
      if (result.stats) setStats(result.stats);
      else await loadStats(selectedId);
      setNotice('流水线执行完成：清洗 → 去重 → 打标 → 质检');
      await loadItems(selectedId, 1, statusFilter);
      await loadQc(selectedId);
      await loadDatasets();
    } catch (err) {
      setError(toMessage(err, '流水线执行失败'));
    } finally {
      setBusy(null);
    }
  };

  const exportDataset = async (format: 'chat_jsonl' | 'sharegpt' | 'alpaca') => {
    if (selectedId === null) return;
    setBusy(`export-${format}`);
    setError(null);
    setNotice(null);
    try {
      const result = await apiPost<ExportResult>(`/datasets/${selectedId}/export`, { format });
      setExportResult(result);
      setNotice(`已导出 ${format}：${result.count} 条`);
      await loadVersions(selectedId);
    } catch (err) {
      setError(toMessage(err, '导出失败'));
    } finally {
      setBusy(null);
    }
  };

  const changePage = (next: number) => {
    if (selectedId === null) return;
    setPage(next);
    void loadItems(selectedId, next, statusFilter);
  };

  const changeStatus = (status: string) => {
    if (selectedId === null) return;
    setStatusFilter(status);
    setPage(1);
    void loadItems(selectedId, 1, status);
  };

  const selected = datasets.find((item) => item.id === selectedId) ?? null;
  const pageCount = Math.max(1, Math.ceil(itemsTotal / size));

  return (
    <div className="mx-auto max-w-[1180px] space-y-4">
      <PageHeader
        title="数据集"
        description="SFT 样本从导入到清洗、去重、打标、质检与导出的完整链路。"
        actions={
          <Button size="sm" variant="secondary" onClick={() => void loadDatasets()} loading={listLoading}>
            刷新
          </Button>
        }
      />

      {error && <Banner tone="error" onClose={() => setError(null)}>{error}</Banner>}
      {notice && <Banner tone="success" onClose={() => setNotice(null)}>{notice}</Banner>}

      <Card title="新建数据集" subtitle={`任务类型固定为 sft（监督微调）`}>
        <div className="grid gap-3 md:grid-cols-[1fr_1.6fr_auto] md:items-end">
          <div>
            <label className={labelCls} htmlFor="ds-name">名称</label>
            <input
              id="ds-name"
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              placeholder="例如：客服问答 v1"
              className={inputCls}
            />
          </div>
          <div>
            <label className={labelCls} htmlFor="ds-desc">描述</label>
            <input
              id="ds-desc"
              value={newDesc}
              onChange={(event) => setNewDesc(event.target.value)}
              placeholder="可选"
              className={inputCls}
            />
          </div>
          <Button variant="primary" onClick={() => void createDataset()} loading={busy === 'create'}>
            创建
          </Button>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <Card title="数据集列表" dense className="h-fit">
          {listLoading ? (
            <LoadingDots className="px-1 py-3" label="加载中" />
          ) : datasets.length === 0 ? (
            <EmptyState compact title="还没有数据集" description="先在上方创建数据集。" />
          ) : (
            <ul className="space-y-1">
              {datasets.map((dataset) => (
                <li key={dataset.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(dataset.id)}
                    className={cn(
                      'w-full rounded-lg border px-2.5 py-2 text-left transition-colors',
                      dataset.id === selectedId
                        ? 'border-accent/40 bg-accent/10'
                        : 'border-transparent hover:border-line hover:bg-panel-soft',
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-[12px] text-fg">{dataset.name}</span>
                      <Badge tone={statusTone(dataset.status)}>{dataset.status}</Badge>
                    </div>
                    <div className="mt-1 flex gap-2 font-mono text-[10px] text-muted/80">
                      <span>{dataset.task_type}</span>
                      <span>{dataset.item_count} 条</span>
                      <span>合格 {dataset.passed_count}</span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className="space-y-4">
          {selected === null ? (
            <Card>
              <EmptyState title="请选择数据集" description="选择后可导入样本、运行流水线与导出。" />
            </Card>
          ) : (
            <>
              <Card
                title={`导入样本 · ${selected.name}`}
                subtitle="粘贴 JSON 数组，字段为 instruction / input / output"
                actions={
                  <Button variant="primary" size="sm" onClick={() => void importItems()} loading={busy === 'import'}>
                    导入
                  </Button>
                }
              >
                <textarea
                  value={importText}
                  onChange={(event) => setImportText(event.target.value)}
                  rows={7}
                  placeholder={SAMPLE_PLACEHOLDER}
                  className={textareaCls}
                />
                <div className="mt-2 flex items-center justify-between text-[10px] text-muted">
                  <span>提示：input 可为空字符串；导入后默认 split 为 train。</span>
                  <span className="font-mono">{importText.length} 字符</span>
                </div>
              </Card>

              <Card
                title="数据流水线"
                subtitle="清洗 → 去重 → 打标 → 质检，一次执行全部阶段"
                actions={
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() => void runPipeline()}
                    loading={busy === 'pipeline'}
                  >
                    运行全流水线
                  </Button>
                }
              >
                {pipeline ? (
                  <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
                    {pipeline.clean && (
                      <div className="rounded-lg border border-line bg-[#0d131a] px-3 py-2">
                        <div className="text-[10px] uppercase tracking-wide text-muted">清洗</div>
                        <div className="mt-1 font-mono text-[13px]">{formatNumber(pipeline.clean.total)} 条</div>
                        <div className="mt-0.5 text-[10px] text-muted">
                          变更 {pipeline.clean.changed} · 丢弃 {pipeline.clean.dropped}
                        </div>
                      </div>
                    )}
                    {pipeline.dedup && (
                      <div className="rounded-lg border border-line bg-[#0d131a] px-3 py-2">
                        <div className="text-[10px] uppercase tracking-wide text-muted">去重</div>
                        <div className="mt-1 font-mono text-[13px]">保留 {formatNumber(pipeline.dedup.kept)}</div>
                        <div className="mt-0.5 text-[10px] text-muted">重复 {pipeline.dedup.duplicates}</div>
                      </div>
                    )}
                    {pipeline.label && (
                      <div className="rounded-lg border border-line bg-[#0d131a] px-3 py-2">
                        <div className="text-[10px] uppercase tracking-wide text-muted">打标</div>
                        <div className="mt-1 font-mono text-[13px]">标注 {formatNumber(pipeline.label.labeled)}</div>
                        <div className="mt-0.5 text-[10px] text-muted">来源 {pipeline.label.source}</div>
                      </div>
                    )}
                    {pipeline.qc && (
                      <div className="rounded-lg border border-line bg-[#0d131a] px-3 py-2">
                        <div className="text-[10px] uppercase tracking-wide text-muted">质检</div>
                        <div className="mt-1 font-mono text-[13px] text-ok">
                          {formatPercent(pipeline.qc.pass_rate)}
                        </div>
                        <div className="mt-0.5 text-[10px] text-muted">
                          通过 {pipeline.qc.passed}/{pipeline.qc.total} · 均分 {pipeline.qc.avg_score.toFixed(2)}
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="text-[12px] text-muted">
                    尚未运行。点击右上角执行，将依次完成清洗、去重、打标与规则质检。
                  </div>
                )}
              </Card>

              <div className="grid gap-4 md:grid-cols-[1fr_1fr]">
                <Card title="状态分布" subtitle="按质检状态统计样本数量">
                  {stats ? (
                    <>
                      <div className="mb-3 flex flex-wrap gap-3 text-[11px] text-muted">
                        <span>总数 <span className="font-mono text-fg">{formatNumber(stats.total)}</span></span>
                        <span>通过率 <span className="font-mono text-ok">{formatPercent(stats.pass_rate)}</span></span>
                        <span>均分 <span className="font-mono text-fg">{Number(stats.avg_quality ?? 0).toFixed(2)}</span></span>
                      </div>
                      <StatusBars stats={stats} />
                      {Object.keys(stats.intent_distribution ?? {}).length > 0 && (
                        <div className="mt-3 flex flex-wrap gap-1.5">
                          {Object.entries(stats.intent_distribution).map(([intent, count]) => (
                            <Badge key={intent} tone="neutral">
                              {intent} · {count}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="text-[11px] text-muted">暂无统计数据</div>
                  )}
                </Card>

                <Card
                  title="导出"
                  subtitle="三种下游训练格式，导出后可在此查看版本"
                >
                  <div className="flex flex-wrap gap-2">
                    {EXPORT_FORMATS.map((format) => (
                      <Button
                        key={format.value}
                        size="sm"
                        variant={format.value === 'chat_jsonl' ? 'primary' : 'secondary'}
                        loading={busy === `export-${format.value}`}
                        onClick={() => void exportDataset(format.value)}
                      >
                        {format.label}
                      </Button>
                    ))}
                  </div>
                  {exportResult && (
                    <div className="mt-3 rounded-lg border border-line bg-[#0d131a] p-2.5 text-[11px]">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone="ok">v{exportResult.version}</Badge>
                        <span className="font-mono text-muted">{formatNumber(exportResult.count)} 条</span>
                        <span className="font-mono text-muted">{formatBytes(exportResult.size_bytes)}</span>
                      </div>
                      <div className="mt-1.5 break-all font-mono text-[10px] text-muted/80">
                        {exportResult.path}
                      </div>
                    </div>
                  )}

                  <div className="mt-3">
                    <div className="mb-1.5 text-[10px] uppercase tracking-wide text-muted">历史版本</div>
                    {versions.length === 0 ? (
                      <div className="text-[11px] text-muted">暂无导出版本</div>
                    ) : (
                      <ul className="max-h-[150px] space-y-1 overflow-y-auto">
                        {versions.map((version) => (
                          <li
                            key={version.version}
                            className="flex items-center justify-between gap-2 rounded-md border border-line-soft px-2 py-1 text-[11px]"
                          >
                            <span className="font-mono text-accent">v{version.version}</span>
                            <span className="font-mono text-muted">{version.item_count} 条</span>
                            <span className="font-mono text-muted">{formatPercent(version.pass_rate)}</span>
                            <span className="text-[10px] text-muted/70">{formatTime(version.created_at)}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </Card>
              </div>

              <Card
                title="样本列表"
                subtitle={`共 ${itemsTotal} 条`}
                actions={
                  <select
                    value={statusFilter}
                    onChange={(event) => changeStatus(event.target.value)}
                    className="h-7 rounded-md border border-line bg-[#0d131a] px-2 text-[11px] text-fg outline-none focus:border-accent/60"
                  >
                    <option value="">全部状态</option>
                    <option value="passed">passed</option>
                    <option value="rejected">rejected</option>
                    <option value="pending">pending</option>
                  </select>
                }
              >
                {itemsLoading ? (
                  <LoadingDots label="加载样本" />
                ) : items.length === 0 ? (
                  <EmptyState compact title="暂无样本" description="在上方粘贴 JSON 数组导入，或切换状态筛选。" />
                ) : (
                  <>
                    <div className={tableWrapCls}>
                      <table className={tableCls}>
                        <thead>
                          <tr>
                            <th className={thCls}>#</th>
                            <th className={thCls}>内容</th>
                            <th className={thCls}>状态</th>
                            <th className={thCls}>质检分</th>
                            <th className={thCls}>拒绝原因</th>
                          </tr>
                        </thead>
                        <tbody>
                          {items.map((item) => (
                            <tr key={item.id} className="hover:bg-panel-soft/40">
                              <td className={cn(tdCls, 'font-mono text-[10px] text-muted')}>{item.id}</td>
                              <td className={tdCls}>
                                <div className="max-w-[380px] truncate text-[12px]">{itemText(item)}</div>
                                <div className="mt-0.5 font-mono text-[10px] text-muted/70">{item.split}</div>
                              </td>
                              <td className={tdCls}>
                                <Badge tone={statusTone(item.status)}>{item.status}</Badge>
                              </td>
                              <td className={cn(tdCls, 'font-mono')}>
                                {item.quality_score === null || item.quality_score === undefined
                                  ? '-'
                                  : item.quality_score.toFixed(2)}
                              </td>
                              <td className={cn(tdCls, 'max-w-[200px] break-words text-[11px] text-bad/90')}>
                                {item.reject_reason ?? '-'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="mt-2 flex items-center justify-between text-[11px] text-muted">
                      <span className="font-mono">
                        第 {page} / {pageCount} 页
                      </span>
                      <div className="flex gap-1.5">
                        <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => changePage(page - 1)}>
                          上一页
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={page >= pageCount}
                          onClick={() => changePage(page + 1)}
                        >
                          下一页
                        </Button>
                      </div>
                    </div>
                  </>
                )}
              </Card>

              <Card title="质检记录" subtitle="规则级质检结果（最多 100 条）">
                {qcRecords.length === 0 ? (
                  <div className="text-[11px] text-muted">暂无质检记录，运行流水线后生成。</div>
                ) : (
                  <ul className="max-h-[260px] space-y-1.5 overflow-y-auto">
                    {qcRecords.map((record) => (
                      <li
                        key={record.id}
                        className="flex flex-wrap items-center gap-2 rounded-md border border-line-soft px-2 py-1.5"
                      >
                        <Badge tone={record.passed ? 'ok' : 'bad'}>{record.passed ? '通过' : '未通过'}</Badge>
                        <span className="text-[11px] text-fg/90">{record.rule}</span>
                        <Badge tone={record.severity === 'error' ? 'bad' : 'neutral'}>{record.severity}</Badge>
                        <span className="font-mono text-[10px] text-muted">item #{record.item_id}</span>
                        {record.detail && (
                          <span className="min-w-0 flex-1 truncate text-[10px] text-muted/80">{record.detail}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
