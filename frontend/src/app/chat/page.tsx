'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Badge from '@/components/Badge';
import Banner from '@/components/Banner';
import Button from '@/components/Button';
import EmptyState from '@/components/EmptyState';
import LoadingDots from '@/components/LoadingDots';
import { ApiError, apiGet, apiPost, streamChat } from '@/lib/api';
import { cn } from '@/lib/cn';
import { formatMs, formatNumber, formatTime, isRecord, toMessage } from '@/lib/format';
import { inputCls } from '@/lib/ui';
import type { ChatMessage, Citation, ConversationBrief, KnowledgeBase } from '@/lib/types';

type TraceKind = 'plan' | 'node' | 'thought' | 'tool' | 'status';

interface TraceEntry {
  id: number;
  kind: TraceKind;
  title: string;
  detail?: string;
  ok?: boolean | null;
  latency?: number | null;
  pending?: boolean;
}

interface UsageInfo {
  prompt_tokens: number;
  completion_tokens: number;
  steps: number;
  tool_calls: number;
  duration_ms: number;
  mock_mode: boolean;
}

interface DisplayMessage extends ChatMessage {
  trace?: TraceEntry[];
  usage?: UsageInfo | null;
}

const TRACE_STYLE: Record<TraceKind, { dot: string; text: string; label: string }> = {
  plan: { dot: 'bg-accent', text: 'text-accent', label: '规划' },
  node: { dot: 'bg-muted', text: 'text-muted', label: '节点' },
  thought: { dot: 'bg-[#a371f7]', text: 'text-[#b48bfa]', label: '思考' },
  tool: { dot: 'bg-ok', text: 'text-ok', label: '工具' },
  status: { dot: 'bg-warn', text: 'text-warn', label: '状态' },
};

