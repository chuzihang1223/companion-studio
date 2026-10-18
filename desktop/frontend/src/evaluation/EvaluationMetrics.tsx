import { memo } from 'react';
import type { EvaluationRun } from './types';
import { number, rate } from './presentation';

export default memo(function EvaluationMetrics({ run }: { run: EvaluationRun | null }) {
  const m = run?.metrics;
  const recovery = m?.recovery;
  const mcp = m?.mcp;
  const workflow = m?.workflow;
  const knowledge = m?.knowledge;
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
  if (recovery)
    values.push(
      ['恢复尝试 / 成功', `${recovery.attempt_count} / ${recovery.successful_attempts}`],
      ['恢复耗时 ms', number(recovery.duration_ms)],
      ['局部重新规划', String(recovery.replanning_count)],
      ['重复执行', String(recovery.duplicate_execution_count)],
      ['检查点核验率', rate(recovery.checkpoint_validation_rate)],
      ['未确认副作用', String(recovery.unresolved_side_effects)],
      ['合成用户干预', String(recovery.human_interventions)],
      ['合成目标完成率', rate(recovery.goal_completion_rate)],
    );
  if (mcp)
    values.push(
      ['MCP 连接成功率（合成）', rate(mcp.connection_success_rate)],
      ['工具发现成功率（合成）', rate(mcp.discovery_success_rate)],
      ['工具调用成功率（含故障）', rate(mcp.tool_success_rate)],
      ['Schema 拒绝', String(mcp.schema_failures)],
      ['重新连接', String(mcp.reconnections)],
      ['权限拒绝', String(mcp.permission_denials)],
      [
        '调用 P50 / P95 ms',
        `${number(mcp.tool_latency_p50_ms)} / ${number(mcp.tool_latency_p95_ms)}`,
      ],
      ['Skill 加载 ms', number(mcp.average_skill_load_ms)],
      ['Skill 合成匹配准确率', rate(mcp.skill_match_accuracy)],
    );
  if (workflow)
    values.push(
      [
        '工作流运行 / 流程完成（合成）',
        `${workflow.executed_workflows} / ${workflow.completed_workflows}`,
      ],
      ['未知副作用阻断', String(workflow.blocked_side_effects)],
      [
        '流程校验 P50 / P95 ms',
        `${number(workflow.validation_p50_ms)} / ${number(workflow.validation_p95_ms)}`,
      ],
    );
  if (knowledge)
    values.push(
      ['检索模式（合成资料）', knowledge.actual_modes.join(' / ')],
      ['Recall@K / MRR（合成）', `${rate(knowledge.recall_at_k)} / ${number(knowledge.mrr)}`],
      ['来源核验准确率（合成）', rate(knowledge.citation_accuracy)],
      ['引用覆盖率（合成）', rate(knowledge.citation_coverage)],
      ['回答支持度（固定 Mock）', rate(knowledge.answer_groundedness)],
      [
        '检索 P50 / P95 ms',
        `${number(knowledge.retrieval_p50_ms)} / ${number(knowledge.retrieval_p95_ms)}`,
      ],
      ['索引文档 / 秒', number(knowledge.index_documents_per_second)],
      ['增量更新正确率', rate(knowledge.update_correctness)],
      ['越权读取数', String(knowledge.unauthorized_access_count)],
      ['记忆范围隔离率', rate(knowledge.memory_isolation)],
    );
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
