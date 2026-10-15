import type { Comparison } from './types';
import { modelLabel, number, rate } from './presentation';

export default function EvaluationComparison({ comparison }: { comparison: Comparison }) {
  const runs = [comparison.left, comparison.right];
  const fields = [
    ['通过率', ...runs.map((r) => rate(r.metrics.success_rate))],
    ['失败数', ...runs.map((r) => String(r.metrics.counts?.FAIL ?? 0))],
    [
      'P50 / P95 ms',
      ...runs.map((r) => `${number(r.metrics.p50_ms)} / ${number(r.metrics.p95_ms)}`),
    ],
    ['工具调用', ...runs.map((r) => String(r.metrics.tool_calls ?? 'N/A'))],
    ['Token', ...runs.map((r) => String(r.metrics.model_usage?.total_tokens ?? 'N/A'))],
    ['费用', ...runs.map((r) => String(r.metrics.api_cost?.value ?? 'N/A'))],
  ];
  return (
    <section aria-label="评测条件对比" className="evaluation-comparison">
      <h4>运行对比</h4>
      <p role="status">
        {comparison.dataset_compatible ? '数据集一致' : '数据集不同，不能公平比较'} ·{' '}
        {comparison.warning}
      </p>
      <table>
        <thead>
          <tr>
            <th>指标</th>
            {runs.map((r) => (
              <th key={r.run_id}>
                {modelLabel(r)} · {r.run_id.slice(0, 8)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {fields.map(([label, a, b]) => (
            <tr key={label}>
              <th>{label}</th>
              <td>{a}</td>
              <td>{b}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <details>
        <summary>条件差异（{comparison.differences.length}）</summary>
        {comparison.differences.map((d) => (
          <p key={d.field}>
            <b>{d.field}</b>
            <br />
            {JSON.stringify(d.left)} → {JSON.stringify(d.right)}
          </p>
        ))}
      </details>
      <details>
        <summary>逐项结果（最多 100 项）</summary>
        <table>
          <tbody>
            {comparison.cases.left.map((row, i) => (
              <tr key={row.attempt_id}>
                <td>
                  {row.case_id} #{row.repetition_index + 1}
                </td>
                <td>{row.execution_status}</td>
                <td>
                  {comparison.cases.right[i]?.case_id === row.case_id
                    ? comparison.cases.right[i].execution_status
                    : '无匹配项'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}
