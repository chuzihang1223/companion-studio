import type { TraceEvent } from './types';

export const MAX_EVENTS = 5000;
export const ROW_HEIGHT = 58;
export const isFinished = (status: string) =>
  ['completed', 'failed', 'cancelled', 'interrupted'].includes(status);
export function orderedEvents(rows: TraceEvent[]): TraceEvent[] {
  return [...new Map(rows.map((row) => [row.sequence, row])).values()]
    .sort((a, b) => a.sequence - b.sequence)
    .slice(0, MAX_EVENTS);
}
export function replayDelay(current: TraceEvent, next: TraceEvent, speed: number): number {
  const elapsed = Date.parse(next.created_at) - Date.parse(current.created_at);
  // Idle gaps are shortened, visibly documented in the player. No side effects.
  return Math.min(
    2000,
    Math.max(80, (Number.isFinite(elapsed) ? elapsed : 200) / Math.max(0.25, speed)),
  );
}
export function virtualWindow(length: number, top: number, height: number) {
  const start = Math.max(0, Math.floor(top / ROW_HEIGHT) - 4);
  return { start, end: Math.min(length, start + Math.ceil(height / ROW_HEIGHT) + 8) };
}
export const traceStatuses: Record<string, string> = {
  planning: '规划中',
  ready: '待执行',
  queued: '等待执行',
  running: '执行中',
  paused: '已暂停',
  waiting_permission: '等待授权',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
  denied: '已拒绝',
};
export const eventNames: Record<string, string> = {
  VOICE_SESSION_STARTED: '语音会话已开始',
  AUDIO_CAPTURE_STARTED: '实际采集已开始',
  SPEECH_DETECTED: '检测到声音活动',
  ASR_PARTIAL_RECEIVED: '临时转写',
  ASR_REQUESTED: '开始识别终句',
  VOICE_AGENT_LINKED: '关联原工作任务',
  ASR_FINAL_RECEIVED: '终句转写',
  LLM_STREAM_STARTED: '回复生成已开始',
  TTS_CHUNK_REQUESTED: '提交语义段',
  TTS_CHUNK_READY: '音频片段已就绪',
  AUDIO_PLAYBACK_STARTED: '播放器实际开始',
  AUDIO_PLAYBACK_COMPLETED: '播放器已结束',
  VOICE_INTERRUPT_REQUESTED: '请求打断',
  VOICE_INTERRUPT_COMPLETED: '生成取消已完成',
  VOICE_SESSION_STOPPED: '语音会话已停止',
  VOICE_ERROR: '语音处理失败',
  WORKFLOW_CREATED: '创建工作流运行',
  WORKFLOW_VALIDATED: '流程校验通过',
  WORKFLOW_STARTED: '开始工作流',
  WORKFLOW_NODE_READY: '节点准备执行',
  WORKFLOW_NODE_STARTED: '执行节点',
  WORKFLOW_NODE_COMPLETED: '节点完成',
  WORKFLOW_NODE_FAILED: '节点失败',
  WORKFLOW_NODE_SKIPPED: '跳过未激活节点',
  WORKFLOW_CONDITION_EVALUATED: '条件分支判定',
  WORKFLOW_APPROVAL_REQUESTED: '请求节点审批',
  WORKFLOW_PAUSED: '工作流暂停',
  WORKFLOW_RESUMED: '工作流续接',
  WORKFLOW_COMPLETED: '工作流完成',
  WORKFLOW_FAILED: '工作流失败',
  WORKFLOW_CANCELLED: '工作流取消',

  MCP_CONNECTION_STARTED: '开始连接 MCP',
  MCP_CONNECTION_ESTABLISHED: 'MCP 已连接',
  MCP_CONNECTION_FAILED: 'MCP 连接失败',
  MCP_TOOLS_DISCOVERED: '发现 MCP 工具',
  MCP_TOOL_STARTED: '开始 MCP 调用',
  MCP_TOOL_COMPLETED: 'MCP 调用返回',
  MCP_TOOL_FAILED: 'MCP 调用失败',
  MCP_SERVER_DISCONNECTED: 'MCP 已断开',
  MCP_RECONNECT_ATTEMPTED: '重新建立 MCP 连接',
  SKILL_SELECTED: '选中 Skill',
  SKILL_LOADED: '按需加载 Skill',
  SKILL_EXECUTION_BLOCKED: 'Skill 已阻断',
  RECOVERY_DETECTED: '检测到恢复需求',
  RECOVERY_CLASSIFIED: '识别失败类型',
  RECOVERY_PLAN_CREATED: '生成恢复策略',
  RECOVERY_ATTEMPT_STARTED: '开始恢复尝试',
  RECOVERY_ATTEMPT_FAILED: '恢复尝试失败',
  RECOVERY_ATTEMPT_SUCCEEDED: '恢复验证通过',
  CHECKPOINT_CREATED: '保存执行意图',
  CHECKPOINT_VALIDATED: '检查点已核验',
  CHECKPOINT_CONFLICT: '检查点冲突',
  RESUME_REQUESTED: '请求续接任务',
  RESUME_STARTED: '开始安全续接',
  RESUME_BLOCKED: '续接已阻断',
  RESUME_COMPLETED: '续接结束',
  USER_TAKEOVER_REQUESTED: '请求人工接管',
  TASK_CREATED: '创建任务',
  TASK_PLANNING: '开始规划',
  PLANNING_COMPLETED: '规划完成',
  MODEL_REQUEST_STARTED: '请求模型',
  MODEL_REQUEST_COMPLETED: '模型返回',
  MODEL_REQUEST_FAILED: '模型失败',
  TOOL_STARTED: '开始工具',
  TOOL_COMPLETED: '工具完成',
  TOOL_FAILED: '工具失败',
  OPERATION_STARTED: '执行操作',
  OPERATION_COMPLETED: '操作完成',
  OPERATION_FAILED: '操作失败',
  OBSERVATION_CREATED: '观察记录',
  VERIFICATION_STARTED: '开始验证',
  VERIFICATION_PASSED: '验证通过',
  VERIFICATION_FAILED: '验证失败',
  RETRY_STARTED: '重试',
  PERMISSION_REQUESTED: '请求授权',
  PERMISSION_RESOLVED: '授权已处理',
  TASK_PAUSED: '任务暂停',
  TASK_RESUMED: '任务继续',
  TASK_QUEUED: '任务排队',
  TASK_RUNNING: '开始执行',
  TASK_INTERRUPTED: '任务中断',
  TASK_COMPLETED: '任务完成',
  TASK_FAILED: '任务失败',
  TASK_CANCELLED: '任务取消',
};
