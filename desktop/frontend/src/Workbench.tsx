import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import WorkspaceBrowser from './WorkspaceBrowser';
import FilesModule from './FilesModule';
import { WorkspaceModule } from './WorkModule';
import { controlWorkSession, type AgentCall, type ChatMessage, type WorkSession } from './api';
import Icon from './Icon';
import { AgentOutputRouter } from './AgentOutputRouter';
import { WorkspaceModuleRegistry, type ModuleId } from './WorkspaceModuleRegistry';
import { useWorkbenchLayout } from './WorkbenchLayout';
import { DockTile } from './DockableWorkspaceModule';
import WorkspaceGrid from './WorkspaceGrid';
import { splitModule, openModule, type LayoutTemplate } from './WorkspaceLayoutEngine';
import { useModuleDrag } from './useModuleDrag';
import CodeModule from './CodeModule';
import DiffModule from './DiffModule';
import TerminalModule from './TerminalModule';
import MediaModule from './MediaModule';
import TraceViewer from './trace/TraceViewer';
import EvaluationDashboard from './evaluation/EvaluationDashboard';
import RecoveryPanel from './recovery/RecoveryPanel';
import WorkbenchExtensions from './extensions/WorkbenchExtensions';
import WorkbenchActionButton from './WorkbenchActionButton';
import './workbench.css';
import './workspace-grid.css';
const WorkflowBuilder = lazy(() => import('./workflows/WorkflowBuilder'));
const KnowledgePanel = lazy(() => import('./knowledge/KnowledgePanel'));
const VisualObserver = lazy(() => import('./computer/VisualObserver'));
const ScheduledTasks = lazy(() => import('./scheduling/ScheduledTasks'));

type Props = {
  characterId: string;
  visible: boolean;
  conversationId: string | null;
  session: WorkSession | null;
  pending: { calls: AgentCall[]; approved: string[] } | null;
  messages: ChatMessage[];
  onApproval: (id: string, allow: boolean) => void;
  onExecute: () => void;
  onStop: () => void;
  renderMessage: (message: ChatMessage) => ReactNode;
  onError: (message: string) => void;
  onStatus: (message: string) => void;
};
const statuses: Record<string, string> = {
  idle: '准备就绪',
  planning: '规划中',
  running: '执行中',
  executing: '执行中',
  observing: '观察中',
  verifying: '验证中',
  waiting_permission: '等待授权',
  waiting_user: '等待接管',
  paused: '已暂停',
  retrying: '重新尝试',
  summarizing: '整理结果',
  done: '已完成',
  completed: '已完成',
  failed: '未完成',
  stopped: '已停止',
  cancelled: '已取消',
};
const eventNames: Record<string, string> = {
  plan_updated: '任务计划',
  tool_started: '正在操作',
  tool_completed: '工具结果',
  step_started: '当前步骤',
  verification: '步骤验证',
  goal_verification: '目标验证',
  retrying: '重新观察',
  replanning: '调整计划',
  permission_requested: '请求授权',
  permission_resolved: '授权已处理',
  task_completed: '目标已完成',
  task_failed: '任务未完成',
  task_pause: '任务已暂停',
  task_resume: '继续执行',
  task_takeover: '等待你接管',
  task_cancel: '任务已取消',
  user_interruption: '操作已暂停',
  work_interrupted: '上次任务已停止',
  workflow_updated: '状态更新',
};
const tools: Record<string, string> = {
  'music.play': '搜索并播放歌曲',
  'music.search': '搜索歌曲',
  'coding.task': '修改项目并运行测试',
  'app.launch': '启动应用',
  'app.close': '关闭应用',
  'browser.search_site': '网页搜索',
  'system.get_status': '读取系统状态',
};
const ended = new Set(['idle', 'done', 'completed', 'failed', 'stopped', 'cancelled']);

