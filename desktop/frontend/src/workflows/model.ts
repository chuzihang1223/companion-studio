import type { NodeType, WorkflowDefinition, WorkflowNode, WorkflowEdge } from './types';

export const nodeTypes: Array<{ type: NodeType; title: string; group: string; help: string }> = [
  {
    type: 'manual_trigger',
    title: '手动触发',
    group: '输入与输出',
    help: '用户明确运行，不能设置定时任务。',
  },
  {
    type: 'input',
    title: '输入参数',
    group: '输入与输出',
    help: '读取运行表单中已声明和校验的参数。文件只接受所选工作目录内的引用。',
  },
  {
    type: 'output',
    title: '输出结果',
    group: '输入与输出',
    help: '展示实际执行结果，支持文本和 JSON。不会自动写文件。',
  },
  {
    type: 'llm_agent',
    title: '模型分析',
    group: 'Agent 与工具',
    help: '使用现有模型配置分析当前输入；可能产生模型费用。这个节点不自行调用工具。',
  },
  {
    type: 'tool',
    title: '内置工具',
    group: 'Agent 与工具',
    help: '经过原工具注册表、权限和检查点。实际参数解析后授权。',
  },
  {
    type: 'mcp_tool',
    title: 'MCP 工具',
    group: 'Agent 与工具',
    help: '只使用已手动连接、启用且定义未变化的工具；协议成功不等于目标验证成功。',
  },
  {
    type: 'agent_skill',
    title: 'Agent Skill',
    group: 'Agent 与工具',
    help: '按需加载已启用 Skill 作为模型分析说明；不运行脚本、不提升工具权限。',
  },
  {
    type: 'condition',
    title: '条件分支',
    group: '控制与数据',
    help: '使用受限比较表达式，分别从 true / false 出口继续；不能执行代码。',
  },
  {
    type: 'transform',
    title: '数据转换',
    group: '控制与数据',
    help: '字段选择、拼接、默认值、数值、长度、映射与 JSON 提取。',
  },
  {
    type: 'human_approval',
    title: '人工审批',
    group: '控制与数据',
    help: '等待用户明确批准或拒绝；这个批准不会授权后续工具。',
  },
  {
    type: 'merge',
    title: '分支汇合',
    group: '控制与数据',
    help: '等待已激活分支完成，跳过未激活分支。首个可用按拓扑顺序确定，当前串行执行。',
  },
];
const defaults: Record<NodeType, Record<string, unknown>> = {
  manual_trigger: {},
  input: { name: 'text', type: 'string', required: true },
  output: { value: '工作流已执行', format: 'text' },
  llm_agent: {
    instruction: '分析提供的数据并给出简洁建议。',
    value: '',
    model_role: 'planning_model',
    max_characters: 16000,
  },
  tool: { name: 'workspace.read', arguments: { path: 'README.md' }, coding_mode: 'virtual_only' },
  mcp_tool: { tool_id: '', server_id: '', schema_fingerprint: '', arguments: {} },
  agent_skill: { skill_id: '', content_hash: '', goal: '分析提供的数据。', value: '' },
  condition: { expression: { op: 'equals', left: true, right: true } },
  transform: { operation: 'concat', value: ['你好', 'Studio'], separator: ' ' },
  human_approval: { message: '请检查当前内容后选择是否继续。', value: '' },
  merge: { mode: 'all_active' },
};
export function makeNode(type: NodeType, position = { x: 80, y: 80 }): WorkflowNode {
  return {
    node_id: 'node_' + crypto.randomUUID().replaceAll('-', '').slice(0, 12),
    type,
    title: nodeTypes.find((n) => n.type === type)!.title,
    description: '',
    position,
    input_ports: { in: 'any' },
    output_ports:
      type === 'condition'
        ? { true: 'boolean', false: 'boolean', error: 'object' }
        : { out: 'any', error: 'object' },
    config: structuredClone(defaults[type]),
    timeout: 60,
    retry_policy: { max_attempts: 1, strategy: 'stop' },
    permission_requirement: 'runtime',
    metadata: {},
  };
}
export function emptyWorkflow(): WorkflowDefinition {
  return {
    workflow_id: crypto.randomUUID(),
    name: '新工作流',
    description: '',
    schema_version: 1,
    version: 0,
    status: 'DRAFT',
    nodes: [],
    edges: [],
    input_schema: { type: 'object', properties: {}, additionalProperties: false },
    output_schema: {},
    required_capabilities: [],
    budget: {
      max_steps: 100,
      max_duration_seconds: 600,
      max_model_calls: 10,
      max_total_tokens: 32768,
      max_tool_calls: 40,
      max_intermediate_bytes: 262144,
    },
  };
}
export function addEdge(
  graph: WorkflowDefinition,
  source: string,
  port: string,
  target: string,
  targetPort = 'in',
): WorkflowDefinition {
  if (
    graph.edges.length >= 200 ||
    source === target ||
    graph.edges.some(
      (e) =>
        e.source_node_id === source &&
        e.source_port === port &&
        e.target_node_id === target &&
        e.target_port === targetPort,
    )
  )
    throw Error('不能连接自身、重复连线或超过 200 条边。');
  const a = graph.nodes.find((n) => n.node_id === source),
    b = graph.nodes.find((n) => n.node_id === target);
  if (!a?.output_ports[port] || !b?.input_ports[targetPort]) throw Error('端口不存在。');
  const seen = new Set<string>();
  const pending = [target];
  while (pending.length) {
    const id = pending.pop()!;
    if (id === source) throw Error('这条连线会形成循环。');
    if (seen.has(id)) continue;
    seen.add(id);
    pending.push(
      ...graph.edges.filter((e) => e.source_node_id === id).map((e) => e.target_node_id),
    );
  }
  const edge: WorkflowEdge = {
    edge_id: 'edge_' + crypto.randomUUID().replaceAll('-', ''),
    source_node_id: source,
    source_port: port,
    target_node_id: target,
    target_port: targetPort,
    condition_label: ['true', 'false', 'error'].includes(port)
      ? (port as 'true' | 'false' | 'error')
      : '',
  };
  return { ...graph, edges: [...graph.edges, edge] };
}
export function removeNodes(graph: WorkflowDefinition, selected: string[]): WorkflowDefinition {
  const ids = new Set(selected);
  return {
    ...graph,
    nodes: graph.nodes.filter((n) => !ids.has(n.node_id)),
    edges: graph.edges.filter((e) => !ids.has(e.source_node_id) && !ids.has(e.target_node_id)),
  };
}
export function duplicateNodes(graph: WorkflowDefinition, selected: string[]): WorkflowDefinition {
  const ids = new Map(
    selected.map((id) => [id, 'node_' + crypto.randomUUID().replaceAll('-', '').slice(0, 12)]),
  );
  const nodes = graph.nodes.filter((n) => ids.has(n.node_id));
  if (graph.nodes.length + nodes.length > 100) throw Error('节点上限为 100。');
  function rewrite(value: unknown): unknown {
    if (typeof value === 'string')
      return value.replace(/nodes\.([A-Za-z][A-Za-z0-9_-]*)\.output/g, (all, id: string) =>
        ids.has(id) ? `nodes.${ids.get(id)}.output` : all,
      );
    if (Array.isArray(value)) return value.map(rewrite);
    if (value && typeof value === 'object')
      return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, rewrite(v)]));
    return value;
  }
  return {
    ...graph,
    nodes: [
      ...graph.nodes,
      ...nodes.map((n) => ({
        ...structuredClone(n),
        node_id: ids.get(n.node_id)!,
        position: { x: n.position.x + 30, y: n.position.y + 30 },
        config: rewrite(n.config) as Record<string, unknown>,
      })),
    ],
    edges: [
      ...graph.edges,
      ...graph.edges
        .filter((e) => ids.has(e.source_node_id) && ids.has(e.target_node_id))
        .map((e) => ({
          ...e,
          edge_id: 'edge_' + crypto.randomUUID().replaceAll('-', ''),
          source_node_id: ids.get(e.source_node_id)!,
          target_node_id: ids.get(e.target_node_id)!,
        })),
    ],
  };
}
export function fitView(nodes: WorkflowNode[], width: number, height: number) {
  if (!nodes.length) return { x: 40, y: 40, scale: 1 };
  const minX = Math.min(...nodes.map((n) => n.position.x)),
    minY = Math.min(...nodes.map((n) => n.position.y));
  const maxX = Math.max(...nodes.map((n) => n.position.x + 210)),
    maxY = Math.max(...nodes.map((n) => n.position.y + 126));
  const scale = Math.max(
    0.2,
    Math.min(1.5, (width - 70) / (maxX - minX), (height - 70) / (maxY - minY)),
  );
  return {
    x: (width - (maxX - minX) * scale) / 2 - minX * scale,
    y: (height - (maxY - minY) * scale) / 2 - minY * scale,
    scale,
  };
}

