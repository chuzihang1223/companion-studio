import type { Dispatch, SetStateAction } from 'react';
import type { Dataset, EvaluationResult, EvaluationRun } from './types';
import { modelLabel, number } from './presentation';

type Props = {
  rows: EvaluationResult[];
  dataset: Dataset | null;
  run: EvaluationRun;
  picked: string[];
  setPicked: Dispatch<SetStateAction<string[]>>;
  onEvidence: (row: EvaluationResult) => void;
  cursor: number;
  nextCursor: number | null;
  onCursor: (cursor: number) => void;
};

export default function EvaluationResults({
  rows,
  dataset,
  run,
  picked,
  setPicked,
  onEvidence,
  cursor,
  nextCursor,
  onCursor,
}: Props) {
  return (
    <>
      <div className="evaluation-table-wrap">
        <table aria-label="评测结果">
          <thead>
            <tr>
              <th>选中</th>
              <th>Case / 任务</th>
              <th>类别</th>
              <th>执行 / 评分</th>
              <th>ms</th>
              <th>重试</th>
              <th>模型</th>
              <th>Trace</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 20).map((row) => {
              const c = dataset?.cases?.find((c) => c.case_id === row.case_id);
              return (
                <tr key={row.attempt_id} data-status={row.execution_status}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`选择 ${row.case_id}`}
                      checked={picked.includes(row.case_id)}
                      onChange={(e) =>
                        setPicked((old) =>
                          e.target.checked
                            ? [...new Set([...old, row.case_id])]
                            : old.filter((id) => id !== row.case_id),
                        )
                      }
                    />
                  </td>
                  <td>
                    <button onClick={() => onEvidence(row)}>
                      {row.case_id} #{row.repetition_index + 1} · {c?.title}
                    </button>
                  </td>
                  <td>{c?.category}</td>
                  <td>
                    {row.execution_status} / {row.grader_status}
                  </td>
                  <td>{number(row.duration_ms)}</td>
                  <td>{row.result.retry_count ?? 'N/A'}</td>
                  <td>{modelLabel(run)}</td>
                  <td>
                    <button disabled={!row.trace_id} onClick={() => onEvidence(row)}>
                      查看 Trace
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="evaluation-actions">
        <button disabled={cursor === 0} onClick={() => onCursor(Math.max(0, cursor - 20))}>
          上一页
        </button>
        <span>第 {Math.floor(cursor / 20) + 1} 页 · 每页最多 20 项</span>
        <button disabled={nextCursor == null} onClick={() => onCursor(nextCursor || 0)}>
          下一页
        </button>
      </div>
    </>
  );
}
