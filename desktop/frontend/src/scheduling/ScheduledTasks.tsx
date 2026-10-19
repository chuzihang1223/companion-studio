import { useCallback, useEffect, useRef, useState } from 'react';
import type { SavedWorkflow, WorkflowRun } from '../workflows/types';
import type { Background, History, Navigation, Occurrence, Saved, Source, Status } from './types';
import { labels, shownTime } from './types';
import ScheduleForm from './ScheduleForm';
import ScheduleHistory from './ScheduleHistory';
import ScheduleList from './ScheduleList';
import './scheduling.css';

export default function ScheduledTasks({
  conversationId,
  visible,
  navigation,
  onBack,
}: {
  conversationId: string | null;
  visible: boolean;
  navigation?: Navigation | null;
  onBack?: () => void;
}) {
  const [owner, setOwner] = useState(conversationId || ''),
    [owners, setOwners] = useState<Array<{ conversation_id: string; title: string }>>([]);
  const [items, setItems] = useState<Saved[]>([]),
    [workflows, setWorkflows] = useState<SavedWorkflow[]>([]),
    [sources, setSources] = useState<Source[]>([]);
  const [status, setStatus] = useState<Status | null>(null),
    [background, setBackground] = useState<Background | null>(null);
  const [editing, setEditing] = useState<Saved | null>(null),
    [form, setForm] = useState(false),
    [selected, setSelected] = useState('');
  const [history, setHistory] = useState<History>({ items: [], events: [] }),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const [runs, setRuns] = useState<WorkflowRun[]>([]),
    [watchRun, setWatchRun] = useState('');
  const [notifications, setNotifications] = useState<
    Array<Navigation & { notification_id: string; kind: string; delivery: string }>
  >([]);
  const api = window.desktopAPI;
  const [loadedOwner, setLoadedOwner] = useState('');
  const [recent, setRecent] = useState<Array<Navigation & { kind: string; created_at: string }>>(
    [],
  );
  const [historyScope, setHistoryScope] = useState('');
  const scopeRef = useRef(owner + '#' + selected);
  const [occurrenceFocus, setOccurrenceFocus] = useState<string | undefined>(undefined);
  scopeRef.current = owner + '#' + selected;
  function changeOwner(value: string) {
    setOwner(value);
    setSelected('');
    setOccurrenceFocus(undefined);
    setForm(false);
    setEditing(null);
    setItems([]);
    setSources([]);
    setRuns([]);
    setWatchRun('');
    setNotifications([]);
    setLoadedOwner('');
    setHistoryScope('');
    setHistory({ items: [], events: [] });
    setError('');
  }
  function selectSchedule(value: string, occurrence?: string) {
    setSelected(value);
    setOccurrenceFocus(occurrence);
    setHistoryScope('');
    setHistory({ items: [], events: [] });
  }
  useEffect(() => {
    setOwner(navigation?.conversation_id || conversationId || '');
    setSelected(navigation?.schedule_id || '');
    setOccurrenceFocus(navigation?.occurrence_id);
    setHistory({ items: [], events: [] });
    setForm(false);
    setItems([]);
    setSources([]);
    setRuns([]);
    setWatchRun('');
    setNotifications([]);
    setLoadedOwner('');
    setHistoryScope('');
  }, [conversationId, navigation]);
  const refresh = useCallback(async () => {
    if (!api) return;
    const scopeKey = owner + '#' + selected;
    const [state, people, back, latest] = await Promise.all([
      api.readSchedules('status'),
      api.readSchedules('owners'),
      api.schedulerBackground(),
      api.readSchedules('recent'),
    ]);
    if (scopeRef.current !== scopeKey) return;
    setStatus(state);
    setOwners(people.items);
    setBackground(back);
    setRecent(latest.items);
    if (!owner || !people.items.some((row) => row.conversation_id === owner)) return;
    const [list, definitions, scopes, notices, workflowRuns] = await Promise.all([
      api.readSchedules('schedules', { conversation_id: owner }),
      api.readWorkflows('list'),
      api.readSchedules('sources', { conversation_id: owner }),
      api.readSchedules('notifications', { conversation_id: owner }),
      api.readWorkflows('runs', { conversation_id: owner }),
    ]);
    if (scopeRef.current !== scopeKey) return;
    setItems(list.items);
    setWorkflows(definitions.items);
    setSources(scopes.items);
    setNotifications(notices.items);
    setRuns(workflowRuns.items);
    setLoadedOwner(owner);
    if (selected) {
      const result = await api.readSchedules('history', {
        conversation_id: owner,
        schedule_id: selected,
      });
      if (scopeRef.current === scopeKey) {
        setHistory(result);
        setHistoryScope(scopeKey);
      }
    }
  }, [api, owner, selected]);
  useEffect(() => {
    if (!visible) return;
    let live = true,
      pending = false;
    const update = async () => {
      if (pending) return;
      pending = true;
      try {
        await refresh();
      } catch (cause) {
        if (live) setError(String(cause));
      } finally {
        pending = false;
      }
    };
    void update();
    const timer = window.setInterval(() => void update(), 2000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [refresh, visible]);
  async function perform(operation: () => Promise<unknown>) {
    setBusy(true);
    setError('');
    try {
      await operation();
      await refresh();
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }
  async function occurrenceAction(
    operation: 'approve' | 'cancel' | 'resume',
    row: Occurrence,
    revision?: number,
  ) {
    await perform(() =>
      api!.controlSchedules(operation, {
        occurrence_id: row.occurrence_id,
        conversation_id: owner,
        ...(revision ? { revision } : {}),
      }),
    );
  }
  return (
    <div
      className={onBack ? 'scheduler-page scheduler-overlay' : 'scheduler-page'}
      aria-label="定时任务管理"
    >
      <header>
        <h2>定时任务</h2>
        {onBack && <button onClick={onBack}>返回工作台</button>}
      </header>
      <p>
        按时间或受控变化运行已发布工作流。完全退出、关机或睡眠期间不能按时执行；默认关闭窗口即退出。
      </p>
      <div className="scheduler-actions">
        <strong data-scheduler-status={status?.running ? 'RUNNING' : 'STOPPED'}>
          {status?.running ? (status.paused ? '调度已暂停' : '调度运行中') : '调度不可用'}
        </strong>
        <span>
          排队 {status?.queued ?? 0} · 待授权 {status?.waiting_permission ?? 0}
        </span>
        <button
          disabled={busy || !api}
          onClick={() =>
            void perform(() => api!.controlSchedules('pause-all', { paused: !status?.paused }))
          }
        >
          {status?.paused ? '恢复全部触发' : '暂停全部触发'}
        </button>
        <label>
          <input
            aria-label="关闭窗口保持托盘运行"
            type="checkbox"
            checked={background?.enabled || false}
            onChange={(e) =>
              void perform(() => api!.schedulerBackground({ enabled: e.target.checked }))
            }
          />
          关闭窗口保持托盘运行
        </label>
        <button
          disabled={!background?.enabled || busy}
          onClick={() => void perform(() => api!.schedulerBackground({ hide: true }))}
        >
          最小化到托盘
        </button>
      </div>
      <small>
        暂停触发不会撤销已发送的操作；开机自启不提供，也不会写启动项。混合／向量检索请在前台运行。
      </small>
      {navigation?.recent && (
        <section className="scheduler-card" aria-label="最近执行结果">
          <h3>最近执行结果</h3>
          {!recent.length && <p>暂无执行记录。</p>}
          {recent.map((row) => (
            <p key={row.occurrence_id}>
              <button
                onClick={() => {
                  changeOwner(row.conversation_id || '');
                  selectSchedule(row.schedule_id || '', row.occurrence_id);
                }}
              >
                {shownTime(row.created_at)} · {labels[row.kind] || row.kind} ·{' '}
                {row.occurrence_id?.slice(0, 8)}
              </button>
            </p>
          ))}
        </section>
      )}
      <label>
        工作会话
        <select
          aria-label="调度工作会话"
          value={owners.some((row) => row.conversation_id === owner) ? owner : ''}
          onChange={(e) => changeOwner(e.target.value)}
        >
          <option value="">请选择工作会话</option>
          {owners.map((row) => (
            <option key={row.conversation_id} value={row.conversation_id}>
              {row.title}
            </option>
          ))}
        </select>
      </label>
      {!owner || !owners.some((row) => row.conversation_id === owner) ? (
        <p>请先创建或选择一个工作会话，再发布需要定时运行的工作流。</p>
      ) : loadedOwner !== owner ? (
        <p>正在加载本会话定时任务…</p>
      ) : (
        <>
          <div className="scheduler-actions">
            <button
              disabled={busy}
              onClick={() => {
                setEditing(null);
                setForm(true);
              }}
            >
              创建定时任务
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void perform(() =>
                  api!.controlSchedules('source', { conversation_id: owner, kind: 'FILE_CHANGE' }),
                )
              }
            >
              授权当前项目文件监控
            </button>
            <select
              aria-label="监控工作流运行"
              value={watchRun}
              onChange={(e) => setWatchRun(e.target.value)}
            >
              <option value="">选择要监控的工作流运行</option>
              {runs.map((row) => (
                <option key={row.run_id} value={row.run_id}>
                  {row.run_id.slice(0, 8)} · {row.state}
                </option>
              ))}
            </select>
            <button
              disabled={!watchRun || busy}
              onClick={() =>
                void perform(() =>
                  api!.controlSchedules('source', {
                    conversation_id: owner,
                    kind: 'WORKFLOW_STATE',
                    workflow_run_id: watchRun,
                  }),
                )
              }
            >
              授权状态监控
            </button>
          </div>
          {sources
            .filter((row) => row.enabled)
            .map((row) => (
              <div key={row.source_id}>
                <small>
                  来源 {row.source_id.slice(0, 8)} · 有效至 {shownTime(row.expires_at)}
                </small>
                <button
                  disabled={busy}
                  onClick={() =>
                    void perform(() =>
                      api!.controlSchedules('revoke-source', {
                        conversation_id: owner,
                        source_id: row.source_id,
                      }),
                    )
                  }
                >
                  撤销来源授权
                </button>
              </div>
            ))}
          {form && (
            <ScheduleForm
              key={editing?.definition.schedule_id || 'new'}
              owner={owner}
              saved={editing}
              workflows={workflows}
              sources={sources}
              onSaved={() => {
                setForm(false);
                void refresh().catch((cause) => setError(String(cause)));
              }}
            />
          )}
          <ScheduleList
            items={loadedOwner === owner ? items : []}
            busy={busy}
            edit={(row) => {
              selectSchedule(row.definition.schedule_id);
              setEditing(row);
              setForm(true);
            }}
            select={selectSchedule}
            state={(row, value) =>
              void perform(() =>
                api!.controlSchedules('state', {
                  conversation_id: owner,
                  schedule_id: row.definition.schedule_id,
                  revision: row.revision,
                  state: value,
                }),
              )
            }
            run={(row) =>
              void perform(() =>
                api!.controlSchedules('run-now', {
                  conversation_id: owner,
                  schedule_id: row.definition.schedule_id,
                }),
              )
            }
          />
          {selected && (
            <ScheduleHistory
              initialOccurrenceId={occurrenceFocus}
              key={selected}
              owner={owner}
              history={
                historyScope === owner + '#' + selected ? history : { items: [], events: [] }
              }
              action={occurrenceAction}
              refresh={() => void refresh().catch((cause) => setError(String(cause)))}
            />
          )}
          <details>
            <summary>本会话本地通知（{notifications.length}）</summary>
            {notifications.map((row) => (
              <p key={row.notification_id}>
                <button
                  onClick={() => {
                    if (row.schedule_id) selectSchedule(row.schedule_id, row.occurrence_id);
                  }}
                >
                  {labels[row.kind] || row.kind}
                </button>{' '}
                · 系统投递 {row.delivery}（不代表用户已读）
              </p>
            ))}
          </details>
        </>
      )}
      {(error || status?.error_code) && <p role="alert">{error || status?.error_code}</p>}
    </div>
  );
}
