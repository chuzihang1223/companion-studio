import { useEffect, useState } from 'react';
import type { Collection, Job, Operation, Owner, SearchResult } from './types';
import { Sources } from './KnowledgeSources';
import MemoryPanel from './MemoryPanel';
import EmbeddingPanel from './EmbeddingPanel';
import './knowledge.css';

const states: Record<string, string> = {
  EMPTY: '尚无目录',
  NEEDS_INDEX: '需要索引',
  READY: '就绪',
  READY_WITH_ERRORS: '就绪，部分文档有错误',
  PENDING: '排队',
  SCANNING: '扫描授权目录',
  PARSING: '解析',
  CHUNKING: '分块',
  INDEXING: '写入索引',
  PAUSED: '已暂停',
  COMPLETED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
  NEEDS_REVIEW: '中断，需要重新核对',
};
export default function KnowledgePanel({
  conversationId,
  characterId,
  workSessionId,
  onBack,
}: {
  conversationId: string | null;
  characterId: string;
  workSessionId?: string;
  onBack?: () => void;
}) {
  const [collections, setCollections] = useState<Collection[]>([]),
    [selected, setSelected] = useState('');
  const [name, setName] = useState(''),
    [description, setDescription] = useState(''),
    [filters, setFilters] = useState('');
  const [selectedIds, setSelectedIds] = useState<string[]>([]),
    [rag, setRag] = useState(false),
    [memory, setMemory] = useState(true);
  const [jobs, setJobs] = useState<Job[]>([]),
    [query, setQuery] = useState(''),
    [result, setResult] = useState<SearchResult | null>(null);
  const [mode, setMode] = useState('keyword'),
    [tab, setTab] = useState('knowledge'),
    [busy, setBusy] = useState(false),
    [status, setStatus] = useState(''),
    [error, setError] = useState('');
  const current = collections.find((row) => row.id === selected);
  const owner: Owner | null = conversationId
    ? { conversation_id: conversationId, character_id: characterId }
    : null;
  async function refresh() {
    setCollections((await window.desktopAPI!.readKnowledge('collections')).collections);
  }
  useEffect(() => {
    let live = true;
    if (!window.desktopAPI) {
      setError('知识库需要在桌面程序中使用。');
      return;
    }
    void window.desktopAPI
      .readKnowledge('collections')
      .then((value) => {
        if (live) {
          setCollections(value.collections);
          setSelected((old) => old || value.collections[0]?.id || '');
        }
      })
      .catch((cause) => {
        if (live) setError(String(cause));
      });
    if (conversationId)
      void window.desktopAPI
        .readKnowledge('selection', { conversation_id: conversationId, character_id: characterId })
        .then((value) => {
          if (live) {
            setSelectedIds(value.collection_ids);
            setRag(value.preference.rag_enabled);
            setMemory(value.preference.memory_enabled);
          }
        })
        .catch((cause) => {
          if (live) setError(String(cause));
        });
    return () => {
      live = false;
    };
  }, [conversationId, characterId]);
  useEffect(() => {
    if (current) {
      setName(current.name);
      setDescription(current.description);
      setFilters(current.config.exclude_globs.join('\n'));
    }
  }, [current]);
  useEffect(() => {
    let live = true,
      timer: number | undefined;
    if (!selected || !window.desktopAPI) {
      setJobs([]);
      return;
    }
    const poll = async () => {
      try {
        const value = await window.desktopAPI!.readKnowledge('jobs', { collection_id: selected });
        if (live) {
          setJobs(value.jobs);
          if (
            value.jobs[0] &&
            ['PENDING', 'SCANNING', 'PARSING', 'CHUNKING', 'INDEXING', 'PAUSED'].includes(
              value.jobs[0].state,
            )
          )
            timer = window.setTimeout(() => void poll(), 1000);
        }
      } catch (cause) {
        if (live) setError(String(cause));
      }
    };
    void poll();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [selected, status]);
  async function action(
    operation: Operation,
    options: Record<string, unknown>,
    success = '已完成',
  ) {
    setBusy(true);
    setError('');
    try {
      const value = await window.desktopAPI!.controlKnowledge(operation, options);
      setStatus(success + ' · ' + new Date().toLocaleTimeString());
      await refresh();
      return value;
    } catch (cause) {
      setError(String(cause));
      return null;
    } finally {
      setBusy(false);
    }
  }
  async function savePreferences(nextRag: boolean, nextMemory: boolean) {
    if (
      await action(
        'preferences',
        { ...owner, rag_enabled: nextRag, memory_enabled: nextMemory },
        '会话设置已保存',
      )
    ) {
      setRag(nextRag);
      setMemory(nextMemory);
    }
  }
  return (
    <section className="knowledge-panel" aria-label="本地知识与记忆">
      <header>
        {onBack && <button onClick={onBack}>返回</button>}
        <h2>本地知识与记忆</h2>
      </header>
      <p>只索引你选择的目录。默认使用本地关键词；不会下载模型或上传文档。</p>
      <div className="knowledge-actions">
        <button onClick={() => setTab('knowledge')}>知识库与检索</button>
        <button onClick={() => setTab('memory')}>长期记忆</button>
      </div>
      {error && <p role="alert">{error}</p>}
      {status && <p role="status">{status}</p>}
      {tab === 'memory' ? (
        owner ? (
          <MemoryPanel key={owner.conversation_id} owner={owner} workSessionId={workSessionId} />
        ) : (
          <p>请先创建或打开会话，再管理记忆。</p>
        )
      ) : (
        <>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void action(
                'create',
                { payload: { name: name || '新知识库', description } },
                '知识库已创建',
              ).then((value) => {
                if (value) setSelected((value as Collection).id);
              });
            }}
            className="knowledge-create"
          >
            <input
              aria-label="知识库名称"
              value={name}
              maxLength={100}
              onChange={(event) => setName(event.target.value)}
              placeholder="知识库名称"
            />
            <input
              aria-label="知识库说明"
              value={description}
              maxLength={2000}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="说明（可选）"
            />
            <button disabled={busy}>新建知识库</button>
          </form>
          <label>
            管理知识库{' '}
            <select
              aria-label="管理知识库"
              value={selected}
              onChange={(event) => {
                setSelected(event.target.value);
                setResult(null);
              }}
            >
              {!selected && <option value="">请选择</option>}
              {collections.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
            </select>
          </label>
          {current && (
            <article className="knowledge-collection">
              <EmbeddingPanel key={current.id} collection={current} onChanged={refresh} />
              <h3>{current.name}</h3>
              <p>
                {states[current.status] || current.status} · {current.document_count} 个文档 ·{' '}
                {current.chunk_count} 个片段 · 索引版本 {current.index_version}
              </p>
              <p>
                格式：{current.config.allowed_types.join(' ')} · 单文件上限{' '}
                {Math.round(current.config.max_file_bytes / 1048576)} MiB
              </p>
              <ul>
                {current.source_roots.map((root) => (
                  <li key={root.id}>
                    <span>{root.path}</span>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void action(
                          'remove-root',
                          {
                            collection_id: current.id,
                            revision: current.revision,
                            root_id: root.id,
                          },
                          '目录授权已撤销',
                        )
                      }
                    >
                      移除目录
                    </button>
                  </li>
                ))}
              </ul>
              <div className="knowledge-actions">
                <button
                  disabled={busy}
                  onClick={() =>
                    void action(
                      'add-root',
                      { collection_id: current.id, revision: current.revision },
                      '目录选择已处理',
                    )
                  }
                >
                  选择并授权目录
                </button>
                <button
                  disabled={busy || !current.source_roots.length}
                  onClick={() =>
                    void action('index', { collection_id: current.id }, '增量索引已启动')
                  }
                >
                  增量索引
                </button>
                <button
                  disabled={busy || !current.source_roots.length}
                  onClick={() =>
                    void action('index', { collection_id: current.id, full: true }, '重建已启动')
                  }
                >
                  重建索引
                </button>
                <button
                  disabled={busy}
                  onClick={() =>
                    void action(
                      'delete',
                      { collection_id: current.id, revision: current.revision },
                      '本地索引已删除',
                    ).then(() => setSelected(''))
                  }
                >
                  删除知识库
                </button>
              </div>
              <details>
                <summary>目录过滤与知识库设置</summary>
                <p>
                  默认忽略：{current.config.excluded_names.join('、')}。以下为额外排除规则，每行一个
                  glob。
                </p>
                <textarea
                  aria-label="排除规则"
                  value={filters}
                  maxLength={2000}
                  onChange={(event) => setFilters(event.target.value)}
                />
                <button
                  disabled={busy}
                  onClick={() =>
                    void action(
                      'update',
                      {
                        collection_id: current.id,
                        revision: current.revision,
                        payload: {
                          name,
                          description,
                          config: {
                            ...current.config,
                            exclude_globs: filters
                              .split('\n')
                              .map((v) => v.trim())
                              .filter(Boolean),
                          },
                        },
                      },
                      '设置已保存，需要重新索引',
                    )
                  }
                >
                  保存名称、说明和规则
                </button>
              </details>
              {jobs[0] && (
                <div className="knowledge-job" role="status">
                  <strong>{states[jobs[0].state] || jobs[0].state}</strong>
                  <p>
                    处理 {jobs[0].progress.processed || 0} · 新增 {jobs[0].progress.new || 0} · 修改{' '}
                    {jobs[0].progress.modified || 0} · 未变 {jobs[0].progress.unchanged || 0} · 删除{' '}
                    {jobs[0].progress.deleted || 0} · 错误 {jobs[0].progress.errors || 0}
                  </p>
                  {jobs[0].last_error && <code>{jobs[0].last_error}</code>}
                  {['PENDING', 'SCANNING', 'PARSING', 'CHUNKING', 'INDEXING', 'PAUSED'].includes(
                    jobs[0].state,
                  ) && (
                    <div className="knowledge-actions">
                      {(['pause', 'continue', 'cancel'] as const).map((value) => (
                        <button
                          key={value}
                          disabled={busy}
                          onClick={() =>
                            void action('job-control', { job_id: jobs[0].id, action: value })
                          }
                        >
                          {value === 'pause' ? '暂停' : value === 'continue' ? '继续' : '取消'}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </article>
          )}
          {owner ? (
            <section className="knowledge-selection">
              <h3>当前会话授权</h3>
              <p>本地搜索不需要模型。发送检索片段前会显示目标服务，必须另外确认。</p>
              {collections.map((row) => (
                <label key={row.id}>
                  <input
                    type="checkbox"
                    checked={selectedIds.includes(row.id)}
                    onChange={(event) =>
                      setSelectedIds((old) =>
                        event.target.checked ? [...old, row.id] : old.filter((id) => id !== row.id),
                      )
                    }
                  />
                  {row.name}
                </label>
              ))}
              <div className="knowledge-actions">
                <button
                  disabled={busy}
                  onClick={() =>
                    void action(
                      'selection',
                      { ...owner, collection_ids: selectedIds, allow_model_transfer: false },
                      '已授权本地检索；模型传输授权已撤销',
                    )
                  }
                >
                  仅授权本地检索
                </button>
                <button
                  disabled={busy || !selectedIds.length}
                  onClick={() =>
                    void action(
                      'selection',
                      { ...owner, collection_ids: selectedIds, allow_model_transfer: true },
                      '会话授权已保存',
                    )
                  }
                >
                  授权检索片段给当前模型
                </button>
              </div>
              <label>
                <input
                  type="checkbox"
                  checked={rag}
                  disabled={busy}
                  onChange={(event) => void savePreferences(event.target.checked, memory)}
                />
                此会话启用知识库回答（RAG）
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={memory}
                  disabled={busy}
                  onChange={(event) => void savePreferences(rag, event.target.checked)}
                />
                使用主动保存的记忆
              </label>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void action(
                    'search',
                    { ...owner, search: { query, collection_ids: selectedIds, mode } },
                    '检索完成',
                  ).then((value) => setResult(value as SearchResult | null));
                }}
              >
                <input
                  aria-label="知识检索问题"
                  value={query}
                  maxLength={300}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="关键词，例如：安装、函数名或端口"
                />
                <select
                  aria-label="检索方式"
                  value={mode}
                  onChange={(event) => setMode(event.target.value)}
                >
                  <option value="keyword">关键词（本地）</option>
                  <option value="hybrid">混合（未配置向量时明确回退）</option>
                </select>
                <button disabled={busy || !query.trim() || !selectedIds.length}>
                  搜索授权知识库
                </button>
              </form>
              {result && (
                <>
                  <p>
                    {result.mode === 'keyword' ? '关键词' : '混合'} · {result.results.length} 条 ·{' '}
                    {result.duration_ms.toFixed(1)} ms
                  </p>
                  {result.warnings.map((warning) => (
                    <p key={warning}>{warning}</p>
                  ))}
                  {!result.results.length && <p>未找到可核验资料，无法据此确认。</p>}
                  <Sources rows={result.results} owner={owner} />
                </>
              )}
            </section>
          ) : (
            <p>请创建或打开会话后选择知识库、搜索和启用回答。</p>
          )}
        </>
      )}
    </section>
  );
}