function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…（已截断）` : text;
}

function readTasks(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (!isRecord(item)) return '';
      const step = item.step;
      const action = item.action;
      const desc = item.desc;
      const head = [typeof step === 'number' ? `${step}.` : '', typeof action === 'string' ? action : '']
        .filter(Boolean)
        .join(' ');
      const tail = typeof desc === 'string' ? desc : '';
      return `${head}${head && tail ? ' — ' : ''}${tail}`;
    })
    .filter(Boolean);
}

function TraceTimeline({ entries }: { entries: TraceEntry[] }) {
  if (entries.length === 0) return null;
  return (
    <ol className="mt-3 space-y-1.5 border-l border-line pl-3">
      {entries.map((entry) => {
        const style = TRACE_STYLE[entry.kind];
        return (
          <li key={entry.id} className="relative wb-fade-in">
            <span
              className={cn(
                'absolute -left-[17px] top-[6px] h-1.5 w-1.5 rounded-full ring-2 ring-panel',
                entry.pending ? 'bg-warn' : entry.ok === false ? 'bg-bad' : style.dot,
              )}
            />
            <div className="flex flex-wrap items-center gap-1.5">
              <span className={cn('text-[10px] uppercase tracking-wide', style.text)}>{style.label}</span>
              <span className="text-[12px] text-fg/90">{entry.title}</span>
              {entry.kind === 'tool' && entry.pending && <Badge tone="warn">执行中</Badge>}
              {entry.kind === 'tool' && entry.ok === true && <Badge tone="ok">成功</Badge>}
              {entry.kind === 'tool' && entry.ok === false && <Badge tone="bad">失败</Badge>}
              {typeof entry.latency === 'number' && (
                <span className="font-mono text-[10px] text-muted">{formatMs(entry.latency)}</span>
              )}
            </div>
            {entry.detail && (
              <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md border border-line-soft bg-[#0d131a] px-2 py-1.5 font-mono text-[11px] leading-relaxed text-muted">
                {entry.detail}
              </pre>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function Citations({ items }: { items: Citation[] }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-3 rounded-lg border border-line bg-[#0d131a] p-2.5">
      <div className="mb-1.5 text-[10px] uppercase tracking-widest text-muted">引用来源 {items.length}</div>
      <ul className="space-y-1.5">
        {items.map((item) => (
          <li key={`${item.chunk_id}-${item.index}`} className="flex gap-2">
            <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded bg-accent/15 font-mono text-[10px] text-accent">
              {item.index}
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="truncate text-[12px] text-fg/90">{item.doc_name}</span>
                <span className="font-mono text-[10px] text-muted">score {Number(item.score ?? 0).toFixed(3)}</span>
              </div>
              <p className="mt-0.5 line-clamp-2 text-[11px] leading-relaxed text-muted">{item.snippet}</p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function UsageBar({ usage }: { usage: UsageInfo }) {
  return (
    <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line-soft pt-2 font-mono text-[10px] text-muted">
      <span>tokens {formatNumber(usage.prompt_tokens)}→{formatNumber(usage.completion_tokens)}</span>
      <span>steps {formatNumber(usage.steps)}</span>
      <span>tools {formatNumber(usage.tool_calls)}</span>
      <span>{formatMs(usage.duration_ms)}</span>
      {usage.mock_mode && <Badge tone="warn">Mock</Badge>}
    </div>
  );
}

export default function ChatPage() {
  const [conversations, setConversations] = useState<ConversationBrief[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [kbId, setKbId] = useState<string>('');
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [answer, setAnswer] = useState('');
  const [trace, setTrace] = useState<TraceEntry[]>([]);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [usage, setUsage] = useState<UsageInfo | null>(null);
  const [runMock, setRunMock] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 断线与普通失败分开：断线时页面已渲染的内容要保留，并给出「重试这一问」入口
  const [interrupted, setInterrupted] = useState<{ query: string; message: string } | null>(null);
  const [convLoading, setConvLoading] = useState(true);
  const [msgLoading, setMsgLoading] = useState(false);

  const answerRef = useRef('');
  const traceRef = useRef<TraceEntry[]>([]);
  const citationsRef = useRef<Citation[]>([]);
  const usageRef = useRef<UsageInfo | null>(null);
  const idRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const activeIdRef = useRef<number | null>(null);

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  const pushTrace = useCallback((entry: Omit<TraceEntry, 'id'>) => {
    const next = [...traceRef.current, { ...entry, id: (idRef.current += 1) }];
    traceRef.current = next;
    setTrace(next);
  }, []);

  const patchLastTool = useCallback((name: string, patch: Partial<TraceEntry>) => {
    const next = [...traceRef.current];
    for (let index = next.length - 1; index >= 0; index -= 1) {
      const entry = next[index];
      if (entry.kind === 'tool' && entry.pending && entry.title === name) {
        next[index] = { ...entry, ...patch, pending: false };
        traceRef.current = next;
        setTrace(next);
        return;
      }
    }
    if (patch.detail !== undefined) {
      const fallback = [
        ...next,
        { id: (idRef.current += 1), kind: 'tool' as TraceKind, title: name, ...patch, pending: false },
      ];
      traceRef.current = fallback;
      setTrace(fallback);
    }
  }, []);

  const loadConversations = useCallback(async () => {
    setConvLoading(true);
    try {
      const data = await apiGet<{ items: ConversationBrief[]; total: number }>(
        '/conversations?page=1&size=50',
      );
      setConversations(Array.isArray(data?.items) ? data.items : []);
    } catch (err) {
      setError(toMessage(err, '加载会话列表失败'));
    } finally {
      setConvLoading(false);
    }
  }, []);

  const loadKnowledgeBases = useCallback(async () => {
    try {
      const data = await apiGet<KnowledgeBase[]>('/knowledge/bases');
      setKbs(Array.isArray(data) ? data : []);
    } catch {
      setKbs([]);
    }
  }, []);

  useEffect(() => {
    void loadConversations();
    void loadKnowledgeBases();
  }, [loadConversations, loadKnowledgeBases]);

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages, answer, trace]);

  const openConversation = useCallback(async (id: number) => {
    abortRef.current?.abort();
    setActiveId(id);
    setError(null);
    setMsgLoading(true);
    setAnswer('');
    setTrace([]);
    setCitations([]);
    setUsage(null);
    traceRef.current = [];
    answerRef.current = '';
    try {
      const data = await apiGet<ChatMessage[]>(`/conversations/${id}/messages`);
      setMessages(Array.isArray(data) ? data : []);
    } catch (err) {
      setMessages([]);
      setError(toMessage(err, '加载消息失败'));
    } finally {
      setMsgLoading(false);
    }
  }, []);

  // 支持 /chat?id=23 直接打开指定会话：刷新后能停在原会话，也给截图/分享一个稳定入口。
  // 用 window.location 而不是 useSearchParams，避免静态预渲染要求 Suspense 包裹。
  const openedFromUrl = useRef(false);
  useEffect(() => {
    if (openedFromUrl.current) return;
    openedFromUrl.current = true;
    const id = Number(new URLSearchParams(window.location.search).get('id'));
    if (Number.isFinite(id) && id > 0) void openConversation(id);
  }, [openConversation]);

  const newConversation = useCallback(async () => {
    abortRef.current?.abort();
    setError(null);
    setAnswer('');
    setTrace([]);
    setCitations([]);
    setUsage(null);
    traceRef.current = [];
    answerRef.current = '';
    setMessages([]);
    try {
      const created = await apiPost<{ id: number; title: string }>('/conversations', {
        title: '新会话',
        knowledge_base_id: kbId === '' ? null : Number(kbId),
      });
      setActiveId(created.id);
      await loadConversations();
    } catch (err) {
      setActiveId(null);
      setError(toMessage(err, '新建会话失败，可直接发送消息自动创建会话'));
    }
  }, [kbId, loadConversations]);

  const send = useCallback(async (overrideQuery?: string) => {
    const query = (overrideQuery ?? input).trim();
    if (!query || streaming) return;

    setError(null);
    setInterrupted(null);
    setInput('');
    setStreaming(true);
    setAnswer('');
    setCitations([]);
    setUsage(null);
    setTrace([]);
    traceRef.current = [];
    citationsRef.current = [];
    usageRef.current = null;
    answerRef.current = '';
    setRunMock(false);
    setMessages((prev) => [
      ...prev,
      {
        id: -Date.now(),
        role: 'user',
        content: query,
        citations: null,
        token_in: null,
        token_out: null,
        created_at: new Date().toISOString(),
      },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;
    let conversationId = activeIdRef.current;
    let runError: string | null = null;

    try {
      await streamChat(
        { query, conversation_id: conversationId, kb_id: kbId === '' ? null : Number(kbId) },
        (type, data) => {
          switch (type) {
            case 'conversation': {
              if (isRecord(data) && typeof data.conversation_id === 'number') {
                conversationId = data.conversation_id;
                setActiveId(data.conversation_id);
              }
              break;
            }
            case 'run_started': {
              if (isRecord(data) && data.mock_mode === true) setRunMock(true);
              break;
            }
            case 'plan': {
              if (!isRecord(data)) break;
              const intent = typeof data.intent === 'string' ? data.intent : '未知意图';
              const needTools = data.need_tools === true ? '需要工具' : '无需工具';
              const tasks = readTasks(data.tasks);
              const latency = typeof data.latency_ms === 'number' ? data.latency_ms : null;
              pushTrace({
                kind: 'plan',
                title: `意图 ${intent} · ${needTools} · ${tasks.length} 个任务`,
                detail: tasks.length > 0 ? tasks.join('\n') : undefined,
                latency,
              });
              break;
            }
            case 'node': {
              if (!isRecord(data)) break;
              const label = typeof data.label === 'string' ? data.label : '';
              const node = typeof data.node === 'string' ? data.node : '';
              pushTrace({ kind: 'node', title: label || node });
              break;
            }
            case 'thought': {
              if (!isRecord(data) || typeof data.content !== 'string') break;
              pushTrace({ kind: 'thought', title: '推理片段', detail: clip(data.content, 600) });
              break;
            }
            case 'tool_call': {
              if (!isRecord(data)) break;
              const name = typeof data.name === 'string' ? data.name : 'unknown';
              pushTrace({
                kind: 'tool',
                title: name,
                detail: clip(JSON.stringify(data.arguments ?? {}, null, 2), 400),
                pending: true,
              });
              break;
            }
            case 'tool_result': {
              if (!isRecord(data)) break;
              const name = typeof data.name === 'string' ? data.name : 'unknown';
              const ok = data.ok === true;
              const latency = typeof data.latency_ms === 'number' ? data.latency_ms : null;
              const retries = typeof data.retries === 'number' ? data.retries : 0;
              const preview =
                typeof data.preview === 'string'
                  ? data.preview
                  : data.error
                    ? String(data.error)
                    : '';
              patchLastTool(name, {
                ok,
                latency,
                detail: clip(
                  `${retries > 0 ? `重试 ${retries} 次 · ` : ''}${preview}`.trim(),
                  600,
                ) || undefined,
              });
              break;
            }
            case 'token': {
              if (!isRecord(data) || typeof data.delta !== 'string') break;
              answerRef.current += data.delta;
              setAnswer(answerRef.current);
              break;
            }
            case 'citations': {
              if (!isRecord(data) || !Array.isArray(data.citations)) break;
              const list = data.citations as Citation[];
              citationsRef.current = list;
              setCitations(list);
              break;
            }
            case 'usage': {
              if (!isRecord(data)) break;
              const info: UsageInfo = {
                prompt_tokens: typeof data.prompt_tokens === 'number' ? data.prompt_tokens : 0,
                completion_tokens:
                  typeof data.completion_tokens === 'number' ? data.completion_tokens : 0,
                steps: typeof data.steps === 'number' ? data.steps : 0,
                tool_calls: typeof data.tool_calls === 'number' ? data.tool_calls : 0,
                duration_ms: typeof data.duration_ms === 'number' ? data.duration_ms : 0,
                mock_mode: data.mock_mode === true,
              };
              usageRef.current = info;
              setUsage(info);
              break;
            }
            case 'run_finished': {
              if (isRecord(data) && data.status === 'failed') {
                runError = typeof data.error === 'string' ? data.error : '本次运行失败';
              }
              break;
            }
            case 'error': {
              runError = isRecord(data) && typeof data.message === 'string' ? data.message : '流式响应异常';
              break;
            }
            default:
              break;
          }
        },
        controller.signal,
      );
    } catch (err) {
      if (!controller.signal.aborted) {
        if (err instanceof ApiError && err.code === 'stream_interrupted') {
          // 已生成的部分保留在消息里，只提示断线并允许重试同一问
          setInterrupted({ query, message: err.message });
        } else {
          runError = toMessage(err, '对话请求失败');
        }
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;

      const finalAnswer = answerRef.current;
      // 注意：必须在重置 ref 之前把值快照下来。setMessages 的 updater 是延迟执行的，
      // 直接在 updater 内读 ref 会读到已被清空的新数组，导致轨迹与引用丢失。
      const finalTrace = traceRef.current as TraceEntry[];
      const finalCitations = citationsRef.current as Citation[];
      const finalUsage = usageRef.current as UsageInfo | null;
      if (finalAnswer || finalTrace.length > 0) {
        setMessages((prev) => [
          ...prev,
          {
            id: -Date.now() - 1,
            role: 'assistant',
            content: finalAnswer || '（本次运行未输出正文）',
            citations: finalCitations,
            token_in: finalUsage?.prompt_tokens ?? null,
            token_out: finalUsage?.completion_tokens ?? null,
            created_at: new Date().toISOString(),
            trace: finalTrace,
            usage: finalUsage,
          },
        ]);
        setAnswer('');
        setTrace([]);
        traceRef.current = [];
        setCitations([]);
        setUsage(null);
      }

      if (runError) setError(runError);
      void loadConversations();
    }
  }, [input, streaming, kbId, pushTrace, patchLastTool, loadConversations]);

  const retryInterrupted = useCallback(() => {
    if (!interrupted) return;
    const query = interrupted.query;
    setInterrupted(null);
    void send(query);
  }, [interrupted, send]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return (
    <div className="mx-auto flex h-[calc(100vh-3rem)] max-w-[1280px] gap-4">
      <aside className="flex w-[236px] shrink-0 flex-col rounded-xl border border-line bg-panel">
        <div className="flex items-center justify-between border-b border-line-soft px-3 py-2.5">
          <span className="text-[12px] font-medium">会话</span>
          <Button size="sm" variant="secondary" onClick={() => void newConversation()}>
            + 新建
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {convLoading ? (
            <LoadingDots className="px-1 py-2" label="加载会话" />
          ) : conversations.length === 0 ? (
            <div className="px-1 py-6 text-center text-[11px] text-muted">暂无会话</div>
          ) : (
            <ul className="space-y-0.5">
              {conversations.map((conv) => (
                <li key={conv.id}>
                  <button
                    type="button"
                    onClick={() => void openConversation(conv.id)}
                    className={cn(
                      'w-full rounded-md px-2 py-1.5 text-left transition-colors',
                      activeId === conv.id ? 'bg-accent/12 text-fg' : 'text-muted hover:bg-panel-soft hover:text-fg',
                    )}
                  >
                    <div className="truncate text-[12px]">{conv.title || `会话 ${conv.id}`}</div>
                    <div className="mt-0.5 flex items-center gap-2 font-mono text-[10px] text-muted/70">
                      <span>#{conv.id}</span>
                      <span>{conv.message_count} 条</span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col rounded-xl border border-line bg-panel">
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line-soft px-4 py-2.5">
          <div className="flex items-center gap-2">
            <h1 className="text-[14px] font-medium">对话调试</h1>
            {activeId !== null && <Badge tone="info">会话 #{activeId}</Badge>}
            {runMock && <Badge tone="warn">Mock 模式</Badge>}
          </div>
          <div className="flex items-center gap-2">
            <select
              value={kbId}
              onChange={(event) => setKbId(event.target.value)}
              className="h-7 rounded-md border border-line bg-[#0d131a] px-2 text-[11px] text-fg outline-none focus:border-accent/60"
            >
              <option value="">不挂载知识库</option>
              {kbs.map((kb) => (
                <option key={kb.id} value={String(kb.id)}>
                  {kb.name}
                </option>
              ))}
            </select>
            {streaming && (
              <Button size="sm" variant="danger" onClick={stop}>
                停止生成
              </Button>
            )}
          </div>
        </header>

        {error && (
          <div className="px-3 pt-3">
            <Banner tone="error" onClose={() => setError(null)}>
              {error}
            </Banner>
          </div>
        )}

        {interrupted && (
          <div className="px-3 pt-3">
            <Banner tone="warn" onClose={() => setInterrupted(null)}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1">{interrupted.message}</span>
                <button
                  type="button"
                  onClick={retryInterrupted}
                  className="shrink-0 rounded-md border border-warn/50 px-2 py-0.5 text-[11px] font-medium transition-colors hover:bg-warn/20"
                >
                  重试这一问
                </button>
              </div>
            </Banner>
          </div>
        )}

        <div ref={scrollRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
          {msgLoading ? (
            <LoadingDots label="加载历史消息" />
          ) : messages.length === 0 && !streaming ? (
            <EmptyState
              title="开始一段新对话"
              description="输入问题后，服务端会以 SSE 推送规划、工具调用、思考与逐字输出，这里会实时渲染执行轨迹。"
              className="mt-6"
            />
          ) : (
            messages.map((message) => {
              const isUser = message.role === 'user';
              return (
                <div key={message.id} className={cn('flex gap-2.5', isUser && 'flex-row-reverse')}>
                  <div
                    className={cn(
                      'mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[10px] font-medium',
                      isUser ? 'bg-panel-soft text-muted' : 'bg-accent/15 text-accent',
                    )}
                  >
                    {isUser ? '我' : 'AI'}
                  </div>
                  <div className={cn('min-w-0 max-w-[86%]', isUser && 'flex flex-col items-end')}>
                    <div
                      className={cn(
                        'rounded-lg border px-3 py-2 text-[13px] leading-relaxed',
                        isUser
                          ? 'border-line bg-panel-soft text-fg'
                          : 'border-line-soft bg-[#0d131a] text-fg/95',
                      )}
                    >
                      <div className="whitespace-pre-wrap break-words">{message.content}</div>
                      {!isUser && <TraceTimeline entries={message.trace ?? []} />}
                      {!isUser && <Citations items={message.citations ?? []} />}
                      {!isUser && message.usage && <UsageBar usage={message.usage} />}
                    </div>
                    <div className="mt-1 font-mono text-[10px] text-muted/60">
                      {formatTime(message.created_at)}
                    </div>
                  </div>
                </div>
              );
            })
          )}

          {streaming && (
            <div className="flex gap-2.5">
              <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-accent/15 text-[10px] font-medium text-accent">
                AI
              </div>
              <div className="min-w-0 max-w-[86%] flex-1">
                <div className="rounded-lg border border-accent/25 bg-[#0d131a] px-3 py-2">
                  {answer ? (
                    <div className="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-fg/95">
                      {answer}
                      <span className="ml-0.5 inline-block h-3.5 w-1.5 translate-y-0.5 animate-pulse bg-accent" />
                    </div>
                  ) : (
                    <LoadingDots label="智能体正在推理…" />
                  )}
                  <TraceTimeline entries={trace} />
                  <Citations items={citations} />
                  {usage && <UsageBar usage={usage} />}
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="border-t border-line-soft p-3">
          <div className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  void send();
                }
              }}
              rows={2}
              placeholder="输入问题，Enter 发送 / Shift+Enter 换行"
              className={cn(inputCls, 'h-auto resize-none py-2 leading-relaxed')}
            />
            <Button variant="primary" onClick={() => void send()} loading={streaming} disabled={!input.trim()}>
              发送
            </Button>
          </div>
          <div className="mt-1.5 flex items-center justify-between text-[10px] text-muted/70">
            <span>走 /api/chat/stream，逐 token 渲染，轨迹来自 plan / tool_call / tool_result 事件</span>
            <span className="font-mono">{input.length} 字</span>
          </div>
        </div>
      </section>
    </div>
  );
}
