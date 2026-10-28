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
import { apiGet, apiPatch, apiPost } from '@/lib/api';
import { cn } from '@/lib/cn';
import { formatMs, formatNumber, formatTime, isRecord, toJsonText, toMessage } from '@/lib/format';
import { tableCls, tableWrapCls, tdCls, thCls, textareaCls } from '@/lib/ui';
import type { ToolInfo, ToolInvocation, ToolInvokeResult } from '@/lib/types';

/** 依据 JSON Schema 生成一份可直接运行的参数示例。 */
function sampleFromSchema(schema: unknown): string {
  if (!isRecord(schema)) return '{}';
  const properties = schema.properties;
  const sample: Record<string, unknown> = {};
  if (isRecord(properties)) {
    for (const [key, raw] of Object.entries(properties)) {
      if (!isRecord(raw)) {
        sample[key] = '';
        continue;
      }
      const type = typeof raw.type === 'string' ? raw.type : 'string';
      const fallback = raw.default;
      if (fallback !== undefined) {
        sample[key] = fallback;
      } else if (type === 'number' || type === 'integer') {
        sample[key] = 0;
      } else if (type === 'boolean') {
        sample[key] = false;
      } else if (type === 'array') {
        sample[key] = [];
      } else if (type === 'object') {
        sample[key] = {};
      } else {
        sample[key] = '';
      }
    }
  }
  return JSON.stringify(sample, null, 2);
}

function Switch({ checked, onChange, disabled }: { checked: boolean; onChange: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={onChange}
      className={cn(
        'relative h-4.5 w-8 shrink-0 rounded-full border transition-colors disabled:opacity-50',
        checked ? 'border-ok/50 bg-ok/30' : 'border-line bg-panel-soft',
      )}
    >
      <span
        className={cn(
          'absolute top-0.5 h-3 w-3 rounded-full transition-all',
          checked ? 'left-[16px] bg-ok' : 'left-0.5 bg-muted',
        )}
      />
    </button>
  );
}

