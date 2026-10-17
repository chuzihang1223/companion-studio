import type { WorkflowRun } from './types';

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object')
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, item]) => [key, canonical(item)]),
    );
  return value;
}
const same = (a: unknown, b: unknown) =>
  JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
const observedModels = (run: WorkflowRun) =>
  run.nodes
    .filter((n) => n.observed_model)
    .map((n) => [n.node_id, n.observed_model])
    .sort(([a], [b]) => String(a).localeCompare(String(b)));

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
    ].filter((key) => !same(left.execution_snapshot?.[key], right.execution_snapshot?.[key])),
    ...(!same(left.inputs, right.inputs) ? ['inputs'] : []),
    ...(!same(observedModels(left), observedModels(right)) ? ['observed_models'] : []),
  ];
  return { differences, conditionsKnown: !!left.execution_snapshot && !!right.execution_snapshot };
}