export function arrangeNodes(graph: WorkflowDefinition): WorkflowDefinition {
  const degree = new Map(graph.nodes.map((n) => [n.node_id, 0]));
  for (const edge of graph.edges)
    if (degree.has(edge.target_node_id))
      degree.set(edge.target_node_id, degree.get(edge.target_node_id)! + 1);
  const queue = [...degree.keys()].filter((id) => degree.get(id) === 0).sort(),
    order: string[] = [];
  while (queue.length) {
    const id = queue.shift()!;
    order.push(id);
    for (const edge of graph.edges.filter((e) => e.source_node_id === id)) {
      const count = (degree.get(edge.target_node_id) || 0) - 1;
      degree.set(edge.target_node_id, count);
      if (count === 0) queue.push(edge.target_node_id);
    }
  }
  for (const node of graph.nodes) if (!order.includes(node.node_id)) order.push(node.node_id);
  const columns = Math.min(10, Math.max(1, graph.nodes.length));
  return {
    ...graph,
    nodes: graph.nodes.map((n) => {
      const i = order.indexOf(n.node_id);
      return {
        ...n,
        position: { x: 50 + (i % columns) * 280, y: 60 + Math.floor(i / columns) * 180 },
      };
    }),
  };
}

export const stateLabels: Record<string, string> = {
  PENDING: '待调度',
  READY: '准备执行',
  RUNNING: '执行中',
  WAITING_PERMISSION: '等待审批',
  COMPLETED: '已完成',
  FAILED: '失败',
  SKIPPED: '未激活',
  PAUSED: '已暂停',
  CANCELLED: '已取消',
  INTERRUPTED: '已中断',
  NEEDS_REVIEW: '需要核查',
};
