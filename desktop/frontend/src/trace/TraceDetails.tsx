import type { AgentTrace, TraceEvent } from './types';
import { eventNames } from './replay';
export const formatMs = (value: number | null | undefined) =>
  value == null ? '未记录' : `${value.toFixed(1)} ms`;
const phases: Record<string, string> = {
  task: '任务',
  planning: '规划',
  model: '模型',
  tool: '工具',
  operation: '操作',
  verification: '验证',
  scheduler: '调度',
  permission: '授权等待',
  recovery: '恢复',
};
export default function TraceDetails({
  trace,
  event,
}: {
  trace: AgentTrace;
  event: TraceEvent | undefined;
}) {
  const span = event?.span;
  return (
    <aside className="trace-details" aria-label="事件详情">
      {event ? (
        <>
          <h4>{eventNames[event.event_type] || event.event_type}</h4>
          <dl>
            {Object.entries({
              阶段: phases[event.phase] || event.phase,
              '任务 ID': trace.task_id,
              '会话 ID': trace.conversation_id,
              'Work Session': trace.work_session_id,
              Span: event.span_id,
              '父 Span': span?.parent_span_id,
              工具: span?.tool_name,
              模型: span?.model_name,
              开始时间: span?.started_at || event.created_at,
              结束时间: span?.completed_at,
              执行耗时: formatMs(event.duration_ms),
              错误代码: event.error_code,
              错误说明: event.error_summary,
              验证: event.verification_status,
              '来源 WorkEvent': event.source_work_event_id,
            }).map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{value ?? '未记录'}</dd>
              </div>
            ))}
          </dl>
          <p>
            {span?.evidence.length || event.evidence.length
              ? '仅显示工具返回的结构化证据标志；不等同屏幕证据。'
              : '未保存外部证据，不会补造截图或执行结果。'}
          </p>
          <details>
            <summary>参数摘要、结果与证据（已脱敏）</summary>
            <pre>
              {JSON.stringify(
                {
                  event: event.metadata,
                  span: span?.metadata,
                  evidence: span?.evidence || event.evidence,
                },
                null,
                2,
              ).slice(0, 8000)}
            </pre>
          </details>
        </>
      ) : (
        <p>选择一个步骤查看详情。</p>
      )}
    </aside>
  );
}