export default function ToolsPage() {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [openName, setOpenName] = useState<string | null>(null);
  const [toggling, setToggling] = useState<string | null>(null);

  const [argsDraft, setArgsDraft] = useState<Record<string, string>>({});
  const [results, setResults] = useState<Record<string, ToolInvokeResult>>({});
  const [invoking, setInvoking] = useState<string | null>(null);

  const [invocations, setInvocations] = useState<ToolInvocation[]>([]);
  const [invTotal, setInvTotal] = useState(0);
  const [invPage, setInvPage] = useState(1);
  const [invLoading, setInvLoading] = useState(true);

  const size = 10;

  const loadTools = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiGet<ToolInfo[]>('/tools');
      const list = Array.isArray(data) ? data : [];
      setTools(list);
      setArgsDraft((prev) => {
        const next = { ...prev };
        for (const tool of list) {
          if (next[tool.name] === undefined) next[tool.name] = sampleFromSchema(tool.params_schema);
        }
        return next;
      });
    } catch (err) {
      setError(toMessage(err, '加载工具列表失败'));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadInvocations = useCallback(async (page: number) => {
    setInvLoading(true);
    try {
      const data = await apiGet<{ items: ToolInvocation[]; total: number }>(
        `/tools/invocations?page=${page}&size=${size}`,
      );
      setInvocations(Array.isArray(data?.items) ? data.items : []);
      setInvTotal(typeof data?.total === 'number' ? data.total : 0);
    } catch (err) {
      setError(toMessage(err, '加载调用流水失败'));
    } finally {
      setInvLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTools();
    void loadInvocations(1);
  }, [loadTools, loadInvocations]);

  const toggleTool = async (tool: ToolInfo) => {
    setToggling(tool.name);
    setError(null);
    try {
      await apiPatch(`/tools/${tool.name}/enabled`, { enabled: !tool.enabled });
      setTools((prev) =>
        prev.map((item) => (item.name === tool.name ? { ...item, enabled: !item.enabled } : item)),
      );
      setNotice(`工具「${tool.display_name}」已${tool.enabled ? '停用' : '启用'}`);
    } catch (err) {
      setError(toMessage(err, '切换工具状态失败'));
    } finally {
      setToggling(null);
    }
  };

  const invoke = async (tool: ToolInfo) => {
    const draft = argsDraft[tool.name] ?? '{}';
    let args: unknown;
    try {
      args = JSON.parse(draft || '{}') as unknown;
    } catch {
      setError('参数不是合法 JSON，请检查后重试');
      return;
    }
    if (!isRecord(args)) {
      setError('参数必须是 JSON 对象，例如 {"query":"你好"}');
      return;
    }
    setInvoking(tool.name);
    setError(null);
    try {
      const result = await apiPost<ToolInvokeResult>(`/tools/${tool.name}/invoke`, { args });
      setResults((prev) => ({ ...prev, [tool.name]: result }));
      await loadTools();
      await loadInvocations(1);
      setInvPage(1);
    } catch (err) {
      setError(toMessage(err, '工具调用失败'));
    } finally {
      setInvoking(null);
    }
  };

  const invPageCount = Math.max(1, Math.ceil(invTotal / size));

  return (
    <div className="mx-auto max-w-[1180px] space-y-4">
      <PageHeader
        title="工具"
        description="查看注册的工具、按 Schema 在线调试，并追踪最近调用流水。"
        actions={
          <Button size="sm" variant="secondary" onClick={() => void loadTools()} loading={loading}>
            刷新
          </Button>
        }
      />

      {error && <Banner tone="error" onClose={() => setError(null)}>{error}</Banner>}
      {notice && <Banner tone="success" onClose={() => setNotice(null)}>{notice}</Banner>}

      {loading ? (
        <Card>
          <LoadingDots label="加载工具注册表…" />
        </Card>
      ) : tools.length === 0 ? (
        <Card>
          <EmptyState title="暂无注册工具" description="后端启动时会同步内置工具到注册表。" />
        </Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {tools.map((tool) => {
            const open = openName === tool.name;
            const result = results[tool.name];
            const successRate = tool.call_count > 0 ? (tool.success_count / tool.call_count) * 100 : 0;
            return (
              <Card
                key={tool.name}
                className={cn('transition-colors', open && 'border-accent/35')}
                title={tool.display_name}
                subtitle={tool.description}
                actions={
                  <Switch
                    checked={tool.enabled}
                    disabled={toggling === tool.name}
                    onChange={() => void toggleTool(tool)}
                  />
                }
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge tone="info">{tool.category || 'general'}</Badge>
                  <Badge tone={tool.enabled ? 'ok' : 'neutral'}>{tool.enabled ? '已启用' : '已停用'}</Badge>
                  <span className="font-mono text-[10px] text-muted">超时 {tool.timeout_s}s</span>
                  <span className="font-mono text-[10px] text-muted">
                    调用 {formatNumber(tool.call_count)} · 成功率 {successRate.toFixed(1)}%
                  </span>
                </div>
                <div className="mt-1 font-mono text-[10px] text-muted/70">{tool.name}</div>

                <div className="mt-2 flex items-center gap-2">
                  <Button
                    size="sm"
                    variant={open ? 'secondary' : 'ghost'}
                    className="h-6 px-2 text-[11px]"
                    onClick={() => setOpenName(open ? null : tool.name)}
                  >
                    {open ? '收起调试' : '参数 / 调试'}
                  </Button>
                  {(tool.tags ?? []).slice(0, 3).map((tag) => (
                    <span key={tag} className="font-mono text-[10px] text-muted/70">
                      #{tag}
                    </span>
                  ))}
                </div>

                {open && (
                  <div className="mt-3 space-y-2.5 border-t border-line-soft pt-3">
                    <div>
                      <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">参数 Schema</div>
                      <JsonView value={tool.params_schema} collapsedHeight={140} emptyText="该工具无参数" />
                    </div>
                    <div>
                      <div className="mb-1 flex items-center justify-between">
                        <span className="text-[10px] uppercase tracking-wide text-muted">调试参数</span>
                        <button
                          type="button"
                          className="text-[10px] text-muted transition-colors hover:text-accent"
                          onClick={() =>
                            setArgsDraft((prev) => ({ ...prev, [tool.name]: sampleFromSchema(tool.params_schema) }))
                          }
                        >
                          按 Schema 重置
                        </button>
                      </div>
                      <textarea
                        value={argsDraft[tool.name] ?? '{}'}
                        onChange={(event) =>
                          setArgsDraft((prev) => ({ ...prev, [tool.name]: event.target.value }))
                        }
                        rows={5}
                        className={textareaCls}
                        placeholder='{"query": "示例参数"}'
                      />
                    </div>
                    <div className="flex items-center gap-2">
                      <Button
                        size="sm"
                        variant="primary"
                        loading={invoking === tool.name}
                        onClick={() => void invoke(tool)}
                      >
                        执行
                      </Button>
                      {result && (
                        <>
                          <Badge tone={result.ok ? 'ok' : 'bad'}>{result.ok ? '成功' : '失败'}</Badge>
                          <span className="font-mono text-[10px] text-muted">{formatMs(result.latency_ms)}</span>
                          <span className="font-mono text-[10px] text-muted">重试 {result.retries}</span>
                        </>
                      )}
                    </div>
                    {result && (
                      <div className="space-y-1.5">
                        {result.error && (
                          <div className="rounded-md border border-bad/30 bg-bad/10 px-2 py-1 text-[11px] text-bad">
                            {result.error}
                          </div>
                        )}
                        <JsonView value={result.data} collapsedHeight={160} emptyText="无返回数据" />
                        {result.observation && (
                          <div className="rounded-md border border-line bg-[#0d131a] px-2 py-1.5 text-[10px] leading-relaxed text-muted">
                            {result.observation}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}

      <Card title="调用流水" subtitle={`共 ${invTotal} 条记录`}>
        {invLoading ? (
          <LoadingDots label="加载流水" />
        ) : invocations.length === 0 ? (
          <EmptyState compact title="暂无调用记录" description="在上方调试任一工具后，这里会出现调用明细。" />
        ) : (
          <>
            <div className={tableWrapCls}>
              <table className={tableCls}>
                <thead>
                  <tr>
                    <th className={thCls}>#</th>
                    <th className={thCls}>工具</th>
                    <th className={thCls}>状态</th>
                    <th className={thCls}>耗时</th>
                    <th className={thCls}>参数</th>
                    <th className={thCls}>时间</th>
                  </tr>
                </thead>
                <tbody>
                  {invocations.map((record) => (
                    <tr key={record.id} className="hover:bg-panel-soft/40">
                      <td className={cn(tdCls, 'font-mono text-[10px] text-muted')}>{record.id}</td>
                      <td className={tdCls}>
                        <div className="font-mono text-[11px] text-fg/90">{record.tool_name}</div>
                        {record.error && (
                          <div className="mt-0.5 max-w-[220px] break-words text-[10px] text-bad/90">
                            {record.error}
                          </div>
                        )}
                      </td>
                      <td className={tdCls}>
                        <Badge tone={statusTone(record.status)}>{record.status}</Badge>
                      </td>
                      <td className={cn(tdCls, 'font-mono text-[11px]')}>
                        {formatMs(record.latency_ms)}
                        {record.retries > 0 && (
                          <span className="ml-1 text-[10px] text-warn">×{record.retries}</span>
                        )}
                      </td>
                      <td className={cn(tdCls, 'max-w-[300px]')}>
                        <div className="truncate font-mono text-[10px] text-muted" title={toJsonText(record.args, 0)}>
                          {toJsonText(record.args, 0)}
                        </div>
                      </td>
                      <td className={cn(tdCls, 'whitespace-nowrap font-mono text-[10px] text-muted')}>
                        {formatTime(record.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-2 flex items-center justify-between text-[11px] text-muted">
              <span className="font-mono">
                第 {invPage} / {invPageCount} 页
              </span>
              <div className="flex gap-1.5">
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={invPage <= 1}
                  onClick={() => {
                    const next = invPage - 1;
                    setInvPage(next);
                    void loadInvocations(next);
                  }}
                >
                  上一页
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={invPage >= invPageCount}
                  onClick={() => {
                    const next = invPage + 1;
                    setInvPage(next);
                    void loadInvocations(next);
                  }}
                >
                  下一页
                </Button>
              </div>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