/** 组合工作模块；工具结果独立展示，任务状态与布局分别按会话归属。 */
export default function Workbench({
  characterId,
  visible,
  conversationId,
  session,
  pending,
  messages,
  onApproval,
  onExecute,
  onStop,
  renderMessage,
  onError,
  onStatus,
}: Props) {
  const sessionKeys = useRef<{
    conversation: string | null;
    id: string | null;
    previous: string | null;
  }>({ conversation: null, id: null, previous: null });
  if (sessionKeys.current.conversation !== conversationId)
    sessionKeys.current = { conversation: conversationId, id: null, previous: null };
  if (session?.id && sessionKeys.current.id !== session.id) {
    sessionKeys.current.previous = sessionKeys.current.id;
    sessionKeys.current.id = session.id;
  }
  // 实际任务生成前使用 pending key；任务更替保留前一个 key 供布局回退。
  const layoutKey = sessionKeys.current.id || 'pending:' + (conversationId || 'new-work');
  const {
    layout,
    loaded,
    restored,
    saveError,
    setLayout,
    change,
    restore,
    reset,
    undo,
    undoCount,
    template,
    saveDefault,
  } = useWorkbenchLayout(layoutKey, sessionKeys.current.previous || conversationId || undefined);
  const [requestedUrl, setRequestedUrl] = useState(''),
    [allModules, setAllModules] = useState(false);
  const previous = useRef<{ id?: string; status?: string }>({}),
    seen = useRef<{ key: string; modules: Set<ModuleId> }>({ key: '', modules: new Set() }),
    end = useRef<HTMLDivElement>(null);
  const routed = useMemo(
    () => new AgentOutputRouter().route(session, messages),
    [session, messages],
  );
  const active = layout.active;
  const running = !!session && !ended.has(session.status),
    paused = ['paused', 'waiting_user'].includes(session?.status || ''),
    step = session?.steps.find((s) => s.status === 'active');
  // 自动开启只处理首次出现的输出模块；已有精确布局和用户最小化状态优先。
  // 依赖保留当前事件/输出数量触发方式，不将每次重新创建的回调加入其中。
  useEffect(() => {
    if (!loaded) return;
    if (seen.current.key !== layoutKey) {
      seen.current = {
        key: layoutKey,
        modules: new Set(restored ? routed.outputs.map((row) => row.module) : []),
      };
    }
    const fresh = [...new Set(routed.outputs.map((row) => row.module))].filter(
      (id) => !seen.current.modules.has(id),
    );
    fresh.forEach((id) => seen.current.modules.add(id));
    if (pending && !seen.current.modules.has('permissions')) {
      fresh.push('permissions');
      seen.current.modules.add('permissions');
    }
    if (fresh.length)
      setLayout(
        (old) =>
          fresh.reduce(
            (next, id) => (next.modules[id].minimized ? next : openModule(next, id, false)),
            old,
          ),
        false,
      );
    if (!session) return;
    previous.current = { id: session.id, status: session.status };
  }, [
    loaded,
    restored,
    layoutKey,
    session?.id,
    session?.status,
    session?.events.length,
    routed.outputs.length,
    pending,
    layout.locked,
  ]);
  useEffect(() => {
    if (saveError) onError(saveError);
  }, [saveError]);
  const dragging = useModuleDrag((id, target) =>
    setLayout(splitModule(layout, id, target.module, target.edge)),
  );
  useEffect(() => {
    const handler = (e: Event) => {
      setRequestedUrl((e as CustomEvent).detail.url);
      restore('browser');
    };
    window.addEventListener('companion-work-browser', handler);
    return () => window.removeEventListener('companion-work-browser', handler);
  }, []);
  useEffect(() => {
    if (
      active === 'conversation' &&
      visible &&
      !document.querySelector('.workspace-free.is-resizing')
    )
      end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, active, visible]);
  async function control(action: 'pause' | 'resume' | 'takeover') {
    if (!session) return;
    try {
      await controlWorkSession(session.id, action);
      onStatus(action === 'resume' ? '继续观察并执行' : '自动操作已暂停');
    } catch (e) {
      onError(String(e));
    }
  }
  function content(id: ModuleId, moduleVisible = true): ReactNode {
    if (id === 'scheduled_tasks')
      return <ScheduledTasks conversationId={conversationId} visible={visible && moduleVisible} />;
    if (id === 'visual_observer')
      return (
        <Suspense fallback={<p>正在打开屏幕观察…</p>}>
          <VisualObserver
            key={conversationId || 'none'}
            conversationId={conversationId}
            characterId={characterId}
            visible={visible && moduleVisible}
            onTrace={() => restore('trace')}
          />
        </Suspense>
      );
    if (id === 'knowledge')
      return (
        <Suspense fallback={<p>正在打开知识库…</p>}>
          <KnowledgePanel
            key={conversationId || 'none'}
            conversationId={conversationId}
            characterId={characterId}
            workSessionId={session?.workspace_root ? session.id : undefined}
          />
        </Suspense>
      );
    if (id === 'workflow_builder')
      return (
        <Suspense fallback={<p>正在打开流程设计器…</p>}>
          <WorkflowBuilder conversationId={conversationId} visible={visible && moduleVisible} />
        </Suspense>
      );
    if (id === 'evaluation') return <EvaluationDashboard visible={visible && moduleVisible} />;
    if (id === 'trace')
      return (
        <TraceViewer
          key={conversationId || 'none'}
          conversationId={conversationId}
          sessionId={session?.id || null}
          revision={session?.trace_revision || 0}
          visible={visible && moduleVisible}
        />
      );
    const outputs = routed.outputs.filter((row) => row.module === id);
    if (id === 'conversation')
      return (
        <div className="work-dialogue">
          {!messages.length ? (
            <div className="work-welcome">
              <span className="work-kicker">协作模式</span>
              <h2>从一件想做的事开始</h2>
              <p>我们在这里聊进展。代码、终端和文件会在各自的工作区展开。</p>
            </div>
          ) : (
            routed.conversation.map(renderMessage)
          )}
          <div ref={end} />
        </div>
      );
    if (id === 'code')
      return (
        <CodeModule
          outputs={outputs}
          sessionId={session?.id}
          root={session?.workspace_root}
          onError={onError}
          onStatus={onStatus}
        />
      );
    if (id === 'diff')
      return <DiffModule outputs={outputs} sessionId={session?.id} onError={onError} />;
    if (id === 'terminal') return <TerminalModule outputs={outputs} />;
    if (id === 'media')
      return (
        <>
          <MediaModule visible={visible && moduleVisible} onError={onError} />
          {outputs.map((row) => (
            <WorkspaceModule
              key={row.id}
              tab={{ id: row.id, kind: 'media', title: row.title, data: row.data }}
            />
          ))}
        </>
      );
    if (id === 'files' && session?.workspace_root)
      return <FilesModule sessionId={session.id} root={session.workspace_root} />;
    if (id === 'browser')
      return (
        <WorkspaceBrowser
          key={session?.id}
          visible={visible && moduleVisible}
          session={session}
          initialUrl={requestedUrl || undefined}
          onError={onError}
        />
      );
    if (id === 'workflow')
      return (
        <>
          <ol className="workflow-steps">
            {session?.steps.map((s) => (
              <li className={s.status} key={s.id}>
                <span>
                  <Icon
                    name={s.status === 'done' ? 'check' : s.status === 'failed' ? 'alert' : 'clock'}
                    size={12}
                  />
                </span>
                {s.title}
              </li>
            ))}
            {!session && <li>收到任务后生成步骤</li>}
          </ol>
          <RecoveryPanel
            key={conversationId || 'none'}
            conversationId={conversationId}
            sessionId={session?.id || null}
            visible={visible && moduleVisible}
            revision={session?.recovery_revision || session?.events.at(-1)?.id || 0}
          />
          <WorkbenchExtensions
            key={session?.id || 'none'}
            session={session}
            conversationId={conversationId}
            calls={pending?.calls || session?.pending_plan?.calls || []}
          />
        </>
      );
    if (id === 'collaboration')
      return (
        <div className="work-events-list">
          {session?.events
            .filter((e) => !['workspace_updated', 'workspace_opened'].includes(e.type))
            .slice(-30)
            .reverse()
            .map((e) => (
              <article className={`work-event ${e.type}`} key={e.id}>
                <time>
                  {new Date(e.created_at).toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </time>
                <strong>{eventNames[e.type] || '任务记录'}</strong>
                <p>
                  {String(
                    e.detail.summary ||
                      e.detail.title ||
                      tools[String(e.detail.name)] ||
                      statuses[String(e.detail.status)] ||
                      '',
                  ).slice(0, 260)}
                </p>
                {e.detail.verified !== undefined && (
                  <small>{e.detail.verified ? '已验证' : '尚未通过验证'}</small>
                )}
              </article>
            ))}
          {!session && <p className="work-empty">任务过程会实时显示在这里</p>}
        </div>
      );
    if (id === 'permissions' && pending)
      return (
        <div className="work-permissions">
          {pending.calls.map((call) => (
            <label key={call.id}>
              <input
                type="checkbox"
                checked={!call.requires_confirmation || pending.approved.includes(call.id)}
                disabled={!call.requires_confirmation}
                onChange={(e) => onApproval(call.id, e.target.checked)}
              />
              <span>
                {tools[call.name] || call.name}
                <small>
                  {String(
                    call.arguments.app ||
                      call.arguments.query ||
                      call.arguments.path ||
                      call.arguments.goal ||
                      '',
                  ).slice(0, 160)}
                </small>
                {!['high', 'critical'].includes(call.risk_level) &&
                  call.action_id &&
                  call.requires_confirmation && (
                    <span className="grant-choices">
                      <button
                        type="button"
                        onClick={() =>
                          void window.desktopAPI
                            ?.setTrustedAction(call.action_id!, 'task', session?.id)
                            .then(() => onApproval(call.id, true))
                            .catch((e) => onError(String(e)))
                        }
                      >
                        本任务允许
                      </button>
                    </span>
                  )}
              </span>
            </label>
          ))}
          <button type="button" className="work-primary" onClick={onExecute}>
            继续执行
          </button>
        </div>
      );
    return outputs.length ? (
      outputs.map((row) => (
        <div className="work-artifact" key={row.id}>
          <WorkspaceModule
            tab={{
              id: row.id,
              kind: row.type === 'application' ? 'app' : row.type === 'browser' ? 'web' : row.type,
              title: row.title,
              data: row.data,
            }}
          />
        </div>
      ))
    ) : (
      <p className="work-empty">还没有{WorkspaceModuleRegistry[id].title}内容</p>
    );
  }
  function dock(side: 'left' | 'right') {
    const palette: ModuleId[] =
      side === 'left' ? ['code', 'files', 'browser'] : ['terminal', 'media', 'system'];
    return (
      <aside
        className={'workspace-dock dock-' + side}
        aria-label={side === 'left' ? '左侧模块停靠栏' : '右侧模块停靠栏'}
      >
        {Object.keys(layout.modules)
          .filter((id) => {
            const p = layout.modules[id as ModuleId];
            return p.minimized ? p.dockSide === side : palette.includes(id as ModuleId);
          })
          .sort((a, b) => layout.modules[a as ModuleId].order - layout.modules[b as ModuleId].order)
          .map((value) => {
            const id = value as ModuleId;
            return (
              <DockTile
                key={id}
                id={id}
                side={side}
                updated={id !== active && routed.outputs.some((row) => row.module === id)}
                dragging={dragging.drag === id}
                onPointerDown={(e) => dragging.down(e, id)}
                onPointerMove={dragging.move}
                onPointerUp={dragging.up}
                onPointerCancel={dragging.cancel}
                onRestore={() => dragging.click(id, () => restore(id))}
                onMove={() =>
                  change(id, { minimized: true, dockSide: side === 'left' ? 'right' : 'left' })
                }
              />
            );
          })}
      </aside>
    );
  }
  return (
    <section
      className="workbench workbench-v8 workbench-v83"
      data-work-session={session?.id || ''}
      data-layout-key={layoutKey}
    >
      <div className="work-goal">
        <div>
          <small>当前目标</small>
          <h2>{session?.goal || '一起完成你的目标'}</h2>
          <p>{step?.title || '对话居中，工具与结果各有自己的位置'}</p>
        </div>
        <div className="work-task-controls">
          <span className={`work-status ${session?.status || 'idle'}`}>
            <i />
            {statuses[session?.status || 'idle'] || '处理中'}
          </span>
          {running && (
            <>
              <WorkbenchActionButton
                type="button"
                onClick={() => void control(paused ? 'resume' : 'pause')}
              >
                {paused ? '继续' : '暂停'}
              </WorkbenchActionButton>
              <WorkbenchActionButton
                type="button"
                disabled={paused}
                onClick={() => void control('takeover')}
              >
                手动接管
              </WorkbenchActionButton>
              <WorkbenchActionButton type="button" onClick={onStop}>
                停止
              </WorkbenchActionButton>
            </>
          )}
        </div>
      </div>
      <div className="workspace-toolbar">
        <WorkbenchActionButton
          type="button"
          className={layout.locked ? 'active' : ''}
          aria-label="锁定工作区视图"
          aria-pressed={layout.locked}
          title="锁定后，任务完成也保留当前工作区"
          onClick={() => setLayout((old) => ({ ...old, locked: !old.locked }))}
        >
          <Icon name="pin" size={15} />
          {layout.locked ? '视图已锁定' : '锁定视图'}
        </WorkbenchActionButton>
        <WorkbenchActionButton type="button" onClick={() => setAllModules(!allModules)}>
          <Icon name="panel" size={15} />
          模块
        </WorkbenchActionButton>
        <select
          aria-label="工作台布局模板"
          defaultValue="default"
          onChange={(e) => template(e.target.value as LayoutTemplate)}
        >
          <option value="default">默认布局</option>
          <option value="coding">代码布局</option>
          <option value="browser">网页布局</option>
          <option value="system">系统布局</option>
          <option value="blank">空白布局</option>
        </select>
        <WorkbenchActionButton type="button" onClick={undo} disabled={!undoCount}>
          撤销布局
        </WorkbenchActionButton>
        <WorkbenchActionButton type="button" onClick={reset}>
          恢复默认布局
        </WorkbenchActionButton>
        <WorkbenchActionButton
          type="button"
          onClick={() => {
            saveDefault();
            onStatus('已保存为默认工作台布局');
          }}
        >
          保存为默认布局
        </WorkbenchActionButton>
        {allModules && (
          <div className="workspace-module-picker">
            {Object.values(WorkspaceModuleRegistry).map((module) => (
              <WorkbenchActionButton
                key={module.id}
                type="button"
                data-module-drag={module.id}
                onPointerDown={(e) => dragging.down(e, module.id)}
                onPointerMove={dragging.move}
                onPointerUp={dragging.up}
                onPointerCancel={dragging.cancel}
                onClick={() =>
                  dragging.click(module.id, () => {
                    restore(module.id);
                    setAllModules(false);
                  })
                }
              >
                <Icon name={module.icon} />
                {module.title}
              </WorkbenchActionButton>
            ))}
          </div>
        )}
      </div>
      <div className="workspace-frame">
        {dock('left')}
        <div className="workspace-grid-shell">
          <nav className="work-tabs" role="tablist">
            {Object.values(WorkspaceModuleRegistry)
              .filter(
                (module) =>
                  !layout.modules[module.id].closed && !layout.modules[module.id].minimized,
              )
              .map((module) => (
                <WorkbenchActionButton
                  type="button"
                  role="tab"
                  aria-selected={active === module.id}
                  className={active === module.id ? 'active' : ''}
                  key={module.id}
                  onClick={() => restore(module.id)}
                >
                  <Icon name={module.icon} size={15} />
                  {module.title}
                </WorkbenchActionButton>
              ))}
          </nav>
          {loaded ? (
            <WorkspaceGrid
              key={layoutKey}
              layout={layout}
              onCommit={setLayout}
              onChange={change}
              renderModule={(id, shown) => content(id, shown)}
              drag={dragging.drag}
              target={dragging.target}
            />
          ) : (
            <p role="status">正在恢复工作台…</p>
          )}
        </div>
        {dock('right')}
      </div>
      {dragging.drag && (
        <div className="module-drag-preview" ref={dragging.ghost}>
          <Icon name={WorkspaceModuleRegistry[dragging.drag].icon} />
          {WorkspaceModuleRegistry[dragging.drag].title}
        </div>
      )}
    </section>
  );
}
