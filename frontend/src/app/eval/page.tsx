'use client';

import { useCallback, useEffect, useState } from 'react';
import Badge, { statusTone } from '@/components/Badge';
import Banner from '@/components/Banner';
import Button from '@/components/Button';
import Card from '@/components/Card';
import EmptyState from '@/components/EmptyState';
import JsonView from '@/components/JsonView';
import LoadingDots from '@/components/LoadingDots';
import PageHeader from '@/components/PageHeader';
import { apiGet, apiPost } from '@/lib/api';
import { cn } from '@/lib/cn';
import { formatMs, formatNumber, formatPercent, formatTime, isRecord, toMessage } from '@/lib/format';
import { inputCls, labelCls, monoCls, tableCls, tableWrapCls, tdCls, thCls, textareaCls } from '@/lib/ui';
import type {
  EvalCase,
  EvalReport,
  EvalRunBrief,
  EvalRunResult,
  EvalSuite,
  KnowledgeBase,
} from '@/lib/types';

const CASES_PLACEHOLDER = `如何重置管理员密码？||重置|密码|tool_search_knowledge
帮我查一下订单 A10086 的物流||物流|已发货
把这句话翻译成英文：今天天气不错||weather`;

function Metric({ label, value, tone }: { label: string; value: string; tone: 'accent' | 'ok' | 'fg' }) {
  const color = tone === 'accent' ? 'text-accent' : tone === 'ok' ? 'text-ok' : 'text-fg';
  return (
    <div className="rounded-lg border border-line bg-[#0d131a] px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className={cn('mt-1 font-mono text-[22px] font-semibold leading-none', color)}>{value}</div>
    </div>
  );
}

