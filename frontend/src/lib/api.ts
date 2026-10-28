/**
 * 后端访问层。
 *
 * 约定：所有业务接口返回 {"code":0,"message":"ok","data":...,"trace_id":"..."}，
 * 这里统一解包 data，失败时抛出 ApiError（携带后端 message）。
 */

const BASE = '/api';

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(message: string, code = 'error', status = 0) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
  }
}

interface Envelope {
  code: number | string;
  message?: string;
  data?: unknown;
  trace_id?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isEnvelope(value: unknown): value is Envelope {
  if (!isRecord(value)) return false;
  return 'code' in value && ('data' in value || 'message' in value);
}

function readString(source: Record<string, unknown>, key: string): string | undefined {
  const value = source[key];
  return typeof value === 'string' ? value : undefined;
}

/** 从任意错误响应体中尽力提取可读信息。 */
function extractMessage(payload: unknown): string | undefined {
  if (typeof payload === 'string' && payload.trim() !== '') {
    return payload.length > 300 ? `${payload.slice(0, 300)}…` : payload;
  }
  if (!isRecord(payload)) return undefined;
  const direct = readString(payload, 'message') ?? readString(payload, 'detail');
  if (direct) return direct;
  const error = payload.error;
  if (typeof error === 'string') return error;
  const detail = payload.detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0];
    if (isRecord(first)) {
      return readString(first, 'msg') ?? '请求参数校验失败';
    }
  }
  return undefined;
}

function extractCode(payload: unknown, fallback: string): string {
  if (isRecord(payload)) {
    const code = payload.code;
    if (typeof code === 'string') return code;
    if (typeof code === 'number') return String(code);
  }
  return fallback;
}

async function parseBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...(init.headers ?? {}),
      },
      cache: 'no-store',
    });
  } catch {
    throw new ApiError('无法连接后端服务，请确认 agentforge 后端已在 8010 端口启动', 'network_error');
  }

  const payload = await parseBody(res);

  if (!res.ok) {
    throw new ApiError(
      extractMessage(payload) ?? `请求失败（HTTP ${res.status}）`,
      extractCode(payload, `http_${res.status}`),
      res.status,
    );
  }

  if (isEnvelope(payload)) {
    const code = payload.code;
    const success = code === 0 || code === '0';
    if (!success) {
      throw new ApiError(extractMessage(payload) ?? '业务处理失败', String(code), res.status);
    }
    return payload.data as T;
  }

  return payload as T;
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'GET' });
}

export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export function apiPatch<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'PATCH',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export function apiDelete<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'DELETE' });
}

export function apiUpload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append('file', file);
  return request<T>(path, { method: 'POST', body: form });
}

export interface ChatStreamPayload {
  query: string;
  conversation_id?: number | null;
  kb_id?: number | null;
}

export type StreamHandler = (event: string, data: unknown) => void;

/**
 * POST + fetch 手动解析 SSE（EventSource 只支持 GET，无法用于对话流）。
 * 解析规则遵循 SSE 规范：空行分派事件，`event:` 记录类型，`data:` 可多行拼接。
 */
export async function streamChat(
  payload: ChatStreamPayload,
  onEvent: StreamHandler,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify({ ...payload, stream: true }),
      signal,
    });
  } catch {
    throw new ApiError('无法连接后端服务，请确认 agentforge 后端已在 8010 端口启动', 'network_error');
  }

  if (!res.ok) {
    const body = await parseBody(res);
    throw new ApiError(
      extractMessage(body) ?? `流式请求失败（HTTP ${res.status}）`,
      extractCode(body, `http_${res.status}`),
      res.status,
    );
  }
  if (!res.body) {
    throw new ApiError('当前浏览器不支持流式响应', 'stream_unsupported');
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let eventType = 'message';
  let dataLines: string[] = [];

  const dispatch = () => {
    if (dataLines.length === 0) {
      eventType = 'message';
      return;
    }
    const raw = dataLines.join('\n');
    let parsed: unknown = raw;
    try {
      parsed = JSON.parse(raw) as unknown;
    } catch {
      parsed = raw;
    }
    const type = eventType;
    eventType = 'message';
    dataLines = [];
    onEvent(type, parsed);
  };

  const handleLine = (line: string) => {
    if (line === '') {
      dispatch();
      return;
    }
    if (line.startsWith(':')) return; // 心跳注释行
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') {
      eventType = value || 'message';
    } else if (field === 'data') {
      dataLines.push(value);
    }
  };

  try {
    for (;;) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (err) {
        // 用户主动停止不算断线；其余情况是网络中断或后端进程重启，
        // 向上抛出可识别的错误码，让界面能提示「已断线」而不是报一个笼统失败。
        if (signal?.aborted) throw err;
        throw new ApiError(
          '流式连接中断（网络抖动或后端重启），已接收的内容仍保留在页面上',
          'stream_interrupted',
        );
      }
      const { done, value } = chunk;
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline = buffer.indexOf('\n');
      while (newline !== -1) {
        const line = buffer.slice(0, newline).replace(/\r$/, '');
        buffer = buffer.slice(newline + 1);
        handleLine(line);
        newline = buffer.indexOf('\n');
      }
    }
    buffer += decoder.decode();
    if (buffer.trim() !== '') {
      handleLine(buffer.replace(/\r$/, ''));
      dispatch();
    }
  } finally {
    reader.releaseLock();
  }
}
