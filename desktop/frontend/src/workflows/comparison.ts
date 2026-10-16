import type { WorkflowRun } from './types';

// 比较只读取两次历史记录。缺失的旧版本条件标为未知，不以当前设置补写历史。
export function compareRuns(left: WorkflowRun, right: WorkflowRun) {
  if (left.workflow_id !== right.workflow_id || left.conversation_id !== right.conversation_id)
    throw Error('只能对比当前会话中同一工作流的运行。');
  const differences = [
    ...[
      'app_version',
      'git_commit',
      'prompt_version',
      'schema_version',
      'models',
      'dependencies',
      'budget',
      'permission_mode',
      'required_capabilities',
      'environment',
      'dataset_version',
    ].filter(
      (key) =>
        JSON.stringify(left.execution_snapshot?.[key]) !==
        JSON.stringify(right.execution_snapshot?.[key]),
    ),
    ...(JSON.stringify(left.inputs) !== JSON.stringify(right.inputs) ? ['inputs'] : []),
    ...(JSON.stringify(left.nodes.map((n) => [n.node_id, n.observed_model])) !==
    JSON.stringify(right.nodes.map((n) => [n.node_id, n.observed_model]))
      ? ['observed_models']
      : []),
  ];
  return { differences, conditionsKnown: !!left.execution_snapshot && !!right.execution_snapshot };
}
