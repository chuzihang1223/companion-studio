import { useEffect, useRef, useState } from 'react';
import type { WorkflowRun, ResumePreview } from '../workflows/types';
import RunPanel from '../workflows/RunPanel';
import type { History, Occurrence } from './types';
import { labels, shownTime } from './types';

export default function ScheduleHistory({
  owner,
  history,
  action,
  refresh,
  initialOccurrenceId,
}: {
  owner: string;
  history: History;
  action: (
    operation: 'approve' | 'cancel' | 'resume',
    row: Occurrence,
    revision?: number,
  ) => Promise<void>;
  refresh: () => void;
  initialOccurrenceId?: string;
}) {
  const [selected, setSelected] = useState<Occurrence | null>(null),
    [run, setRun] = useState<WorkflowRun | null>(null);
  const [preview, setPreview] = useState<ResumePreview | null>(null),
    [details, setDetails] = useState<unknown>(null),
    [error, setError] = useState('');
  const current =
    history.items.find((row) => row.occurrence_id === selected?.occurrence_id) || null;
  const appliedNavigation = useRef('');
  useEffect(() => {
    if (!initialOccurrenceId || appliedNavigation.current === initialOccurrenceId) return;
    const found = history.items.find((row) => row.occurrence_id === initialOccurrenceId);
    if (found) {
      appliedNavigation.current = initialOccurrenceId;
      setSelected(found);
      setPreview(null);
      setDetails(null);
    }
  }, [initialOccurrenceId, history.items]);
  const scopedRun = run?.run_id === current?.workflow_run_id ? run : null;
  const detailScope =
    owner + ':' + (current?.occurrence_id || '') + ':' + (current?.workflow_run_id || '');
  const detailScopeRef = useRef(detailScope);
  detailScopeRef.current = detailScope;
  const scopedPreview = preview?.run_id === scopedRun?.run_id ? preview : null;
  const execute = async (operation: () => Promise<unknown>) => {
    try {
      setError('');
      await operation();
      refresh();
    } catch (cause) {
      setError(String(cause));
    }
  };
  useEffect(() => {
    let live = true;
    if (!current?.workflow_run_id) {
      setRun(null);
      return;
    }
    void window.desktopAPI
      ?.readWorkflows('run', { run_id: current.workflow_run_id, conversation_id: owner })
      .then((value) => {
        if (live) setRun(value);
      })
      .catch((cause) => live && setError(String(cause)));
    return () => {
      live = false;
    };
  }, [current?.workflow_run_id, current?.status, history, owner]);
  return (
    <section className="scheduler-card" aria-label="调度执行历史">
      <h3>执行历史</h3>
      <p>查看记录不会重放工具；“立即运行”另建一次记录。没有关联评测时不补造评测结果。</p>
      {history.items.map((row) => (
        <article key={row.occurrence_id} data-occurrence-state={row.status}>
          <button
            onClick={() => {
              setSelected(row);
              setPreview(null);
              setDetails(null);
            }}
          >
            {shownTime(row.scheduled_for)} · {labels[row.status] || row.status}
          </button>
          <small>{row.last_error || ''}</small>
          {row.status === 'WAITING_PERMISSION' && !row.workflow_run_id && (
            <button onClick={() => void action('approve', row)}>确认启动本次</button>
          )}
          {['QUEUED', 'CLAIMED', 'RUNNING', 'WAITING_PERMISSION', 'WAITING_ENVIRONMENT'].includes(
            row.status,
          ) && <button onClick={() => void action('cancel', row)}>取消本次</button>}
        </article>
      ))}
      {!history.items.length && <p>尚无执行记录。</p>}
      {current && (
        <div>
          <h4>{labels[current.status] || current.status}</h4>
          <dl>
            <dt>计划时间</dt>
            <dd>{shownTime(current.scheduled_for)}</dd>
            <dt>实际开始</dt>
            <dd>{shownTime(current.started_at)}</dd>
            <dt>完成时间</dt>
            <dd>{shownTime(current.finished_at)}</dd>
            <dt>运行耗时</dt>
            <dd>{current.summary?.duration_ms ?? '未知'} ms</dd>
            <dt>验证</dt>
            <dd>{current.summary?.verification_result || '尚未验证'}（不代表 AI 目标成功率）</dd>
            <dt>恢复次数</dt>
            <dd>{current.summary?.recovery_count ?? 0}</dd>
          </dl>
          <RunPanel
            run={scopedRun}
            history={scopedRun ? [scopedRun] : []}
            events={[]}
            preview={scopedPreview}
            onSelect={() => {}}
            onRefresh={refresh}
            onAction={(operation) =>
              void execute(() =>
                operation === 'cancel'
                  ? action('cancel', current)
                  : window.desktopAPI!.controlWorkflows('control', {
                      run_id: run!.run_id,
                      conversation_id: owner,
                      action: operation,
                    }),
              )
            }
            onPreview={() =>
              void execute(async () => {
                const ticket = detailScope;
                const value = await window.desktopAPI!.readSchedules('recovery-preview', {
                  occurrence_id: current.occurrence_id,
                  conversation_id: owner,
                });
                if (detailScopeRef.current === ticket) setPreview(value);
              })
            }
            onResume={() => void execute(() => action('resume', current, scopedPreview?.revision))}
            onApprove={(approval, decision) =>
              void execute(() =>
                window.desktopAPI!.controlWorkflows('approve', {
                  run_id: run!.run_id,
                  conversation_id: owner,
                  approval_id: approval,
                  decision,
                }),
              )
            }
            onTrace={() =>
              void execute(async () => {
                const ticket = detailScope;
                if (current.summary?.trace_id) {
                  const value = await window.desktopAPI!.readAgentTraces('events', {
                    conversation_id: owner,
                    trace_id: current.summary.trace_id,
                    limit: 100,
                  });
                  if (detailScopeRef.current === ticket) setDetails(value);
                }
              })
            }
          />
        </div>
      )}
      {details !== null && (
        <details open>
          <summary>Trace 只读记录</summary>
          <pre>{JSON.stringify(details, null, 2)}</pre>
        </details>
      )}
      {error && <p role="alert">{error}</p>}
      <details>
        <summary>调度状态事件（{history.events.length}）</summary>
        <ol>
          {history.events.map((row) => (
            <li key={row.cursor}>
              {shownTime(row.created_at)} · {row.kind}
            </li>
          ))}
        </ol>
      </details>
    </section>
  );
}
