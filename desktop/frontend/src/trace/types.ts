export type TraceStatus =
  | 'planning'
  | 'ready'
  | 'queued'
  | 'running'
  | 'paused'
  | 'waiting_permission'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted';
export type TraceMetadata = Record<
  string,
  string | number | boolean | null | Record<string, string>
>;
export type TraceEvidence = { kind: 'boolean' | 'count'; name: string; value: boolean | number };
export type AgentTrace = {
  trace_id: string;
  task_id: string;
  conversation_id: string | null;
  work_session_id: string | null;
  status: TraceStatus;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  event_count: number;
  span_count: number;
  dropped_events: number;
  incomplete: boolean;
  schema_version: number;
};
export type TraceSpan = {
  span_id: string;
  parent_span_id: string | null;
  kind: string;
  phase: string;
  tool_name: string | null;
  model_name: string | null;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  status: string;
  error_code: string | null;
  error_summary: string | null;
  retry_count: number;
  verification_status: string;
  metadata: TraceMetadata;
  evidence: TraceEvidence[];
};
export type TraceEvent = {
  sequence: number;
  trace_id: string;
  task_id: string;
  conversation_id: string | null;
  work_session_id: string | null;
  span_id: string | null;
  event_type: string;
  phase: string;
  status: string;
  created_at: string;
  duration_ms: number | null;
  error_code: string | null;
  error_summary: string | null;
  verification_status: string;
  metadata: TraceMetadata;
  evidence: TraceEvidence[];
  span: TraceSpan | null;
  source_work_event_id: number | null;
  schema_version: number;
};
export type TracePage<T> = {
  integrity?: { pending: number; needs_review: number; failed: number } | null;
  items: T[];
  next_cursor: number | null;
  has_more: boolean;
  limit: number;
  schema_version: number;
};
export type TraceSummary = {
  trace_id: string;
  task_total_ms: number | null;
  model_ms: number;
  tool_ms: number;
  scheduler_ms: number;
  permission_wait_ms: number;
  recovery_ms: number;
  model_requests: number;
  tool_requests: number;
  tools_executed: number;
  verification_passes: number;
  verification_failures: number;
  retries: number;
  incomplete: boolean;
  schema_version: number;
};
export type TraceQuery = {
  conversation_id: string;
  work_session_id?: string;
  trace_id?: string;
  task_id?: string;
  status?: TraceStatus;
  tool_name?: string;
  since?: string;
  until?: string;
  cursor?: number;
  after?: number;
  limit?: number;
};
