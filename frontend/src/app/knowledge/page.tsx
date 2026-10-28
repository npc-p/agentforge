'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Badge, { statusTone } from '@/components/Badge';
import Banner from '@/components/Banner';
import Button from '@/components/Button';
import Card from '@/components/Card';
import EmptyState from '@/components/EmptyState';
import LoadingDots from '@/components/LoadingDots';
import PageHeader from '@/components/PageHeader';
import { apiDelete, apiGet, apiPost, apiUpload } from '@/lib/api';
import { cn } from '@/lib/cn';
import { formatBytes, formatNumber, toMessage } from '@/lib/format';
import { inputCls, labelCls, monoCls, tableCls, tableWrapCls, tdCls, thCls } from '@/lib/ui';
import type { DocChunk, KbDocument, KnowledgeBase, SearchResult } from '@/lib/types';

export default function KnowledgePage() {
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [basesLoading, setBasesLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [docs, setDocs] = useState<KbDocument[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [openDocId, setOpenDocId] = useState<number | null>(null);
  const [chunks, setChunks] = useState<DocChunk[]>([]);
  const [chunksLoading, setChunksLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [chunkSize, setChunkSize] = useState('800');
  const [chunkOverlap, setChunkOverlap] = useState('120');

  const [query, setQuery] = useState('');
  const [topK, setTopK] = useState('5');
  const [searching, setSearching] = useState(false);
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null);

  const fileRef = useRef<HTMLInputElement | null>(null);

  const loadBases = useCallback(async (keepSelection = true) => {
    setBasesLoading(true);
    try {
      const data = await apiGet<KnowledgeBase[]>('/knowledge/bases');
      const list = Array.isArray(data) ? data : [];
      setBases(list);
      setSelectedId((prev) => {
        if (keepSelection && prev !== null && list.some((item) => item.id === prev)) return prev;
        return list.length > 0 ? list[0].id : null;
      });
    } catch (err) {
      setError(toMessage(err, '加载知识库失败'));
    } finally {
      setBasesLoading(false);
    }
  }, []);

  const loadDocs = useCallback(async (baseId: number) => {
    setDocsLoading(true);
    try {
      const data = await apiGet<KbDocument[]>(`/knowledge/bases/${baseId}/documents`);
      setDocs(Array.isArray(data) ? data : []);
    } catch (err) {
      setDocs([]);
      setError(toMessage(err, '加载文档失败'));
    } finally {
      setDocsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadBases(false);
  }, [loadBases]);

  useEffect(() => {
    if (selectedId === null) {
      setDocs([]);
      return;
    }
    setOpenDocId(null);
    setChunks([]);
    setSearchResult(null);
    void loadDocs(selectedId);
  }, [selectedId, loadDocs]);

  const createBase = async () => {
    if (!name.trim()) {
      setError('请填写知识库名称');
      return;
    }
    setBusy('create');
    setError(null);
    setNotice(null);
    try {
      const created = await apiPost<{ id: number; name: string }>('/knowledge/bases', {
        name: name.trim(),
        description: description.trim(),
        chunk_size: Number(chunkSize) || 800,
        chunk_overlap: Number(chunkOverlap) || 0,
      });
      setName('');
      setDescription('');
      setNotice(`知识库「${created.name}」创建成功`);
      await loadBases(false);
      setSelectedId(created.id);
    } catch (err) {
      setError(toMessage(err, '创建知识库失败'));
    } finally {
      setBusy(null);
    }
  };

  const removeBase = async (base: KnowledgeBase) => {
    if (!window.confirm(`确认删除知识库「${base.name}」及其下的文档与切片？`)) return;
    setBusy(`del-base-${base.id}`);
    setError(null);
    try {
      await apiDelete(`/knowledge/bases/${base.id}`);
      setNotice(`已删除知识库「${base.name}」`);
      await loadBases(false);
    } catch (err) {
      setError(toMessage(err, '删除知识库失败'));
    } finally {
      setBusy(null);
    }
  };

  const upload = async (file: File) => {
    if (selectedId === null) return;
    setBusy('upload');
    setError(null);
    setNotice(null);
    try {
      await apiUpload(`/knowledge/bases/${selectedId}/upload`, file);
      setNotice(`已上传「${file.name}」，服务端正在解析与切片`);
      await loadDocs(selectedId);
      await loadBases();
    } catch (err) {
      setError(toMessage(err, '上传失败'));
    } finally {
      setBusy(null);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const removeDoc = async (doc: KbDocument) => {
    if (!window.confirm(`确认删除文档「${doc.filename}」？`)) return;
    setBusy(`del-doc-${doc.id}`);
    setError(null);
    try {
      await apiDelete(`/knowledge/documents/${doc.id}`);
      if (openDocId === doc.id) {
        setOpenDocId(null);
        setChunks([]);
      }
      if (selectedId !== null) await loadDocs(selectedId);
      await loadBases();
    } catch (err) {
      setError(toMessage(err, '删除文档失败'));
    } finally {
      setBusy(null);
    }
  };

  const toggleChunks = async (doc: KbDocument) => {
    if (openDocId === doc.id) {
      setOpenDocId(null);
      setChunks([]);
      return;
    }
    setOpenDocId(doc.id);
    setChunksLoading(true);
    setChunks([]);
    try {
      const data = await apiGet<DocChunk[]>(`/knowledge/documents/${doc.id}/chunks`);
      setChunks(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(toMessage(err, '加载切片失败'));
    } finally {
      setChunksLoading(false);
    }
  };

  const runSearch = async () => {
    if (selectedId === null) return;
    if (!query.trim()) {
      setError('请输入检索关键词');
      return;
    }
    setSearching(true);
    setError(null);
    try {
      const data = await apiPost<SearchResult>('/knowledge/search', {
        query: query.trim(),
        kb_id: selectedId,
        top_k: Number(topK) || 5,
      });
      setSearchResult(data);
    } catch (err) {
      setSearchResult(null);
      setError(toMessage(err, '检索失败'));
    } finally {
      setSearching(false);
    }
  };

  const selectedBase = bases.find((item) => item.id === selectedId) ?? null;

  return (
    <div className="mx-auto max-w-[1180px] space-y-4">
      <PageHeader
        title="知识库"
        description="管理知识库、上传文档、查看切片，并用混合检索（向量 + BM25）调试召回效果。"
        actions={
          <Button size="sm" variant="secondary" onClick={() => void loadBases()} loading={basesLoading}>
            刷新
          </Button>
        }
      />

      {error && <Banner tone="error" onClose={() => setError(null)}>{error}</Banner>}
      {notice && <Banner tone="success" onClose={() => setNotice(null)}>{notice}</Banner>}

      <Card title="新建知识库" subtitle="切片大小与重叠决定后续文档的切分策略">
        <div className="grid gap-3 md:grid-cols-[1.1fr_1.6fr_auto_auto_auto] md:items-end">
          <div>
            <label className={labelCls} htmlFor="kb-name">名称</label>
            <input
              id="kb-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：产品手册"
              className={inputCls}
            />
          </div>
          <div>
            <label className={labelCls} htmlFor="kb-desc">描述</label>
            <input
              id="kb-desc"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="可选，用于说明知识库用途"
              className={inputCls}
            />
          </div>
          <div className="w-[110px]">
            <label className={labelCls} htmlFor="kb-size">切片大小</label>
            <input
              id="kb-size"
              value={chunkSize}
              onChange={(event) => setChunkSize(event.target.value)}
              inputMode="numeric"
              className={inputCls}
            />
          </div>
          <div className="w-[110px]">
            <label className={labelCls} htmlFor="kb-overlap">重叠字符</label>
            <input
              id="kb-overlap"
              value={chunkOverlap}
              onChange={(event) => setChunkOverlap(event.target.value)}
              inputMode="numeric"
              className={inputCls}
            />
          </div>
          <Button variant="primary" onClick={() => void createBase()} loading={busy === 'create'}>
            创建
          </Button>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        <Card title="知识库列表" dense className="h-fit">
          {basesLoading ? (
            <LoadingDots className="px-1 py-3" label="加载中" />
          ) : bases.length === 0 ? (
            <EmptyState compact title="还没有知识库" description="在上方表单创建第一个知识库。" />
          ) : (
            <ul className="space-y-1">
              {bases.map((base) => (
                <li key={base.id}>
                  <div
                    className={cn(
                      'group rounded-lg border px-2.5 py-2 transition-colors',
                      base.id === selectedId
                        ? 'border-accent/40 bg-accent/10'
                        : 'border-transparent hover:border-line hover:bg-panel-soft',
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => setSelectedId(base.id)}
                      className="w-full text-left"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-[12px] text-fg">{base.name}</span>
                        <Badge tone={statusTone(base.status)}>{base.status || 'ready'}</Badge>
                      </div>
                      {base.description && (
                        <p className="mt-0.5 line-clamp-1 text-[10px] text-muted">{base.description}</p>
                      )}
                      <div className="mt-1 flex gap-2 font-mono text-[10px] text-muted/80">
                        <span>{base.doc_count} 文档</span>
                        <span>{base.chunk_count} 切片</span>
                        <span>{base.embed_dim} 维</span>
                      </div>
                    </button>
                    <div className="mt-1 flex justify-end">
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-6 px-1.5 text-[10px] hover:text-bad"
                        loading={busy === `del-base-${base.id}`}
                        onClick={() => void removeBase(base)}
                      >
                        删除
                      </Button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className="space-y-4">
          {selectedBase === null ? (
            <Card>
              <EmptyState title="请选择一个知识库" description="左侧列表中选择后，可上传文档并调试检索。" />
            </Card>
          ) : (
            <>
              <Card
                title={`文档 · ${selectedBase.name}`}
                subtitle={`切片 ${selectedBase.chunk_size} 字符 / 重叠 ${selectedBase.chunk_overlap} 字符`}
                actions={
                  <>
                    <input
                      ref={fileRef}
                      type="file"
                      className="hidden"
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (file) void upload(file);
                      }}
                    />
                    <Button
                      size="sm"
                      variant="primary"
                      loading={busy === 'upload'}
                      onClick={() => fileRef.current?.click()}
                    >
                      上传文件
                    </Button>
                  </>
                }
              >
                {docsLoading ? (
                  <LoadingDots label="加载文档" />
                ) : docs.length === 0 ? (
                  <EmptyState
                    compact
                    title="暂无文档"
                    description="支持 PDF / Word / Markdown / TXT 等格式，上传后自动解析并切片。"
                  />
                ) : (
                  <div className={tableWrapCls}>
                    <table className={tableCls}>
                      <thead>
                        <tr>
                          <th className={thCls}>文件</th>
                          <th className={thCls}>状态</th>
                          <th className={thCls}>切片</th>
                          <th className={thCls}>字符</th>
                          <th className={thCls}>大小</th>
                          <th className={thCls}>操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {docs.map((doc) => (
                          <tr key={doc.id} className="hover:bg-panel-soft/40">
                            <td className={tdCls}>
                              <div className="max-w-[260px] truncate text-[12px]">{doc.filename}</div>
                              <div className="mt-0.5 flex items-center gap-1.5 font-mono text-[10px] text-muted">
                                <span>#{doc.id}</span>
                                <span>{doc.ext}</span>
                              </div>
                              {doc.status === 'failed' && doc.error && (
                                <div className="mt-1 max-w-[280px] break-words text-[10px] text-bad">
                                  {doc.error}
                                </div>
                              )}
                            </td>
                            <td className={tdCls}>
                              <Badge tone={statusTone(doc.status)}>{doc.status}</Badge>
                            </td>
                            <td className={cn(tdCls, 'font-mono')}>{formatNumber(doc.chunk_count)}</td>
                            <td className={cn(tdCls, 'font-mono')}>{formatNumber(doc.char_count)}</td>
                            <td className={cn(tdCls, 'font-mono')}>{formatBytes(doc.size_bytes)}</td>
                            <td className={tdCls}>
                              <div className="flex gap-1">
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  className="h-6 px-1.5 text-[10px]"
                                  onClick={() => void toggleChunks(doc)}
                                >
                                  {openDocId === doc.id ? '收起切片' : '查看切片'}
                                </Button>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  className="h-6 px-1.5 text-[10px] hover:text-bad"
                                  loading={busy === `del-doc-${doc.id}`}
                                  onClick={() => void removeDoc(doc)}
                                >
                                  删除
                                </Button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {openDocId !== null && (
                  <div className="mt-3 rounded-lg border border-line bg-[#0d131a]">
                    <div className="flex items-center justify-between border-b border-line-soft px-3 py-2">
                      <span className="text-[11px] text-muted">切片预览 · 文档 #{openDocId}</span>
                      <button
                        type="button"
                        className="text-[11px] text-muted hover:text-accent"
                        onClick={() => {
                          setOpenDocId(null);
                          setChunks([]);
                        }}
                      >
                        关闭
                      </button>
                    </div>
                    <div className="max-h-[320px] space-y-2 overflow-y-auto p-2.5">
                      {chunksLoading ? (
                        <LoadingDots label="加载切片" />
                      ) : chunks.length === 0 ? (
                        <div className="py-4 text-center text-[11px] text-muted">该文档暂无切片</div>
                      ) : (
                        chunks.map((chunk) => (
                          <div key={chunk.id} className="rounded-md border border-line-soft p-2">
                            <div className="mb-1 flex items-center gap-2 font-mono text-[10px] text-muted">
                              <span className="text-accent">seq {chunk.seq}</span>
                              <span>{chunk.char_count} 字符</span>
                              <span>#{chunk.id}</span>
                            </div>
                            <p className="whitespace-pre-wrap break-words text-[11px] leading-relaxed text-fg/85">
                              {chunk.content}
                            </p>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                )}
              </Card>

              <Card title="检索调试" subtitle="混合检索：向量召回 + BM25 关键词，返回两路排名以便对比">
                <div className="flex flex-wrap items-end gap-2">
                  <div className="min-w-[240px] flex-1">
                    <label className={labelCls} htmlFor="kb-query">检索问题</label>
                    <input
                      id="kb-query"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') void runSearch();
                      }}
                      placeholder="例如：如何重置管理员密码？"
                      className={inputCls}
                    />
                  </div>
                  <div className="w-[96px]">
                    <label className={labelCls} htmlFor="kb-topk">TopK</label>
                    <input
                      id="kb-topk"
                      value={topK}
                      onChange={(event) => setTopK(event.target.value)}
                      inputMode="numeric"
                      className={inputCls}
                    />
                  </div>
                  <Button variant="primary" onClick={() => void runSearch()} loading={searching}>
                    检索
                  </Button>
                </div>

                {searchResult && (
                  <div className="mt-3">
                    <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-muted">
                      <Badge tone={searchResult.hit_count > 0 ? 'ok' : 'warn'}>
                        命中 {searchResult.hit_count}
                      </Badge>
                      <span className="font-mono">{searchResult.latency_ms.toFixed(1)} ms</span>
                      <span className="truncate">query: {searchResult.query}</span>
                    </div>
                    {searchResult.hits.length === 0 ? (
                      <EmptyState compact title="没有命中片段" description="尝试更换问法或降低阈值。" />
                    ) : (
                      <ul className="space-y-2">
                        {searchResult.hits.map((hit, index) => (
                          <li key={hit.chunk_id} className="rounded-lg border border-line bg-[#0d131a] p-2.5">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span className="flex h-4 w-4 items-center justify-center rounded bg-accent/15 font-mono text-[10px] text-accent">
                                {index + 1}
                              </span>
                              <span className="max-w-[240px] truncate text-[12px] text-fg/90">
                                {hit.doc_name}
                              </span>
                              <Badge tone="info">score {hit.score.toFixed(4)}</Badge>
                              <span className={monoCls}>vec {hit.vector_score.toFixed(3)}</span>
                              <span className={monoCls}>bm25 {hit.bm25_score.toFixed(3)}</span>
                              <span className={monoCls}>
                                v_rank {hit.vector_rank ?? '-'} · b_rank {hit.bm25_rank ?? '-'}
                              </span>
                            </div>
                            <p className="mt-1.5 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-muted">
                              {hit.content}
                            </p>
                          </li>
                        ))}
                      </ul>
                    )}
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
