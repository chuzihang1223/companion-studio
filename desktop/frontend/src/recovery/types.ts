export type Checkpoint = {
  checkpoint_id: string;
  operation_id: string;
  step_index: number;
  tool_call_id: string;
  tool_name: string;
  step_state: string;
  side_effect_class: string;
  permission_state: string;
  execution_intent: Record<string, string>;
  result_summary: Record<string, boolean | number | null>;
  updated_at: string;
};
export type RecoveryTask = {
  task_id: string;
  conversation_id: string;
  work_session_id: string;
  execution_id: string;
  trace_id: string | null;
  goal: string;
  state: string;
  revision: number;
  failure: { error_code?: string; message?: string };
  counters: Record<string, number>;
  checkpoints: Checkpoint[];
  completed_steps: number;
  updated_at: string;
};
export type RecoveryPreview = {
  task_id: string;
  revision: number;
  can_resume: boolean;
  extra_model_requests: number;
  summary: string;
  items: {
    step_index: number;
    tool_call_id: string;
    tool_name: string;
    action: string;
    error_code: string | null;
    requires_approval: boolean;
  }[];
};
export type RecoveryScope = { conversation_id: string; work_session_id?: string; task_id?: string };
export type RecoveryReads = {
  list: { items: RecoveryTask[] };
  detail: RecoveryTask;
  checkpoints: { items: Checkpoint[] };
  preview: RecoveryPreview;
};
export type RecoveryAction =
  'resume' | 'cancel' | 'revalidate' | 'restart' | 'pause' | 'continue' | 'takeover';
export type RecoveryControl = {
  conversation_id: string;
  work_session_id: string;
  request_id: string;
  revision: number;
  approved_ids: string[];
  confirm_model_requests: boolean;
};
export const states: Record<string, string> = {
  INTERRUPTED: '已中断',
  RECOVERY_AVAILABLE: '可安全续接',
  REVALIDATING: '重新核验中',
  WAITING_USER: '等待用户',
  RESUMING: '续接中',
  RECOVERING: '执行 / 恢复中',
  COMPLETED: '已验证完成',
  FAILED: '未完成',
  CANCELLED: '已取消',
  UNRECOVERABLE: '不能恢复',
  NEEDS_REVIEW: '需要人工检查',
};
export const actions: Record<string, string> = {
  EXECUTE: '重新检查授权后执行',
  SKIP_VERIFIED: '核验通过，跳过重复执行',
  REVIEW: '停止自动操作，人工检查',
};
