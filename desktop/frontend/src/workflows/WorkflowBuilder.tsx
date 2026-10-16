import { useCallback, useEffect, useRef, useState } from 'react';
import { addEdge, duplicateNodes, emptyWorkflow, makeNode, nodeTypes, removeNodes } from './model';
import type {
  NodeType,
  SavedWorkflow,
  Template,
  WorkflowCatalog,
  WorkflowDefinition,
  WorkflowVersion,
} from './types';
import WorkflowCanvas from './WorkflowCanvas';
import NodeInspector from './NodeInspector';
import RunPanel from './RunPanel';
import WorkflowInputs from './WorkflowInputs';
import { nodeTraceEvents } from './trace-links';
import type { TraceEvent } from '../trace/types';
import { useWorkflowRun } from './useWorkflowRun';
import './workflows.css';

const blankCatalog: WorkflowCatalog = { tools: [], mcp_tools: [], skills: [] };
export default function WorkflowBuilder({
  conversationId,
  visible,
}: {
  conversationId: string | null;
  visible: boolean;
}) {
  const [graph, setGraph] = useState(emptyWorkflow);
  const [revision, setRevision] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [list, setList] = useState<SavedWorkflow[]>([]);
  const [versions, setVersions] = useState<WorkflowVersion[]>([]);
  const [versionId, setVersionId] = useState('');
  const [snapshot, setSnapshot] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [catalog, setCatalog] = useState(blankCatalog);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [search, setSearch] = useState('');
  const [inputText, setInputText] = useState('{}');
  const [parameters, setParameters] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [trace, setTrace] = useState<unknown[] | null>(null);
  const past = useRef<WorkflowDefinition[]>([]),
    future = useRef<WorkflowDefinition[]>([]);
  const clipboard = useRef<string[]>([]);
  const state = useRef({ graph, snapshot, dirty, selected, busy });
  state.current = { graph, snapshot, dirty, selected, busy };
  const report = useCallback((message: string) => setError(message), []);
  const run = useWorkflowRun(conversationId, visible, report);
  const api = window.desktopAPI;
  const read = api?.readWorkflows;
  const runWork = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError('');
    try {
      await operation();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  function change(next: WorkflowDefinition) {
    if (state.current.snapshot || state.current.busy) return;
    past.current = [...past.current.slice(-49), state.current.graph];
    future.current = [];
    state.current.graph = next;
    setGraph(next);
    setDirty(true);
  }
  function undo() {
    if (state.current.snapshot || state.current.busy || !past.current.length) return;
    const previous = past.current.pop()!;
    future.current.push(state.current.graph);
    setGraph(previous);
    setDirty(true);
  }
  function redo() {
    if (state.current.snapshot || state.current.busy || !future.current.length) return;
    const next = future.current.pop()!;
    past.current.push(state.current.graph);
    setGraph(next);
    setDirty(true);
  }
  const commands = useRef({ undo, redo, change });
  commands.current = { undo, redo, change };
  useEffect(() => {
    function keyboard(e: KeyboardEvent) {
      if (
        !visible ||
        !document.activeElement?.closest('.workflow-builder') ||
        /INPUT|TEXTAREA|SELECT/.test((e.target as HTMLElement)?.tagName)
      )
        return;
      if ((e.ctrlKey || e.metaKey) && ['z', 'y', 'c', 'v'].includes(e.key.toLowerCase())) {
        e.preventDefault();
        const key = e.key.toLowerCase();
        if (key === 'z') e.shiftKey ? commands.current.redo() : commands.current.undo();
        if (key === 'y') commands.current.redo();
        if (key === 'c') clipboard.current = [...state.current.selected];
        if (key === 'v') {
          try {
            commands.current.change(duplicateNodes(state.current.graph, clipboard.current));
          } catch (error) {
            setError(String(error));
          }
        }
      } else if (['Delete', 'Backspace'].includes(e.key) && state.current.selected.length) {
        e.preventDefault();
        commands.current.change(removeNodes(state.current.graph, state.current.selected));
        setSelected([]);
      }
    }
    window.addEventListener('keydown', keyboard);
    return () => window.removeEventListener('keydown', keyboard);
  }, [visible]);
  const refreshList = useCallback(async () => {
    if (!read) return;
    const [saved, capabilities, examples] = await Promise.all([
      read('list'),
      read('catalog'),
      read('templates'),
    ]);
    setList(saved.items);
    setCatalog(capabilities);
    setTemplates(examples.items);
  }, [read]);
  useEffect(() => {
    if (visible) void refreshList().catch((e) => setError(String(e)));
  }, [visible, refreshList]);
  async function discard() {
    if (!dirty) return true;
    const result = (await api?.controlWorkflows('confirm-discard')) as
      { discard: boolean } | undefined;
    return result?.discard === true;
  }
  function load(definition: WorkflowDefinition, rev = 0, historical = false) {
    setGraph(definition);
    setRevision(rev);
    setDirty(false);
    setSnapshot(historical);
    setSelected([]);
    past.current = [];
    future.current = [];
    setParameters('');
    setTrace(null);
  }
  async function open(id: string) {
    if (!read || !(await discard())) return;
    const saved = await read('detail', { workflow_id: id });
    load(saved.definition, saved.revision);
    const published = await read('versions', { workflow_id: id });
    setVersions(published.items);
    setVersionId(published.items[0]?.version_id || '');
  }
  async function save() {
    if (!api || snapshot) return;
    const saved = (await api.controlWorkflows('save', {
      definition: graph,
      revision,
    })) as SavedWorkflow;
    setRevision(saved.revision);
    if (state.current.graph === graph) {
      setGraph(saved.definition);
      setDirty(false);
    }
    setNotice('草稿已保存。');
    await refreshList();
    return saved;
  }
  async function publish() {
    const saved = await save();
    if (!saved || !api) return;
    const result = (await api.controlWorkflows('publish', {
      definition: saved.definition,
      revision: saved.revision,
    })) as { version_id: string; revision: number };
    const current = await read!('detail', { workflow_id: graph.workflow_id });
    load(current.definition, current.revision);
    setVersionId(result.version_id);
    setVersions((await read!('versions', { workflow_id: graph.workflow_id })).items);
    setNotice('已发布本地不可变版本；尚未运行。');
    await refreshList();
  }
  function add(
    type: NodeType,
    position = {
      x: 70 + (graph.nodes.length % 5) * 240,
      y: 70 + Math.floor(graph.nodes.length / 5) * 170,
    },
  ) {
    if (graph.nodes.length >= 100) {
      setError('节点上限为 100。');
      return;
    }
    const node = makeNode(type, position);
    let next = { ...graph, nodes: [...graph.nodes, node] };
    if (type === 'input')
      next = {
        ...next,
        input_schema: {
          ...graph.input_schema,
          properties: {
            ...((graph.input_schema.properties as Record<string, unknown>) || {}),
            text: { type: 'string', maxLength: 4000 },
          },
        },
      };
    change(next);
    setSelected([node.node_id]);
  }
  function connect(a: string, port: string, b: string, input?: string) {
    try {
      change(addEdge(state.current.graph, a, port, b, input));
      setNotice('已连接节点。');
    } catch (e) {
      setError(String(e));
    }
  }
  async function selectRun(id: string, discardApproved = false) {
    if (!discardApproved && !(await discard())) return;
    const result = await run.select(id);
    if (result && read) {
      const version = await read('version', { version_id: result.version_id });
      load(version, revision, true);
      setVersionId(result.version_id);
    }
  }
  async function showTrace(nodeId?: string) {
    if (!run.run?.trace_id || !api || !conversationId) return;
    const record = run.run.nodes.find((n) => n.node_id === nodeId);
    const traceId = record?.trace_id || run.run.trace_id;
    const events: TraceEvent[] = [];
    let after = 0;
    for (let page = 0; page < 20; page++) {
      const result = await api.readAgentTraces('events', {
        trace_id: traceId,
        conversation_id: conversationId,
        limit: 250,
        after,
      });
      events.push(...result.items);
      if (!result.has_more || result.next_cursor === null || result.next_cursor <= after) break;
      after = result.next_cursor;
    }
    const matches = nodeTraceEvents(events, record?.node_execution_id);
    setTrace(matches.slice(0, 500));
    setNotice(
      matches.length
        ? '只读真实执行段 Trace，不会重放节点。此处最多显示 500 条，完整回放可在 Agent Trace 模块查看。'
        : '该节点没有已记录轨迹；尚未执行、历史不完整或追踪不可用。',
    );
  }
  const selectedNode = graph.nodes.find((n) => n.node_id === selected[0]) || null;
  const disconnected = graph.nodes.filter(
    (n) => n.type !== 'manual_trigger' && !graph.edges.some((e) => e.target_node_id === n.node_id),
  );
  if (!api) return <p>工作流需要桌面主进程提供受控权限入口。</p>;
  return (
    <section
      className="workflow-builder"
      aria-label="可视化工作流编辑器"
      aria-busy={busy}
      onPointerDown={(e) => e.stopPropagation()}
    >
      <header className="wf-toolbar">
        <strong>可视化工作流</strong>
        <select
          aria-label="选择已保存工作流"
          value={
            list.some((s) => s.definition.workflow_id === graph.workflow_id)
              ? graph.workflow_id
              : ''
          }
          disabled={busy}
          onChange={(e) => e.target.value && void runWork(() => open(e.target.value))}
        >
          <option value="">未保存的草稿</option>
          {list.map((s) => (
            <option key={s.definition.workflow_id} value={s.definition.workflow_id}>
              {s.definition.name}
            </option>
          ))}
        </select>
        <button
          onClick={() =>
            void runWork(async () => {
              if (await discard()) {
                load(emptyWorkflow());
                setVersions([]);
                setVersionId('');
              }
            })
          }
        >
          新建
        </button>
        <button
          disabled={snapshot || busy}
          onClick={() =>
            void runWork(async () => {
              await save();
            })
          }
        >
          保存草稿
        </button>
        <button
          disabled={busy}
          onClick={() =>
            void runWork(async () => {
              await api.controlWorkflows('validate', { definition: graph, revision });
              setNotice('图校验通过。');
            })
          }
        >
          校验
        </button>
        <button disabled={snapshot || busy} onClick={() => void runWork(publish)}>
          发布版本
        </button>
        <span className="wf-dirty">
          {snapshot
            ? '只读版本快照'
            : dirty
              ? '● 尚未保存'
              : graph.status === 'ARCHIVED'
                ? '已归档 · 保存可重新启用'
                : '草稿'}
        </span>
      </header>
      <div className="wf-secondary">
        <label>
          名称
          <input
            aria-label="工作流名称"
            maxLength={100}
            disabled={snapshot || busy}
            value={graph.name}
            onChange={(e) => change({ ...graph, name: e.target.value })}
          />
        </label>
        <select
          aria-label="工作流版本"
          value={versionId}
          onChange={(e) => {
            setVersionId(e.target.value);
          }}
        >
          <option value="">请先发布本地版本</option>
          {versions.map((v) => (
            <option key={v.version_id} value={v.version_id}>
              v{v.version} · {v.digest.slice(0, 8)}
            </option>
          ))}
        </select>
        <button
          disabled={!versionId || busy}
          onClick={() =>
            void runWork(async () => {
              if (!(await discard())) return;
              load(await read!('version', { version_id: versionId }), revision, true);
            })
          }
        >
          查看版本
        </button>
        <button
          disabled={!revision || busy}
          onClick={() => void runWork(() => open(graph.workflow_id))}
        >
          编辑草稿
        </button>
        <button disabled={snapshot || !past.current.length} onClick={undo}>
          撤销
        </button>
        <button disabled={snapshot || !future.current.length} onClick={redo}>
          重做
        </button>
        <button
          disabled={snapshot || !selected.length}
          onClick={() => {
            change(duplicateNodes(graph, selected));
          }}
        >
          复制节点
        </button>
        <button
          disabled={snapshot || !selected.length}
          onClick={() => {
            change(removeNodes(graph, selected));
            setSelected([]);
          }}
        >
          删除节点
        </button>
      </div>
      {(error || notice || disconnected.length > 0) && (
        <div className="wf-feedback" aria-live="polite">
          {error ? <p role="alert">{error}</p> : <p>{notice}</p>}
          {disconnected.length > 0 && (
            <small>{disconnected.length} 个节点尚无输入连线；运行前须校验。</small>
          )}
        </div>
      )}
      <div className="wf-editor-grid">
        <aside className="wf-palette">
          <input
            aria-label="搜索工作流节点"
            placeholder="搜索节点…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {['输入与输出', 'Agent 与工具', '控制与数据'].map((group) => (
            <section key={group}>
              <h4>{group}</h4>
              {nodeTypes
                .filter(
                  (t) =>
                    t.group === group &&
                    (t.title + t.type + t.help).toLowerCase().includes(search.toLowerCase()),
                )
                .map((t) => (
                  <button
                    draggable={!snapshot && !busy}
                    disabled={snapshot || busy}
                    key={t.type}
                    title={t.help}
                    data-add-node={t.type}
                    onDragStart={(e) =>
                      e.dataTransfer.setData('application/studio-workflow', t.type)
                    }
                    onClick={() => add(t.type)}
                  >
                    {t.title}
                  </button>
                ))}
            </section>
          ))}
          <details>
            <summary>MIT 模板</summary>
            {templates.map((t) => (
              <button
                key={t.template_id}
                title={t.description}
                onClick={() =>
                  void runWork(async () => {
                    if (await discard()) {
                      load(await read!('template', { template_id: t.template_id }));
                      setDirty(true);
                      setVersionId('');
                      setVersions([]);
                      setNotice(t.description);
                    }
                  })
                }
              >
                {t.name}
              </button>
            ))}
          </details>
          <button onClick={() => void runWork(refreshList)}>刷新工具 / Skills</button>
        </aside>
        <WorkflowCanvas
          graph={graph}
          selected={selected}
          onSelect={setSelected}
          onChange={change}
          onConnect={connect}
          onAdd={add}
          onRemoveEdge={(id) =>
            change({ ...graph, edges: graph.edges.filter((e) => e.edge_id !== id) })
          }
          records={snapshot ? run.run?.nodes || [] : []}
          readOnly={snapshot || busy}
        />
        <NodeInspector
          node={selectedNode}
          record={
            snapshot ? run.run?.nodes.find((n) => n.node_id === selectedNode?.node_id) : undefined
          }
          catalog={catalog}
          variables={[
            ...Object.keys((graph.input_schema.properties || {}) as object).map(
              (k) => 'inputs.' + k,
            ),
            ...graph.nodes
              .filter((n) => n.node_id !== selectedNode?.node_id)
              .map((n) => `nodes.${n.node_id}.output`),
          ]}
          readOnly={snapshot || busy}
          onApply={(node) =>
            change({
              ...graph,
              nodes: graph.nodes.map((n) => (n.node_id === node.node_id ? node : n)),
            })
          }
          onTrace={() => void runWork(() => showTrace(selectedNode?.node_id))}
        />
      </div>
      <details className="wf-definition-config">
        <summary>流程参数、预算与导入导出</summary>
        <p>
          输入表单基于 input_schema 校验；文件仅允许已选项目内的相对路径引用。预算超过上限会停止。
        </p>
        <textarea
          aria-label="流程参数 JSON"
          className="wf-json"
          rows={8}
          disabled={snapshot || busy}
          value={
            parameters ||
            JSON.stringify(
              {
                input_schema: graph.input_schema,
                output_schema: graph.output_schema,
                budget: graph.budget,
              },
              null,
              2,
            )
          }
          onChange={(e) => setParameters(e.target.value)}
        />
        <button
          disabled={snapshot || !parameters}
          onClick={() => {
            try {
              const value = JSON.parse(parameters);
              if (
                Object.keys(value).some(
                  (k) => !['input_schema', 'output_schema', 'budget'].includes(k),
                )
              )
                throw Error('只允许输入/输出 Schema 与预算。');
              change({ ...graph, ...value });
              setParameters('');
            } catch (e) {
              setError(String(e));
            }
          }}
        >
          应用流程参数
        </button>
        <button
          disabled={busy}
          onClick={() =>
            void runWork(async () => {
              if (!(await discard())) return;
              const saved = (await api.controlWorkflows('import')) as SavedWorkflow & {
                cancelled?: boolean;
              };
              if (!saved.cancelled) {
                load(saved.definition, saved.revision);
                await refreshList();
              }
            })
          }
        >
          导入 JSON 副本
        </button>
        <button
          disabled={!revision || dirty || busy}
          onClick={() =>
            void runWork(async () => {
              const file = (await api.controlWorkflows('export', {
                workflow_id: graph.workflow_id,
              })) as { filename: string };
              setNotice('已导出到应用 Exports/Workflows：' + file.filename);
            })
          }
        >
          导出已保存草稿
        </button>
        <button
          disabled={!revision || busy || snapshot || dirty || graph.status === 'ARCHIVED'}
          onClick={() =>
            void runWork(async () => {
              const saved = (await api.controlWorkflows('archive', {
                workflow_id: graph.workflow_id,
                revision,
              })) as SavedWorkflow;
              load(saved.definition, saved.revision);
              await refreshList();
              setNotice('已归档，历史记录保留；保存草稿可重新启用。');
            })
          }
        >
          归档草稿
        </button>
        <button
          disabled={!revision || busy}
          onClick={() =>
            void runWork(async () => {
              await api.controlWorkflows('delete', { workflow_id: graph.workflow_id, revision });
              load(emptyWorkflow());
              setVersions([]);
              setVersionId('');
              await refreshList();
            })
          }
        >
          删除草稿
        </button>
      </details>
      <section className="wf-start">
        <WorkflowInputs graph={graph} value={inputText} onChange={setInputText} />
        <label>
          本次输入（JSON 对象）
          <textarea
            aria-label="工作流运行输入"
            rows={3}
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            spellCheck={false}
          />
        </label>
        <button
          className="wf-primary"
          disabled={!conversationId || !versionId || busy}
          onClick={() =>
            void runWork(async () => {
              if (!(await discard())) return;
              const inputs = JSON.parse(inputText);
              if (!inputs || typeof inputs !== 'object' || Array.isArray(inputs))
                throw Error('输入必须为 JSON 对象。');
              const result = (await api.controlWorkflows('start', {
                workflow_id: graph.workflow_id,
                version_id: versionId,
                conversation_id: conversationId,
                inputs,
              })) as { run_id: string };
              await selectRun(result.run_id, true);
              await run.reloadHistory();
            })
          }
        >
          运行所选版本
        </button>
        <small>图中声明的模型会使用当前 API 配置；运行前会显示概览，实际工具另行授权。</small>
      </section>
      {run.disconnected && (
        <p role="alert">
          事件订阅已断开。历史状态仍保存。
          <button onClick={() => void runWork(run.refresh)}>重新读取状态</button>
        </p>
      )}
      <RunPanel
        run={run.run}
        history={run.history}
        events={run.events}
        preview={run.preview}
        onSelect={(id) => void runWork(() => selectRun(id))}
        onAction={(action) => void runWork(() => run.action('control', { action }))}
        onPreview={() => void runWork(run.checkRecovery)}
        onResume={() =>
          void runWork(() => run.action('resume', { revision: run.preview?.revision }))
        }
        onApprove={(approval_id, decision) =>
          void runWork(() => run.action('approve', { approval_id, decision }))
        }
        onTrace={(id) => void runWork(() => showTrace(id))}
        onRefresh={() => void runWork(run.reloadHistory)}
      />
      {trace && (
        <details open className="wf-trace">
          <summary>节点关联 Trace（只读）</summary>
          <button onClick={() => setTrace(null)}>关闭</button>
          <pre>{JSON.stringify(trace, null, 2)}</pre>
        </details>
      )}
    </section>
  );
}
