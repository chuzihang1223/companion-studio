export type EvaluationMode = 'mock' | 'sandbox' | 'windows_gui' | 'live_model';
export type EvaluationOptions = {
  visual_consent?: string;
  dataset_id?:
    | 'studio-core'
    | 'recovery-core'
    | 'mcp-skills-core'
    | 'workflow-core'
    | 'knowledge-core'
    | 'computer-use-core'
    | 'scheduler-core'
    | 'voice-core'
    | 'router-core';
  case_ids?: string[];
  mode?: EvaluationMode;
  model_role?: string;
  repetitions?: number;
  concurrency?: number;
  timeout_seconds?: number;
  max_duration_seconds?: number;
  max_requests?: number;
  token_budget?: number;
  live_consent?: boolean;
  knowledge_mode?: 'KeywordOnly' | 'HybridLocal' | 'HybridRemote' | 'MockRAG';
};
export type BenchmarkCase = {
  input: string;
  case_id: string;
  title: string;
  category: string;
  description: string;
  execution_mode: EvaluationMode[];
};
export type Dataset = {
  dataset_id: string;
  dataset_version: string;
  title: string;
  case_count: number;
  cases?: BenchmarkCase[];
};
export type Usage = { status: string; total_tokens: number | null; observed_requests?: number };
export type EvaluationMetrics = {
  knowledge?: {
    recall_at_k: number | null;
    mrr: number | null;
    citation_accuracy: number | null;
    citation_coverage: number | null;
    answer_groundedness: number | null;
    retrieval_p50_ms: number | null;
    retrieval_p95_ms: number | null;
    index_documents_per_second: number | null;
    update_correctness: number | null;
    unauthorized_access_count: number;
    memory_isolation: number | null;
    actual_modes: string[];
    requested_modes: string[];
    real_model_quality: null;
  } | null;
  workflow?: {
    source: string;
    case_count: number;
    executed_workflows: number;
    completed_workflows: number;
    blocked_side_effects: number;
    validation_p50_ms: number | null;
    validation_p95_ms: number | null;
    real_ai_task_success_rate: null;
    estimated_cost: null;
  } | null;
  mcp?: {
    source: string;
    connection_success_rate: number | null;
    discovery_success_rate: number | null;
    tool_success_rate: number | null;
    schema_failures: number;
    reconnections: number;
    permission_denials: number;
    tool_latency_p50_ms: number | null;
    tool_latency_p95_ms: number | null;
    average_skill_load_ms: number | null;
    skill_match_accuracy: number | null;
    real_third_party_reliability: null;
  } | null;
  recovery?: {
    attempt_count: number;
    successful_attempts: number;
    success_rate: number | null;
    duration_ms: number;
    replanning_count: number;
    duplicate_execution_count: number;
    checkpoint_checks: number;
    validated_checkpoints: number;
    checkpoint_validation_rate: number | null;
    unresolved_side_effects: number;
    human_interventions: number;
    goal_completed_count: number;
    goal_completion_rate: number;
    cost: { value: number | null; status: string };
  } | null;
  source: string;
  counts: Record<string, number>;
  valid_count: number;
  total_count: number;
  success_rate: number | null;
  failure_rate: number | null;
  real_task_success_rate: number | null;
  average_duration_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  average_tool_calls: number | null;
  tool_calls: number;
  retry_count: number;
  recovery_success_rate: number | null;
  recovery_attempts: number;
  model_usage: Usage;
  api_cost: { status: string; value: number | null };
  failure_categories: Record<string, number>;
  excluded_statuses: Record<string, number>;
};
export type EvaluationRun = {
  run_id: string;
  status: string;
  mode: EvaluationMode;
  total_cases: number;
  revision: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  options: EvaluationOptions;
  metrics: Partial<EvaluationMetrics>;
  snapshot: {
    dataset_id: string;
    dataset_version: string;
    app_version: string;
    git_commit: string;
    model: { provider: string; model_id: string | null };
    environment: Record<string, unknown>;
  };
};
export type EvaluationResult = {
  attempt_id: string;
  run_id: string;
  case_id: string;
  repetition_index: number;
  execution_status: string;
  grader_status: string;
  duration_ms: number | null;
  trace_id: string | null;
  task_id: string | null;
  conversation_id: string | null;
  work_session_id: string | null;
  error_code: string | null;
  result: {
    completion_status?: string;
    passed?: boolean | null;
    explanation?: string;
    tool_calls?: number;
    retry_count?: number;
    evidence?: Record<string, string | number | boolean | null>;
    dimensions?: Record<string, boolean | null>;
  };
};
export type Page<T> = { items: T[]; next_cursor: number | null };
export type Comparison = {
  left: EvaluationRun;
  right: EvaluationRun;
  conditions_equal: boolean;
  dataset_compatible: boolean;
  warning: string;
  differences: { field: string; left: unknown; right: unknown }[];
  cases: { left: EvaluationResult[]; right: EvaluationResult[] };
};
export type EvaluationQuery = {
  dataset_id?:
    | 'studio-core'
    | 'recovery-core'
    | 'mcp-skills-core'
    | 'workflow-core'
    | 'knowledge-core'
    | 'computer-use-core'
    | 'scheduler-core'
    | 'voice-core'
    | 'router-core';
  trace_id?: string;
  run_id?: string;
  attempt_id?: string;
  cursor?: number;
  limit?: number;
  left?: string;
  right?: string;
};
export type ModelChoice = { role: string; model_id: string | null; configured: boolean };
export type EvaluationReads = {
  datasets: { items: Dataset[] };
  dataset: Dataset;
  models: { items: ModelChoice[] };
  runs: Page<EvaluationRun>;
  detail: EvaluationRun;
  results: Page<EvaluationResult>;
  metrics: EvaluationMetrics;
  compare: Comparison;
  'trace-list': import('../trace/types').TracePage<import('../trace/types').AgentTrace>;
  'trace-detail': import('../trace/types').AgentTrace;
  'trace-events': import('../trace/types').TracePage<import('../trace/types').TraceEvent>;
  'trace-summary': import('../trace/types').TraceSummary;
};
export type EvaluationProgress = { event: string; run_id: string; case_id: string | null };
