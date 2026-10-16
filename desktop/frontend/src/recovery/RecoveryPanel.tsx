import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import TraceViewer from '../trace/TraceViewer';
import {
  actions,
  states,
  type RecoveryAction,
  type RecoveryPreview,
  type RecoveryTask,
} from './types';
import './recovery.css';

type Props = {
  conversationId: string | null;
  sessionId: string | null;
  visible: boolean;
  revision: number;
};
const terminal = (state: string) => ['COMPLETED', 'CANCELLED', 'UNRECOVERABLE'].includes(state);

/** Local module state; no global progress loop or Workbench geometry changes. */
export default function RecoveryPanel({ conversationId, sessionId, visible, revision }: Props) {
  const [rows, setRows] = useState<RecoveryTask[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [preview, setPreview] = useState<RecoveryPreview | null>(null);
  const [approved, setApproved] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [inspect, setInspect] = useState(false);
  const [showTrace, setShowTrace] = useState(false);
  const [modelConsent, setModelConsent] = useState(false);
  const generation = useRef({ value: 0 });
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const refresh = useCallback(async () => {
    if (!conversationId || !window.desktopAPI) return;
    const ticket = ++generation.current.value;
    try {
      const data = await window.desktopAPI.readAgentRecovery('list', {
        conversation_id: conversationId,
      });
      if (!mounted.current || ticket !== generation.current.value) return;
      setRows(data.items);
      setSelectedId((old) =>
        data.items.some((row) => row.task_id === old)
          ? old
          : (data.items.find((row) => row.work_session_id === sessionId) || data.items[0])
              ?.task_id || '',
      );
    } catch (e) {
      if (mounted.current && ticket === generation.current.value) setError(String(e).slice(0, 300));
    }
  }, [conversationId, sessionId]);
  useEffect(() => {
    if (!visible) return;
    const epoch = generation.current;
    const debounce = setTimeout(() => void refresh(), 180);
    const poll = setInterval(() => void refresh(), 3000);
    return () => {
      clearTimeout(debounce);
      clearInterval(poll);
      epoch.value++;
    };
  }, [visible, revision, refresh]);
  useEffect(() => {
    setPreview(null);
    setApproved([]);
    setInspect(false);
    setShowTrace(false);
    setError('');
  }, [selectedId]);
  const selected = useMemo(
    () => rows.find((row) => row.task_id === selectedId),
    [rows, selectedId],
  );

  async function readPreview() {
    if (!selected || !window.desktopAPI || busy) return;
    setBusy(true);
    setError('');
    try {
      const value = await window.desktopAPI.readAgentRecovery('preview', {
        task_id: selected.task_id,
        conversation_id: selected.conversation_id,
        work_session_id: selected.work_session_id,
      });
      if (mounted.current) {
        setPreview(value);
        setApproved([]);
      }
    } catch (e) {
      if (mounted.current) setError(String(e).slice(0, 300));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  async function control(action: RecoveryAction) {
    if (!selected || !window.desktopAPI || busy) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const value = await window.desktopAPI.controlAgentRecovery(selected.task_id, action, {
        conversation_id: selected.conversation_id,
        work_session_id: selected.work_session_id,
        request_id: crypto.randomUUID(),
        revision: selected.revision,
        approved_ids: approved,
        confirm_model_requests: modelConsent,
      });
      if (mounted.current) {
        if (action === 'revalidate') setPreview(value as unknown as RecoveryPreview);
        setNotice(
          action === 'restart'
            ? '已创建新计划。检查普通授权区后再执行；旧任务保留。'
            : action === 'resume'
              ? '已提交安全续接；不会追加模型或语音请求。'
              : '任务状态已更新。',
        );
        await refresh();
      }
    } catch (e) {
      if (mounted.current) setError(String(e).slice(0, 300));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  const canRestart =
    selected &&
    !selected.checkpoints.some(
      (step) =>
        step.side_effect_class !== 'READ_ONLY' && !['PREPARED', 'DENIED'].includes(step.step_state),
    );
  const active = selected && ['RECOVERING', 'RESUMING', 'WAITING_USER'].includes(selected.state);
  return (
    <section className="recovery-panel" aria-label="任务恢复与检查点">
      <h3>任务恢复与检查点</h3>
      {!conversationId ? (
        <p>选择工作会话后查看执行检查点。</p>
      ) : !window.desktopAPI ? (
        <p>请在桌面应用中查看恢复状态。</p>
      ) : !rows.length ? (
        <p>此会话还没有检查点。旧任务不补造执行记录。</p>
      ) : (
        <>
          <label>
            任务
            <select
              aria-label="恢复任务"
              value={selectedId}
              onChange={(e) => setSelectedId(e.target.value)}
              disabled={busy}
            >
              {rows.map((row) => (
                <option key={row.task_id} value={row.task_id}>
                  {states[row.state] || row.state} · {row.goal.slice(0, 35)}
                </option>
              ))}
            </select>
          </label>
          {selected && (
            <>
              <p className="recovery-state">
                {states[selected.state] || selected.state} · 已提交 {selected.completed_steps}{' '}
                个检查点
              </p>
              <p>{selected.failure.message || '当前没有已分类的失败。'}</p>
              <small>
                任务 {selected.task_id} · 最新执行 {selected.execution_id}
              </small>
              <p>
                恢复尝试 {selected.counters.recovery_attempts || 0} · 重试{' '}
                {selected.counters.retry_count || 0} · 人工介入{' '}
                {selected.counters.human_interventions || 0}
              </p>
              <p>
                最后检查点：{selected.checkpoints.at(-1)?.step_state || '尚未执行工具'} ·{' '}
                {new Date(selected.updated_at).toLocaleString()}
              </p>
              <div className="recovery-actions">
                <button onClick={() => setInspect(!inspect)}>检查检查点</button>
                <button disabled={busy || !!active} onClick={() => void readPreview()}>
                  恢复预览
                </button>
                <button
                  disabled={busy || !!active || terminal(selected.state)}
                  onClick={() => void control('revalidate')}
                >
                  重新观察
                </button>
                <button
                  disabled={
                    busy ||
                    !preview?.can_resume ||
                    !!active ||
                    terminal(selected.state) ||
                    preview.items.some(
                      (row) => row.requires_approval && !approved.includes(row.tool_call_id),
                    )
                  }
                  onClick={() => void control('resume')}
                >
                  安全续接
                </button>
              </div>
              {preview && (
                <div className="recovery-preview">
                  <p>{preview.summary}</p>
                  <p>续接额外模型请求：{preview.extra_model_requests}。执行前仍会再次核验。</p>
                  <ol>
                    {preview.items.map((row) => (
                      <li key={row.tool_call_id}>
                        <strong>{row.tool_name}</strong>：{actions[row.action] || row.action}
                        {row.error_code && `（${row.error_code}）`}
                        {row.requires_approval && (
                          <label>
                            <input
                              type="checkbox"
                              checked={approved.includes(row.tool_call_id)}
                              onChange={(e) =>
                                setApproved((old) =>
                                  e.target.checked
                                    ? [...old, row.tool_call_id]
                                    : old.filter((id) => id !== row.tool_call_id),
                                )
                              }
                            />
                            本次允许该步骤；仍受当前权限策略限制
                          </label>
                        )}
                      </li>
                    ))}
                  </ol>
                  {!preview.can_resume && (
                    <p role="alert">存在冲突或未确认副作用。请手动核查；不会自动覆盖或重放。</p>
                  )}
                </div>
              )}
              {inspect && (
                <div className="recovery-checkpoints">
                  {selected.checkpoints.map((step) => (
                    <details key={step.checkpoint_id}>
                      <summary>
                        {step.step_index + 1}. {step.tool_name} · {step.step_state} ·{' '}
                        {step.permission_state}
                      </summary>
                      <pre>
                        {JSON.stringify(
                          {
                            operation_id: step.operation_id,
                            intent: step.execution_intent,
                            result: step.result_summary,
                          },
                          null,
                          2,
                        ).slice(0, 8000)}
                      </pre>
                    </details>
                  ))}
                </div>
              )}
              <div className="recovery-actions">
                <button
                  disabled={busy || !active || selected.state === 'WAITING_USER'}
                  onClick={() => void control('pause')}
                >
                  暂停
                </button>
                <button
                  disabled={busy || selected.state !== 'WAITING_USER'}
                  onClick={() => void control('continue')}
                >
                  继续当前执行
                </button>
                <button
                  disabled={busy || terminal(selected.state)}
                  onClick={() => void control('takeover')}
                >
                  手动接管
                </button>
                <button
                  disabled={busy || terminal(selected.state)}
                  onClick={() => void control('cancel')}
                >
                  停止任务 / 取消恢复
                </button>
                <button onClick={() => setShowTrace(!showTrace)}>执行追踪与只读回放</button>
              </div>
              <details>
                <summary>重新开始与安全边界</summary>
                <p>
                  重新规划会建立新的任务编号，并可能调用已配置的模型；不会自动执行新计划。已产生副作用的任务禁止从头重放。
                </p>
                <label>
                  <input
                    type="checkbox"
                    checked={modelConsent}
                    onChange={(e) => setModelConsent(e.target.checked)}
                  />
                  允许这次重新规划使用当前模型（可能计费）
                </label>
                <button
                  disabled={busy || !!active || !canRestart || !modelConsent}
                  onClick={() => void control('restart')}
                >
                  创建新计划
                </button>
                <p>
                  当前计划没有可选步骤标记，因此不提供跳过必需步骤。任意外部操作不保证 Exactly
                  Once。
                </p>
              </details>
              {showTrace && (
                <TraceViewer
                  key={selected.work_session_id}
                  conversationId={conversationId}
                  sessionId={selected.work_session_id}
                  visible={visible}
                  revision={revision}
                  readOnly
                />
              )}
            </>
          )}
        </>
      )}
      {busy && <p role="status">正在核验或更新…</p>}
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