export default function EvalPage() {
  const [suites, setSuites] = useState<EvalSuite[]>([]);
  const [suitesLoading, setSuitesLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [cases, setCases] = useState<EvalCase[]>([]);
  const [casesLoading, setCasesLoading] = useState(false);
  const [runs, setRuns] = useState<EvalRunBrief[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [report, setReport] = useState<EvalReport | null>(null);
  const [lastRun, setLastRun] = useState<EvalRunResult | null>(null);
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [kbId, setKbId] = useState('');
  const [topK, setTopK] = useState('5');
  const [caseText, setCaseText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [newName, setNewName] = useState('');
  const [newType, setNewType] = useState<'retrieval' | 'agent'>('agent');
  const [newDesc, setNewDesc] = useState('');

  const loadSuites = useCallback(async (keepSelection = true) => {
    setSuitesLoading(true);
    try {
      const data = await apiGet<EvalSuite[]>('/eval/suites');
      const list = Array.isArray(data) ? data : [];
      setSuites(list);
      setSelectedId((prev) => {
        if (keepSelection && prev !== null && list.some((item) => item.id === prev)) return prev;
        return list.length > 0 ? list[0].id : null;
      });
    } catch (err) {
      setError(toMessage(err, '加载评测套件失败'));
    } finally {
      setSuitesLoading(false);
    }
  }, []);

  const loadCases = useCallback(async (suiteId: number) => {
    setCasesLoading(true);
    try {
      const data = await apiGet<EvalCase[]>(`/eval/suites/${suiteId}/cases`);
      setCases(Array.isArray(data) ? data : []);
    } catch (err) {
      setCases([]);
      setError(toMessage(err, '加载用例失败'));
    } finally {
      setCasesLoading(false);
    }
  }, []);

  const loadRuns = useCallback(async (suiteId: number) => {
    setRunsLoading(true);
    try {
      const data = await apiGet<{ items: EvalRunBrief[]; total: number }>(
        `/eval/runs?suite_id=${suiteId}&page=1`,
      );
      setRuns(Array.isArray(data?.items) ? data.items : []);
    } catch {
      setRuns([]);
    } finally {
      setRunsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSuites(false);
    apiGet<KnowledgeBase[]>('/knowledge/bases')
      .then((data) => setKbs(Array.isArray(data) ? data : []))
      .catch(() => setKbs([]));
  }, [loadSuites]);

  useEffect(() => {
    if (selectedId === null) {
      setCases([]);
      setRuns([]);
      return;
    }
    setReport(null);
    setLastRun(null);
    void loadCases(selectedId);
    void loadRuns(selectedId);
  }, [selectedId, loadCases, loadRuns]);

  const createSuite = async () => {
    if (!newName.trim()) {
      setError('请填写套件名称');
      return;
    }
    setBusy('create');
    setError(null);
    setNotice(null);
    try {
      const created = await apiPost<{ id: number; name: string; type: string }>('/eval/suites', {
        name: newName.trim(),
        type: newType,
        description: newDesc.trim(),
      });
      setNewName('');
      setNewDesc('');
      setNotice(`套件「${created.name}」创建成功`);
      await loadSuites(false);
      setSelectedId(created.id);
    } catch (err) {
      setError(toMessage(err, '创建套件失败'));
    } finally {
      setBusy(null);
    }
  };

  const addCases = async () => {
    if (selectedId === null) return;
    const lines = caseText
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line !== '');
    if (lines.length === 0) {
      setError('请按每行 `问题||期望关键词||工具名` 的格式填写用例');
      return;
    }
    const payload = lines.map((line) => {
      const [input, expected, tools] = line.split('||').map((part) => part.trim());
      const expectTools = tools
        ? tools
            .split(/[,，\s]+/)
            .map((item) => item.trim())
            .filter(Boolean)
        : [];
      return {
        input,
        expected: expected || null,
        expect_tools: expectTools.length > 0 ? expectTools : null,
        meta: kbId === '' ? null : { kb_id: Number(kbId) },
      };
    });
    if (payload.some((item) => !item.input)) {
      setError('存在缺少问题文本的用例行');
      return;
    }

    setBusy('cases');
    setError(null);
    setNotice(null);
    try {
      await apiPost(`/eval/suites/${selectedId}/cases`, { cases: payload });
      setNotice(`已添加 ${payload.length} 条用例`);
      setCaseText('');
      await loadCases(selectedId);
      await loadSuites();
    } catch (err) {
      setError(toMessage(err, '添加用例失败'));
    } finally {
      setBusy(null);
    }
  };

  const runSuite = async () => {
    if (selectedId === null) return;
    setBusy('run');
    setError(null);
    setNotice(null);
    try {
      const result = await apiPost<EvalRunResult>(`/eval/suites/${selectedId}/run`, {
        top_k: Number(topK) || 5,
      });
      setLastRun(result);
      const detail = await apiGet<EvalReport>(`/eval/runs/${result.run_id}/report`);
      setReport(detail);
      setNotice(`评测完成：通过 ${result.case_passed}/${result.case_total}`);
      await loadRuns(selectedId);
      await loadSuites();
    } catch (err) {
      setError(toMessage(err, '评测执行失败'));
    } finally {
      setBusy(null);
    }
  };

  const openRun = async (runId: number) => {
    setBusy(`report-${runId}`);
    setError(null);
    try {
      const detail = await apiGet<EvalReport>(`/eval/runs/${runId}/report`);
      setReport(detail);
      setLastRun(null);
    } catch (err) {
      setError(toMessage(err, '加载评测报告失败'));
    } finally {
      setBusy(null);
    }
  };

  const selected = suites.find((item) => item.id === selectedId) ?? null;
  const metrics = lastRun?.metrics ?? report?.metrics ?? null;

  return (
    <div className="mx-auto max-w-[1180px] space-y-4">
      <PageHeader
        title="评测"
        description="检索与智能体套件的用例管理、指标计算与历史报告。"
        actions={
          <Button size="sm" variant="secondary" onClick={() => void loadSuites()} loading={suitesLoading}>
            刷新
          </Button>
        }
      />

      {error && <Banner tone="error" onClose={() => setError(null)}>{error}</Banner>}
      {notice && <Banner tone="success" onClose={() => setNotice(null)}>{notice}</Banner>}

      <Card title="新建套件" subtitle="retrieval 关注召回命中与 MRR；agent 关注答案与工具调用">
        <div className="grid gap-3 md:grid-cols-[1fr_140px_1.4fr_auto] md:items-end">
          <div>
            <label className={labelCls} htmlFor="suite-name">名称</label>
            <input
              id="suite-name"
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              placeholder="例如：检索基线 v1"
              className={inputCls}
            />
          </div>
          <div>
            <label className={labelCls} htmlFor="suite-type">类型</label>
            <select
              id="suite-type"
              value={newType}
              onChange={(event) => setNewType(event.target.value === 'retrieval' ? 'retrieval' : 'agent')}
              className={inputCls}
            >
              <option value="agent">agent</option>
              <option value="retrieval">retrieval</option>
            </select>
          </div>
          <div>
            <label className={labelCls} htmlFor="suite-desc">描述</label>
            <input
              id="suite-desc"
              value={newDesc}
              onChange={(event) => setNewDesc(event.target.value)}
              placeholder="可选"
              className={inputCls}
            />
          </div>
          <Button variant="primary" onClick={() => void createSuite()} loading={busy === 'create'}>
            创建
          </Button>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <Card title="套件列表" dense className="h-fit">
          {suitesLoading ? (
            <LoadingDots className="px-1 py-3" label="加载中" />
          ) : suites.length === 0 ? (
            <EmptyState compact title="还没有套件" description="先创建一个检索或智能体套件。" />
          ) : (
            <ul className="space-y-1">
              {suites.map((suite) => (
                <li key={suite.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(suite.id)}
                    className={cn(
                      'w-full rounded-lg border px-2.5 py-2 text-left transition-colors',
                      suite.id === selectedId
                        ? 'border-accent/40 bg-accent/10'
                        : 'border-transparent hover:border-line hover:bg-panel-soft',
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-[12px] text-fg">{suite.name}</span>
                      <Badge tone={suite.type === 'retrieval' ? 'info' : 'warn'}>{suite.type}</Badge>
                    </div>
                    <div className="mt-1 flex items-center gap-2 font-mono text-[10px] text-muted/80">
                      <span>#{suite.id}</span>
                      <span>{suite.case_count} 用例</span>
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
              <EmptyState title="请选择评测套件" description="选择后可以添加用例并执行评测。" />
            </Card>
          ) : (
            <>
              <Card
                title={`用例 · ${selected.name}`}
                subtitle={`共 ${cases.length} 条 · 每行格式：问题||期望关键词||工具名（工具名可多个，逗号分隔）`}
                actions={
                  <Button size="sm" variant="primary" onClick={() => void addCases()} loading={busy === 'cases'}>
                    添加用例
                  </Button>
                }
              >
                <div className="space-y-2.5">
                  <textarea
                    value={caseText}
                    onChange={(event) => setCaseText(event.target.value)}
                    rows={5}
                    placeholder={CASES_PLACEHOLDER}
                    className={textareaCls}
                  />
                  <div className="flex flex-wrap items-end gap-2">
                    <div className="w-[220px]">
                      <label className={labelCls} htmlFor="suite-kb">绑定知识库（写入用例 meta）</label>
                      <select
                        id="suite-kb"
                        value={kbId}
                        onChange={(event) => setKbId(event.target.value)}
                        className={inputCls}
                      >
                        <option value="">不绑定</option>
                        {kbs.map((kb) => (
                          <option key={kb.id} value={String(kb.id)}>
                            {kb.name}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>

                  {casesLoading ? (
                    <LoadingDots label="加载用例" />
                  ) : cases.length === 0 ? (
                    <EmptyState compact title="暂无用例" description="按上面格式粘贴多行内容后点击「添加用例」。" />
                  ) : (
                    <div className={tableWrapCls}>
                      <table className={tableCls}>
                        <thead>
                          <tr>
                            <th className={thCls}>#</th>
                            <th className={thCls}>问题</th>
                            <th className={thCls}>期望</th>
                            <th className={thCls}>期望工具</th>
                          </tr>
                        </thead>
                        <tbody>
                          {cases.map((item) => (
                            <tr key={item.id} className="hover:bg-panel-soft/40">
                              <td className={cn(tdCls, 'font-mono text-[10px] text-muted')}>{item.id}</td>
                              <td className={cn(tdCls, 'max-w-[320px]')}>
                                <div className="truncate">{item.input}</div>
                              </td>
                              <td className={cn(tdCls, 'max-w-[180px]')}>
                                <div className="truncate font-mono text-[11px] text-muted">
                                  {item.expected ?? '-'}
                                </div>
                              </td>
                              <td className={cn(tdCls, 'font-mono text-[10px] text-muted')}>
                                {(item.expect_tools ?? []).join(', ') || '-'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </Card>

              <Card
                title="运行评测"
                subtitle="检索类按 top_k 召回，智能体类会真实跑一遍规划与工具调用"
                actions={
                  <Button size="sm" variant="primary" onClick={() => void runSuite()} loading={busy === 'run'}>
                    运行
                  </Button>
                }
              >
                <div className="flex flex-wrap items-end gap-2">
                  <div className="w-[110px]">
                    <label className={labelCls} htmlFor="run-topk">top_k</label>
                    <input
                      id="run-topk"
                      value={topK}
                      onChange={(event) => setTopK(event.target.value)}
                      inputMode="numeric"
                      className={inputCls}
                    />
                  </div>
                  {lastRun && <Badge tone={statusTone(lastRun.status)}>{lastRun.status}</Badge>}
                  {lastRun && (
                    <span className={monoCls}>
                      运行 #{lastRun.run_id} · 通过 {lastRun.case_passed}/{lastRun.case_total}
                    </span>
                  )}
                </div>

                {metrics ? (
                  <div className="mt-3 grid grid-cols-2 gap-2.5 sm:grid-cols-5">
                    <Metric
                      label="hit_rate"
                      value={metrics.hit_rate === undefined ? '-' : formatPercent(metrics.hit_rate)}
                      tone="accent"
                    />
                    <Metric
                      label="hit_top1"
                      value={
                        metrics.hit_rate_top1 === undefined ? '-' : formatPercent(metrics.hit_rate_top1)
                      }
                      tone="accent"
                    />
                    <Metric label="mrr" value={metrics.mrr === undefined ? '-' : metrics.mrr.toFixed(3)} tone="accent" />
                    <Metric label="pass_rate" value={formatPercent(metrics.pass_rate)} tone="ok" />
                    <Metric label="p95" value={formatMs(metrics.latency_p95_ms)} tone="fg" />
                  </div>
                ) : (
                  <div className="mt-3 text-[11px] text-muted">尚未运行，点击右上角「运行」开始评测。</div>
                )}

                {metrics && (
                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-muted">
                    <span>total {formatNumber(metrics.total)}</span>
                    <span>passed {formatNumber(metrics.passed)}</span>
                    <span>avg {formatMs(metrics.latency_avg_ms)}</span>
                    {report && <span>p95 {formatMs(report.latency_p95_ms)}</span>}
                  </div>
                )}

                {report && (
                  <div className="mt-3">
                    <div className="mb-1.5 flex items-center gap-2">
                      <span className="text-[10px] uppercase tracking-wide text-muted">失败用例</span>
                      <Badge tone={report.failures.length > 0 ? 'bad' : 'ok'}>
                        {report.failures.length}
                      </Badge>
                    </div>
                    {report.failures.length === 0 ? (
                      <div className="rounded-md border border-ok/25 bg-ok/10 px-2.5 py-1.5 text-[11px] text-ok">
                        全部用例通过
                      </div>
                    ) : (
                      <ul className="space-y-1.5">
                        {report.failures.map((failure, index) => (
                          <li
                            key={String(failure.case_id ?? index)}
                            className="rounded-md border border-bad/25 bg-bad/10 px-2.5 py-2"
                          >
                            <div className="flex flex-wrap items-center gap-2">
                              <Badge tone="bad">case #{String(failure.case_id ?? '-')}</Badge>
                              {Boolean(failure.error) && (
                                <span className="text-[10px] text-bad/90">{String(failure.error)}</span>
                              )}
                            </div>
                            {Boolean(failure.output) && (
                              <div className="mt-1 line-clamp-2 text-[11px] text-muted">
                                输出：{String(failure.output)}
                              </div>
                            )}
                            {isRecord(failure.scores) && (
                              <div className="mt-1">
                                <JsonView value={failure.scores} collapsedHeight={64} />
                              </div>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </Card>

              <Card title="历史运行" subtitle="点击可加载对应报告">
                {runsLoading ? (
                  <LoadingDots label="加载历史" />
                ) : runs.length === 0 ? (
                  <div className="text-[11px] text-muted">暂无运行记录</div>
                ) : (
                  <div className={tableWrapCls}>
                    <table className={tableCls}>
                      <thead>
                        <tr>
                          <th className={thCls}>run</th>
                          <th className={thCls}>状态</th>
                          <th className={thCls}>通过</th>
                          <th className={thCls}>pass_rate</th>
                          <th className={thCls}>p95</th>
                          <th className={thCls}>时间</th>
                          <th className={thCls}>操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {runs.map((run) => (
                          <tr key={run.run_id} className="hover:bg-panel-soft/40">
                            <td className={cn(tdCls, 'font-mono text-[11px]')}>#{run.run_id}</td>
                            <td className={tdCls}>
                              <Badge tone={statusTone(run.status)}>{run.status}</Badge>
                            </td>
                            <td className={cn(tdCls, 'font-mono text-[11px]')}>
                              {run.case_passed}/{run.case_total}
                            </td>
                            <td className={cn(tdCls, 'font-mono text-[11px]')}>
                              {formatPercent(run.metrics?.pass_rate)}
                            </td>
                            <td className={cn(tdCls, 'font-mono text-[11px]')}>
                              {formatMs(run.latency_p95_ms ?? run.metrics?.latency_p95_ms)}
                            </td>
                            <td className={cn(tdCls, 'whitespace-nowrap font-mono text-[10px] text-muted')}>
                              {formatTime(run.finished_at ?? run.started_at)}
                            </td>
                            <td className={tdCls}>
                              <Button
                                size="sm"
                                variant="ghost"
                                className="h-6 px-1.5 text-[10px]"
                                loading={busy === `report-${run.run_id}`}
                                onClick={() => void openRun(run.run_id)}
                              >
                                查看报告
                              </Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
