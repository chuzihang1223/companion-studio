import type { ResumePreview } from '../workflows/types';
export type TriggerKind = 'ONCE' | 'DAILY' | 'WEEKLY' | 'INTERVAL' | 'CONDITION';
export type Trigger = {
  at?: string;
  time?: string;
  weekdays?: number[];
  interval_seconds?: number;
  condition_type?: 'FILE_CHANGE' | 'WORKFLOW_STATE';
  source_id?: string;
  poll_interval?: number;
  debounce?: number;
  cooldown?: number;
  expected_value?: 'COMPLETED' | 'FAILED';
};
export type Definition = {
  schedule_id: string;
  name: string;
  description?: string;
  conversation_id: string;
  workflow_id: string;
  workflow_version_id: string;
  inputs: Record<string, unknown>;
  trigger_type: TriggerKind;
  trigger_config: Trigger;
  timezone: string;
  enabled: boolean;
  misfire_policy: 'SKIP_MISSED' | 'RUN_ONCE_AFTER_RESUME' | 'REQUIRE_REVIEW';
  concurrency_policy: 'SKIP_IF_RUNNING' | 'QUEUE_ONE' | 'REJECT_OVERLAP';
  notification: boolean;
};
export type Snapshot = {
  definition_hash: string;
  may_use_models: boolean;
  grants_permissions: false;
  nodes: Array<{
    node_id: string;
    type: string;
    tool: string | null;
    requires_interaction: boolean;
  }>;
};
export type Saved = {
  definition: Definition;
  revision: number;
  state: string;
  snapshot: Snapshot;
  next_due: string | null;
  generated: number;
};
export type Occurrence = {
  occurrence_id: string;
  schedule_id: string;
  owner_id: string;
  status: string;
  scheduled_for: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  last_error: string | null;
  workflow_run_id: string | null;
  summary: {
    task_id?: string;
    trace_id?: string;
    workflow_version?: string;
    duration_ms?: number;
    recovery_count?: number;
    verification_result?: string;
    checkpoint_task_ids?: string[];
  } | null;
};
export type Source = {
  source_id: string;
  condition_type: 'FILE_CHANGE' | 'WORKFLOW_STATE';
  enabled: boolean;
  expires_at: string;
  workflow_run_id?: string;
};
export type Navigation = {
  conversation_id?: string;
  schedule_id?: string;
  occurrence_id?: string;
  recent?: boolean;
};
export type Status = {
  running: boolean;
  paused: boolean;
  queued: number;
  waiting_permission: number;
  error_code: string | null;
};
export type Background = { enabled: boolean; tray_available: boolean; startup_supported: false };
export type Preview = { times: Array<{ utc: string; local: string }>; permissions: Snapshot };
export type History = {
  items: Occurrence[];
  events: Array<{ cursor: number; kind: string; created_at: string; occurrence_id: string | null }>;
};
export type Reads = {
  status: Status;
  owners: { items: Array<{ conversation_id: string; title: string }> };
  recent: { items: Array<Navigation & { kind: string; created_at: string }> };
  schedules: { items: Saved[] };
  history: History;
  sources: { items: Source[] };
  notifications: {
    items: Array<Navigation & { notification_id: string; kind: string; delivery: string }>;
  };
  'recovery-preview': ResumePreview;
};
export type Operation =
  | 'save'
  | 'preview'
  | 'state'
  | 'run-now'
  | 'approve'
  | 'cancel'
  | 'resume'
  | 'pause-all'
  | 'source'
  | 'revoke-source';
export const labels: Record<string, string> = {
  ENABLED: '已启用',
  PAUSED: '已暂停',
  DISABLED: '已禁用',
  ARCHIVED: '已删除（保留历史）',
  QUEUED: '排队中',
  PENDING: '待触发',
  DUE: '已到期',
  CLAIMED: '已领取',
  RUNNING: '运行中',
  WAITING_PERMISSION: '等待授权',
  WAITING_ENVIRONMENT: '等待桌面环境',
  COMPLETED: '已完成',
  FAILED: '失败',
  SKIPPED: '已跳过',
  CANCELLED: '已取消',
  INTERRUPTED: '已中断',
  NEEDS_REVIEW: '需要核查',
  MISSED: '已错过',
  ONCE: '一次性',
  DAILY: '每天',
  WEEKLY: '每周',
  INTERVAL: '固定间隔',
  CONDITION: '条件监控',
};
export const shownTime = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString() : '—';
export function newDefinition(owner: string): Definition {
  const at = new Date(Date.now() + 60000);
  const local =
    [
      at.getFullYear(),
      String(at.getMonth() + 1).padStart(2, '0'),
      String(at.getDate()).padStart(2, '0'),
    ].join('-') +
    'T' +
    [at.getHours(), at.getMinutes(), at.getSeconds()]
      .map((value) => String(value).padStart(2, '0'))
      .join(':');
  return {
    schedule_id: crypto.randomUUID(),
    name: '',
    conversation_id: owner,
    workflow_id: '',
    workflow_version_id: '',
    inputs: {},
    trigger_type: 'ONCE',
    trigger_config: { at: local },
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Etc/UTC',
    enabled: true,
    misfire_policy: 'SKIP_MISSED',
    concurrency_policy: 'SKIP_IF_RUNNING',
    notification: true,
  };
}
