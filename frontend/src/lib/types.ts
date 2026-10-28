/** 后端接口数据结构定义（与 agentforge/backend 的响应信封一一对应）。 */

export interface HealthInfo {
  status: string;
  app?: string;
  version?: string;
  env?: string;
  database: { ok: boolean; dialect?: string };
  vector_store: { ok: boolean; backend?: string; path?: string; error?: string };
  embedder: { provider: string; dim: number };
  llm: { provider: string; model: string; ready: boolean; mock: boolean };
}

export interface OverviewInfo {
  conversations: number;
  messages: number;
  agent_runs: number;
  runs_with_tools: number;
  avg_run_latency_ms: number;
  knowledge_bases: number;
  documents: number;
  chunks: number;
  datasets: number;
  dataset_items: number;
  tools: number;
  eval_runs: number;
  llm_mock_mode: boolean;
}

export interface SystemConfig {
  app_name?: string;
  version?: string;
  env?: string;
  llm: { provider: string; model: string; mock_mode: boolean; max_steps: number };
  rag: {
    chunk_size: number;
    chunk_overlap?: number;
    top_k: number;
    vector_weight: number;
    bm25_weight: number;
  };
  vector_store: { backend: string; path?: string };
}

export interface Citation {
  index: number;
  chunk_id: number;
  doc_name: string;
  score: number;
  snippet: string;
}

export interface ChatMessage {
  id: number;
  role: string;
  content: string;
  citations: Citation[] | null;
  token_in: number | null;
  token_out: number | null;
  created_at: string | null;
}

export interface ConversationBrief {
  id: number;
  title: string;
  message_count: number;
  created_at: string | null;
}

export interface PageData<T> {
  items: T[];
  total: number;
  page?: number;
  size?: number;
}

export interface KnowledgeBase {
  id: number;
  name: string;
  description: string | null;
  embed_provider: string;
  embed_dim: number;
  chunk_size: number;
  chunk_overlap: number;
  doc_count: number;
  chunk_count: number;
  status: string;
}

export interface KbDocument {
  id: number;
  filename: string;
  ext: string;
  size_bytes: number;
  status: string;
  chunk_count: number;
  char_count: number;
  error: string | null;
}

export interface DocChunk {
  id: number;
  seq: number;
  content: string;
  char_count: number;
}

export interface SearchHit {
  chunk_id: number;
  doc_name: string;
  content: string;
  score: number;
  vector_score: number;
  bm25_score: number;
  vector_rank: number | null;
  bm25_rank: number | null;
}

export interface SearchResult {
  query: string;
  hit_count: number;
  latency_ms: number;
  hits: SearchHit[];
}

export interface Dataset {
  id: number;
  name: string;
  task_type: string;
  status: string;
  item_count: number;
  passed_count: number;
}

export interface DatasetItem {
  id: number;
  status: string;
  split: string;
  raw: Record<string, unknown> | null;
  cleaned: Record<string, unknown> | null;
  label: unknown;
  quality_score: number | null;
  reject_reason: string | null;
}

export interface PipelineStats {
  total: number;
  by_status: Record<string, number>;
  pass_rate: number;
  avg_quality: number;
  intent_distribution: Record<string, number>;
}

export interface PipelineResult {
  clean?: { total: number; changed: number; dropped: number; drop_reasons: Record<string, number> };
  dedup?: { total: number; duplicates: number; kept: number };
  label?: { total: number; labeled: number; source: string };
  qc?: {
    total: number;
    passed: number;
    pass_rate: number;
    avg_score: number;
    rule_hits: Record<string, number>;
  };
  stats?: PipelineStats;
}

export interface QcRecord {
  id: number;
  item_id: number;
  rule: string;
  passed: boolean;
  severity: string;
  detail: string | null;
}

export interface ExportResult {
  version: string;
  count: number;
  size_bytes: number;
  path: string;
}

export interface DatasetVersion {
  version: string;
  item_count: number;
  pass_rate: number;
  export_path: string | null;
  created_at: string | null;
}

export interface ToolInfo {
  id: number | null;
  name: string;
  display_name: string;
  description: string;
  params_schema: unknown;
  category: string;
  handler?: string;
  timeout_s: number;
  enabled: boolean;
  call_count: number;
  success_count: number;
  tags: string[] | null;
}

export interface ToolInvokeResult {
  tool: string;
  ok: boolean;
  data: unknown;
  error: string | null;
  latency_ms: number;
  retries: number;
  observation: string;
}

export interface ToolInvocation {
  id: number;
  run_id: number | null;
  tool_name: string;
  args: unknown;
  result: unknown;
  status: string;
  error: string | null;
  latency_ms: number;
  retries: number;
  created_at: string | null;
}

export interface EvalSuite {
  id: number;
  name: string;
  type: string;
  description: string | null;
  case_count: number;
  created_at?: string | null;
}

export interface EvalCase {
  id: number;
  suite_id: number;
  input: string;
  expected: string | null;
  expect_tools?: string[] | null;
  meta?: Record<string, unknown> | null;
}

export interface EvalMetrics {
  total?: number;
  passed?: number;
  pass_rate?: number;
  hit_rate?: number;
  hit_rate_top1?: number;
  mrr?: number;
  latency_avg_ms?: number;
  latency_p95_ms?: number;
}

export interface EvalRunResult {
  run_id: number;
  suite_id?: number;
  suite_type?: string;
  status: string;
  metrics: EvalMetrics;
  case_total: number;
  case_passed: number;
}

export interface EvalRunBrief {
  run_id: number;
  suite_id: number;
  status: string;
  metrics: EvalMetrics | null;
  case_total: number;
  case_passed: number;
  latency_p95_ms?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface EvalFailure {
  case_id?: number;
  input?: string;
  expected?: string | null;
  reason?: string | null;
  [key: string]: unknown;
}

export interface EvalReport {
  metrics: EvalMetrics;
  case_total: number;
  case_passed: number;
  latency_p95_ms?: number;
  failures: EvalFailure[];
}
