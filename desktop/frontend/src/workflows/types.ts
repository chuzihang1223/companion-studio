export type NodeType =
  | 'knowledge_search'
  | 'manual_trigger'
  | 'input'
  | 'llm_agent'
  | 'tool'
  | 'mcp_tool'
  | 'agent_skill'
  | 'condition'
  | 'transform'
  | 'human_approval'
  | 'merge'
  | 'output';
export type WorkflowNode = {
  node_id: string;
  type: NodeType;
  title: string;
  description: string;
  position: { x: number; y: number };
  input_ports: Record<string, string>;
  output_ports: Record<string, string>;
  config: Record<string, unknown>;
  timeout: number;
  retry_policy: { max_attempts: number; strategy: 'stop' | 'safe_read' };
  permission_requirement: 'runtime';
  metadata: Record<string, string>;
};
export type WorkflowEdge = {
  edge_id: string;
  source_node_id: string;
  source_port: string;
  target_node_id: string;
  target_port: string;
  condition_label: '' | 'true' | 'false' | 'error';
};
export type WorkflowDefinition = {
  workflow_id: string;
  name: string;
  description: string;
  schema_version: 1;
  version: number;
  status: 'DRAFT' | 'PUBLISHED' | 'ARCHIVED';
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  required_capabilities: string[];
  budget: {
    max_steps: number;
    max_duration_seconds: number;
    max_model_calls: number;
    max_total_tokens: number;
    max_tool_calls: number;
    max_intermediate_bytes: number;
  };
};
export type SavedWorkflow = {
  definition: WorkflowDefinition;
  revision: number;
  updated_at: string;
};
export type WorkflowVersion = { version_id: string; version: number; digest: string };
export type NodeRun = {
  trace_id?: string | null;
  node_id: string;
  node_execution_id: string;
  state: string;
  attempt: number;
  output: unknown;
  duration_ms: number;
  error_code: string | null;
  pending?: {
    approval_id: string;
    node_execution_id: string;
    kind: 'tool' | 'human';
    message?: string;
    value?: unknown;
    call?: { name: string; arguments: Record<string, unknown> };
    risk?: string;
  };
  recovery_task_id?: string;
  goal_verified?: boolean;
  scheduling_ms?: number;
  observed_model?: Record<string, unknown> | null;
};
export type WorkflowRun = {
  run_id: string;
  workflow_id: string;
  version_id: string;
  version: number;
  conversation_id: string;
  work_session_id: string;
  task_id: string;
  trace_id: string | null;
  state: string;
  nodes: NodeRun[];
  inputs: Record<string, unknown>;
  output: unknown;
  revision: number;
  created_at: string;
  error_code: string | null;
  model_calls: number;
  tool_calls: number;
  active_ms: number;
  attempt: number;
  validation_ms?: number;
  compile_ms?: number;
  queue_wait_ms?: number;
  model_usage?: { total_tokens?: number | null };
  estimated_cost?: number | null;
  execution_snapshot?: Record<string, unknown>;
};
export type WorkflowEvent = {
  cursor: number;
  node_id?: string;
  state?: string;
  kind: string;
  created_at?: string;
};
export type WorkflowProgress = Partial<WorkflowEvent> & {
  run_id: string;
  conversation_id: string;
  disconnected?: boolean;
};
export type ResumePreview = {
  run_id: string;
  revision: number;
  can_resume: boolean;
  items: Array<{
    node_id: string;
    node_execution_id: string;
    action: string;
    code: string | null;
    checkpoints: Array<{ checkpoint_id: string | null }>;
  }>;
};
export type WorkflowCatalog = {
  tools: Array<{
    name: string;
    description: string;
    schema: Record<string, unknown>;
    schema_fingerprint: string;
  }>;
  mcp_tools: Array<{
    id: string;
    server_id: string;
    name: string;
    description: string;
    enabled: boolean;
    schema_fingerprint: string;
    input_schema: Record<string, unknown>;
    risk_level: string;
  }>;
  skills: Array<{
    id: string;
    name: string;
    enabled: boolean;
    content_hash: string;
    description: string;
  }>;
};
export type Template = { template_id: string; name: string; description: string; license: string };
export type WorkflowQuery = {
  workflow_id?: string;
  version_id?: string;
  run_id?: string;
  conversation_id?: string;
  template_id?: string;
  after?: number;
};
export type WorkflowReads = {
  list: { items: SavedWorkflow[] };
  detail: SavedWorkflow;
  catalog: WorkflowCatalog;
  templates: { items: Template[] };
  template: WorkflowDefinition;
  versions: { items: WorkflowVersion[] };
  version: WorkflowDefinition;
  run: WorkflowRun;
  runs: { items: WorkflowRun[] };
  events: { items: WorkflowEvent[] };
  'resume-preview': ResumePreview;
};
export type WorkflowControl =
  | 'archive'
  | 'confirm-discard'
  | 'save'
  | 'validate'
  | 'publish'
  | 'delete'
  | 'import'
  | 'export'
  | 'start'
  | 'approve'
  | 'resume'
  | 'control';
