import { useEffect, useState } from 'react';
import type { WorkflowRun } from './types';
import { compareRuns } from './comparison';
import { stateLabels } from './model';

const milliseconds = (value?: number) => (value == null ? 'N/A' : value.toFixed(2));
export default function RunComparison({
  run,
  history,
}: {
  run: WorkflowRun;
  history: WorkflowRun[];
}) {
  const [otherId, setOtherId] = useState('');
  const [other, setOther] = useState<WorkflowRun | null>(null);
  const [error, setError] = useState('');
  const choices = history.filter(
    (r) => r.workflow_id === run.workflow_id && r.run_id !== run.run_id,
  );
  useEffect(() => {
    setOtherId('');
    setOther(null);
  }, [run.run_id]);
  useEffect(() => {
    let current = true;
    setOther(null);
    setError('');
    if (otherId && window.desktopAPI)
      void window.desktopAPI
        .readWorkflows('run', { run_id: otherId, conversation_id: run.conversation_id })
        .then((value) => {
          if (current) setOther(value);
        })
        .catch((value: unknown) => {
          if (current) setError(String(value));
        });
    return () => {
      current = false;
    };
  }, [otherId, run.conversation_id]);
  const comparison =
    other && other.workflow_id === run.workflow_id && other.conversation_id === run.conversation_id
      ? compareRuns(run, other)
      : null;
  const rows: Array<[string, (r: WorkflowRun) => string | number]> = [
    ['版本', (r) => 'v' + r.version],
    ['状态', (r) => stateLabels[r.state] || r.state],
    ['累计执行 ms', (r) => milliseconds(r.active_ms)],
    ['校验 ms', (r) => milliseconds(r.validation_ms)],
    ['编译 ms', (r) => milliseconds(r.compile_ms)],
    ['排队 ms', (r) => milliseconds(r.queue_wait_ms)],
    [
      '平均节点调度 ms',
      (r) => {
        const times = r.nodes.map((n) => n.scheduling_ms).filter((n): n is number => n != null);
        return times.length ? milliseconds(times.reduce((a, b) => a + b, 0) / times.length) : 'N/A';
      },
    ],
    ['模型 / 工具次数', (r) => r.model_calls + ' / ' + r.tool_calls],
    [
      '失败节点 / 已执行节点',
      (r) =>
        r.nodes.filter((n) => n.state === 'FAILED').length +
        ' / ' +
        r.nodes.filter((n) => n.attempt > 0).length,
    ],
    ['续接次数', (r) => Math.max(0, r.attempt - 1)],
    ['已观察 Token', (r) => r.model_usage?.total_tokens ?? 'N/A'],
    ['估计费用', (r) => r.estimated_cost ?? 'N/A'],
  ];
  return (
    <details className="wf-comparison">
      <summary>对比同一工作流的运行</summary>
      <label>
        另一条运行
        <select
          aria-label="对比工作流运行"
          value={otherId}
          onChange={(e) => setOtherId(e.target.value)}
        >
          <option value="">选择当前会话中的记录</option>
          {choices.map((r) => (
            <option key={r.run_id} value={r.run_id}>
              v{r.version} · {r.created_at.slice(0, 19)} · {stateLabels[r.state] || r.state}
            </option>
          ))}
        </select>
      </label>
      {!choices.length && <p>同一工作流再运行一次后，可以对比不同版本。</p>}
      {error && <p role="alert">{error}</p>}
      {other && comparison && (
        <div aria-label="工作流版本运行对比">
          <p>
            {!comparison.conditionsKnown
              ? '旧记录缺少运行条件，不能公平比较。'
              : comparison.differences.length
                ? '运行条件不同，不能公平排名：' + comparison.differences.join('、')
                : '已记录条件一致；小样本不代表模型能力排名。'}
          </p>
          <table>
            <thead>
              <tr>
                <th>观察项</th>
                <th>当前记录</th>
                <th>所选记录</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([label, value]) => (
                <tr key={label}>
                  <th>{label}</th>
                  <td>{value(run)}</td>
                  <td>{value(other)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <details>
            <summary>运行条件与实际输出</summary>
            <pre>
              {JSON.stringify(
                {
                  current: { conditions: run.execution_snapshot, output: run.output },
                  other: { conditions: other.execution_snapshot, output: other.output },
                },
                null,
                2,
              )}
            </pre>
          </details>
          <p>
            此处只读历史，不重新调用模型或工具。节点内部重试与恢复核验详情在原 Trace / Checkpoint
            查看。
          </p>
        </div>
      )}
    </details>
  );
}
