import { memo } from 'react';
import type { EvaluationRun } from './types';
import { number, rate } from './presentation';

export default memo(function EvaluationMetrics({ run }: { run: EvaluationRun | null }) {
  const m = run?.metrics;
  const values = [
    ['有效评分 / 总任务', `${m?.valid_count ?? 0} / ${run?.total_cases ?? 0}`],
    [run?.mode === 'mock' ? 'Mock 回归通过率' : '评测通过率', rate(m?.success_rate)],
    ['失败率', rate(m?.failure_rate)],
    ['平均耗时 ms', number(m?.average_duration_ms)],
    ['P50 / P95 ms', `${number(m?.p50_ms)} / ${number(m?.p95_ms)}`],
    ['平均工具调用', number(m?.average_tool_calls)],
    ['重试次数', String(m?.retry_count ?? 0)],
    ['恢复成功率', rate(m?.recovery_success_rate)],
  ];
  return (
    <>
      <div className="evaluation-metrics">
        {values.map(([label, value]) => (
          <div key={label}>
            <small>{label}</small>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      <p className="evaluation-note">
        真实 AI 任务成功率：
        {m?.real_task_success_rate == null ? '未测量' : rate(m.real_task_success_rate)}。 Usage：
        {m?.model_usage?.status || 'Unavailable'} · Token {m?.model_usage?.total_tokens ?? 'N/A'}。
        费用：{m?.api_cost?.value ?? 'N/A'}（{m?.api_cost?.status || 'Unavailable'}）。
        未执行、阻断、错误和未知结果不进入通过率分母；预期失败的安全断言通过不代表用户目标完成。
      </p>
    </>
  );
});
