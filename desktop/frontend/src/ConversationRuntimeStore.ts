import type { AttachmentInput, ChatMessage, AgentCall, AgentPlan } from './api';
export type PendingWork = {
  planId: string;
  calls: AgentCall[];
  approved: string[];
  conversationId: string | null;
};
export const rendererId = sessionStorage.getItem('renderer-id') || crypto.randomUUID();
sessionStorage.setItem('renderer-id', rendererId);
/** 仅为渲染器运行状态；完整历史由后端保存，草稿另按会话存 localStorage。 */
type Runtime = {
  draft: string;
  attachments: AttachmentInput[];
  messages: ChatMessage[] | null;
  generating: boolean;
  status: string;
  error: string;
  completed: boolean;
  queued: number;
  pending: PendingWork | null;
  apps: AgentPlan | null;
  work_session_id: string | null;
  voice_state: string;
  pending_request_id: string | null;
  is_streaming: boolean;
};
/** 导航不转移请求归属；React 可见状态只是当前会话的投影。 */
export class ConversationRuntimeStore {
  private rows = new Map<string, Runtime>();
  private queues = new Map<string, Promise<unknown>>();
  private listeners = new Set<() => void>();
  get(id: string): Runtime {
    let row = this.rows.get(id);
    if (!row) {
      row = {
        draft: localStorage.getItem('draft:' + id) || '',
        attachments: [],
        messages: null,
        generating: false,
        status: '',
        error: '',
        completed: false,
        queued: 0,
        pending: null,
        apps: null,
        work_session_id: null,
        voice_state: 'idle',
        pending_request_id: null,
        is_streaming: false,
      };
      this.rows.set(id, row);
    }
    return row;
  }
  subscribe(callback: () => void) {
    this.listeners.add(callback);
    return () => {
      this.listeners.delete(callback);
    };
  }
  changed() {
    for (const listener of this.listeners) listener();
  }
  patch(id: string, patch: Partial<Runtime>) {
    Object.assign(this.get(id), patch);
    this.changed();
  }
  draft(id: string, text: string) {
    this.get(id).draft = text;
    localStorage.setItem('draft:' + id, text);
  }
  messages(id: string, update: (rows: ChatMessage[]) => ChatMessage[]) {
    const row = this.get(id);
    row.messages = update(row.messages || []);
    this.changed();
    return row.messages;
  }
  /** 同一会话串行，不同会话使用独立 Promise 链；前次失败不阻断后续任务。 */
  async enqueue(id: string, run: () => Promise<unknown>) {
    const row = this.get(id);
    row.queued++;
    this.changed();
    const predecessor = this.queues.get(id) || Promise.resolve();
    const task = predecessor
      .catch(() => {})
      .then(async () => {
        row.queued--;
        row.generating = true;
        row.error = '';
        row.completed = false;
        this.changed();
        try {
          return await run();
        } finally {
          row.generating = false;
          if (!row.pending && !row.apps) {
            row.status = '';
            row.completed = true;
          }
          this.changed();
        }
      });
    this.queues.set(id, task);
    try {
      return await task;
    } finally {
      if (this.queues.get(id) === task) this.queues.delete(id);
    }
  }
  running() {
    return [...this.rows].filter(([, row]) => row.generating || row.queued).map(([id]) => id);
  }
}
