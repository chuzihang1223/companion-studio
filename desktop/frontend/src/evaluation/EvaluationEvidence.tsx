import { useMemo } from 'react';
import TraceViewer from '../trace/TraceViewer';
import type { TraceQuery } from '../trace/types';
import type { BenchmarkCase, EvaluationResult } from './types';

export default function EvaluationEvidence({
  row,
  visible,
  caseInfo,
}: {
  row: EvaluationResult;
  visible: boolean;
  caseInfo?: BenchmarkCase;
}) {
  const api = window.desktopAPI;
  const source = useMemo(() => {
    if (!api) return undefined;
    const read = (operation: 'list' | 'detail' | 'events' | 'summary', query: TraceQuery) =>
      api.readEvaluation(`trace-${operation}`, {
        run_id: row.run_id,
        attempt_id: row.attempt_id,
        trace_id: query.trace_id,
        cursor: query.after ?? query.cursor,
        limit: query.limit,
      });
    const unavailable = () =>
      Promise.reject(Error('评测 Trace 随整次评测历史管理，请使用评测报告导出。'));
    return {
      readAgentTraces: read as NonNullable<Window['desktopAPI']>['readAgentTraces'],
      deleteAgentTrace: unavailable,
      exportAgentTrace: unavailable,
    };
  }, [api, row.run_id, row.attempt_id]);
  return (
    <section className="evaluation-evidence" aria-label="评分证据">
      <h4>
        {row.case_id} · {row.execution_status}
      </h4>
      <p>
        {row.error_code || '无错误代码'} · {row.result.explanation || '尚未评分'}
      </p>
      {caseInfo && (
        <details>
          <summary>本次合成任务输入</summary>
          <p>{caseInfo.description}</p>
          <blockquote>{caseInfo.input}</blockquote>
        </details>
      )}
      <details open>
        <summary>实际验证证据</summary>
        <dl>
          {Object.entries(row.result.evidence || {}).map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{value == null ? 'N/A' : String(value)}</dd>
            </div>
          ))}
        </dl>
      </details>
      {row.trace_id && source ? (
        <details>
          <summary>查看关联 Trace 与只读回放</summary>
          <TraceViewer
            key={row.attempt_id}
            conversationId={row.conversation_id}
            sessionId={row.work_session_id}
            revision={0}
            visible={visible}
            source={source}
            readOnly
          />
        </details>
      ) : (
        <p>此项没有可用 Trace（未执行或被环境阻断）。</p>
      )}
    </section>
  );
}
