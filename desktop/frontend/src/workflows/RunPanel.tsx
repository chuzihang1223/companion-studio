import type { WorkflowRun, WorkflowEvent, ResumePreview } from './types';
import { stateLabels } from './model';

export default function RunPanel({
  run,
  history,
  events,
  preview,
  onSelect,
  onAction,
  onPreview,
  onResume,
  onApprove,
  onTrace,
  onRefresh,
}: {
  run: WorkflowRun | null;
  history: WorkflowRun[];
  events: WorkflowEvent[];
  preview: ResumePreview | null;
  onSelect: (id: string) => void;
  onAction: (action: 'pause' | 'continue' | 'cancel') => void;
  onPreview: () => void;
  onResume: () => void;
  onApprove: (id: string, decision: boolean) => void;
  onTrace: (nodeId?: string) => void;
  onRefresh: () => void;
}) {
  const pending = run?.nodes.find((n) => n.pending)?.pending;
  return (
    <section className="wf-run-panel" aria-label="工作流运行记录">
      <header>
        <strong>运行与日志</strong>
        <button onClick={onRefresh}>刷新记录</button>
        <select
          aria-label="工作流运行历史"
          value={run?.run_id || ''}
          onChange={(e) => e.target.value && onSelect(e.target.value)}
        >
          <option value="">选择当前会话的历史记录</option>
          {history.map((r) => (
            <option value={r.run_id} key={r.run_id}>
              {r.created_at.slice(0, 19)} · v{r.version} · {stateLabels[r.state] || r.state}
            </option>
          ))}
        </select>
        {run && <span data-workflow-state={run.state}>{stateLabels[run.state] || run.state}</span>}
      </header>
      {!run ? (
        <p>
          保存、校验并发布一个版本后，可在当前工作会话中运行。历史记录按会话保存，回看不会重放工具。
        </p>
      ) : (
        <>
          <div className="wf-run-actions">
            <span>
              v{run.version} · 模型 {run.model_calls} 次 · 工具 {run.tool_calls} 次 ·{' '}
              {(run.active_ms / 1000).toFixed(1)} 秒
            </span>
            <button
              disabled={!['RUNNING', 'READY'].includes(run.state)}
              onClick={() => onAction('pause')}
            >
              暂停后续节点
            </button>
            <button disabled={run.state !== 'PAUSED'} onClick={() => onAction('continue')}>
              继续
            </button>
            <button
              disabled={['COMPLETED', 'CANCELLED'].includes(run.state)}
              onClick={() => onAction('cancel')}
            >
              取消运行
            </button>
            <button
              disabled={!['FAILED', 'INTERRUPTED', 'NEEDS_REVIEW'].includes(run.state)}
              onClick={onPreview}
            >
              检查恢复
            </button>
            <button disabled={!run.trace_id} onClick={() => onTrace()}>
              执行 Trace
            </button>
          </div>
          {run.error_code && <p role="alert">{run.error_code} · 可在节点详情查看失败位置。</p>}
          {pending && (
            <div className="wf-approval">
              <strong>等待你确认这个节点</strong>
              <p>{pending.message || `${pending.call?.name} · ${pending.risk}`}</p>
              <pre>{JSON.stringify(pending.call?.arguments ?? pending.value, null, 2)}</pre>
              <button className="wf-primary" onClick={() => onApprove(pending.approval_id, true)}>
                批准本次节点
              </button>
              <button onClick={() => onApprove(pending.approval_id, false)}>拒绝</button>
              <button onClick={() => onAction('cancel')}>取消整个运行</button>
            </div>
          )}
          {preview && (
            <div className="wf-recovery">
              <strong>
                {preview.can_resume ? '当前检查点允许续接' : '需要人工核查，未自动重试'}
              </strong>
              {preview.items.map((i) => (
                <p key={i.node_id}>
                  {i.node_id}：{i.action} {i.code || ''}{' '}
                  {i.checkpoints.filter((c) => c.checkpoint_id).length
                    ? '· 已关联原系统检查点'
                    : ''}
                </p>
              ))}
              <button disabled={!preview.can_resume} onClick={onResume}>
                确认安全续接
              </button>
            </div>
          )}
          <details open={!!run.output}>
            <summary>实际输出</summary>
            <pre className="wf-output">{JSON.stringify(run.output, null, 2)}</pre>
            <p>
              流程完成仅表示已激活节点执行结束；Mock 评测和 MCP 协议成功不代表真实 AI 任务质量。
            </p>
          </details>
          <ol className="wf-event-list">
            {events.slice(-100).map((e) => (
              <li key={e.cursor}>
                <span>#{e.cursor}</span> {e.kind.replace('WORKFLOW_', '')} {e.node_id || ''} ·{' '}
                {e.state || ''}
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
