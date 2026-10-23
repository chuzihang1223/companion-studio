import * as api from './api';
import type { AttachmentInput, ChatMessage, CharacterInfo, AgentPlan } from './api';
import { ConversationRuntimeStore } from './ConversationRuntimeStore';
import type { AudioPlaybackService } from './AudioPlaybackService';
import { VoicePresentationService } from './VoicePresentationService';
import { FirstSpeechGate } from './FirstSpeechGate';

/** 提交时的角色、模式和附件快照，不从后续切换的可见页面重新取值。 */
export type Submission = {
  voiceContext?: import('./realtimeVoice/types').VoiceBinding;
  stillCurrent?: () => boolean;
  onVoiceReply?: (id: string) => void;
  onVoiceState?: (identity: import('./realtimeVoice/types').VoiceIdentity) => void;
  conversationId: string | null;
  character: CharacterInfo;
  text: string;
  mode: 'chat' | 'agent';
  folder: string | null;
  attachments: AttachmentInput[];
  replyTo?: string;
  voice: boolean;
  expression: api.ExpressionLevel;
  speed: number;
  volume: number;
  prefetch: boolean;
  autoWork: boolean;
};
type Hooks = {
  visible: (id: string) => boolean;
  created: (origin: string | null, id: string, mode: 'chat' | 'agent', characterId: string) => void;
  messages: (id: string, rows: ChatMessage[]) => void;
  work: (id: string, session: api.WorkSession) => void;
  changed: () => void;
};
/** 请求归属固定；导航分离播放展示，后台仍可消费流并保存自己的语音。 */
export class ConversationSubmissionService {
  private requests = new Map<
    string,
    { conversationId: string; abort: AbortController; workId?: string }
  >();
  private voices = new Set<VoicePresentationService>();
  private gates = new Map<string, { id: string; gate: FirstSpeechGate }>();
  private audible = '';
  private creating = new Map<string, Promise<api.Conversation>>();
  // Permission/candidate UI supplies only an approval, never a replacement of
  // the original voice/character/consent snapshot. Cleared on execution/stop.
  private pendingRequests = new Map<string, Submission>();
  constructor(
    private store: ConversationRuntimeStore,
    private playback: AudioPlaybackService,
    private hooks: Hooks,
    private sdk: typeof api = api,
  ) {}
  private rows(id: string, update: (rows: ChatMessage[]) => ChatMessage[]) {
    this.hooks.messages(id, this.store.messages(id, update));
  }
  private error(id: string, cause: unknown) {
    this.store.patch(id, { error: cause instanceof Error ? cause.message : '请求失败' });
    this.hooks.changed();
  }
  silence() {
    this.audible = '';
    for (const voice of this.voices) voice.detach();
    this.playback.stop();
  }
  audioStarted(id: string) {
    for (const row of this.gates.values()) if (row.id === id) row.gate.audioStarted();
  }
  async submit(request: Submission) {
    const route = await this.sdk.routeAgent(
      request.text,
      request.mode === 'chat' && request.autoWork ? 'auto' : request.mode,
    );
    if (request.stillCurrent && !request.stillCurrent()) return request.conversationId;
    if (request.voiceContext && route === 'agent' && request.mode === 'chat')
      throw Error('请先切换到工作模式，再开启语音执行任务。');
    let id = request.conversationId;
    if (!id || (route === 'agent' && request.mode === 'chat')) {
      const key = request.character.id + ':' + route;
      let pending = this.creating.get(key);
      if (!pending) {
        pending = this.sdk.createConversation(
          request.character.id,
          route === 'agent' ? 'work' : 'chat',
        );
        this.creating.set(key, pending);
      }
      const created = await pending.finally(() => {
        if (this.creating.get(key) === pending) this.creating.delete(key);
      });
      id = created.id;
      this.hooks.created(
        request.conversationId,
        id,
        route === 'agent' ? 'agent' : 'chat',
        request.character.id,
      );
    }
    const owner = id;
    if (!this.store.get(owner).messages)
      this.store.get(owner).messages = await this.sdk.loadConversation(owner);
    if (request.stillCurrent && !request.stillCurrent()) return owner;
    void this.store
      .enqueue(owner, () => this.run({ ...request, conversationId: owner }, route))
      .catch((cause) => this.error(owner, cause));
    this.hooks.changed();
    return owner;
  }
  private async run(request: Submission, route: 'chat' | 'agent') {
    if (request.stillCurrent && !request.stillCurrent()) return;
    const id = request.conversationId!,
      ticket = crypto.randomUUID(),
      user = 'pending-user-' + ticket,
      reply = 'pending-assistant-' + ticket;
    this.store.patch(id, {
      pending_request_id: ticket,
      is_streaming: true,
      status:
        route === 'agent'
          ? /^(?:请|帮我)?(?:打开|启动)/.test(request.text)
            ? '正在查找应用或网页…'
            : '正在规划任务…'
          : '正在回复…',
    });
    if (this.hooks.visible(id)) {
      this.silence();
      this.audible = ticket;
    }
    this.rows(id, (rows) => [
      ...rows,
      {
        id: user,
        character_id: request.character.id,
        role: 'user',
        content: request.text,
        created_at: new Date().toISOString(),
      },
      {
        id: reply,
        character_id: request.character.id,
        role: 'assistant',
        content: '',
        created_at: new Date().toISOString(),
      },
    ]);
    try {
      if (route === 'agent') {
        const plan = await this.sdk.planAgent(
          request.character.id,
          id,
          request.text,
          request.attachments,
          request.folder,
          request.voiceContext,
        );
        if (request.stillCurrent && !request.stillCurrent()) {
          if (plan.work_session_id) await this.sdk.stopWorkSession(plan.work_session_id);
          return;
        }
        this.rows(id, () =>
          this.store.get(id).messages!.filter((row) => row.id !== user && row.id !== reply),
        );
        this.rows(id, () => this.store.get(id).messages || []);
        const history = await this.sdk.loadConversation(id);
        this.rows(id, () => history);
        await this.applyPlan(plan, request);
      } else
        await this.consume(
          request,
          ticket,
          user,
          reply,
          (signal) =>
            this.sdk.streamMessage(
              request.character.id,
              request.text,
              request.expression,
              request.voice,
              id,
              request.replyTo,
              request.speed,
              request.prefetch,
              request.attachments,
              signal,
              request.voiceContext,
            ),
          false,
        );
    } catch (cause) {
      this.error(id, cause);
      const history = await this.sdk.loadConversation(id).catch(() => null);
      if (history) this.rows(id, () => history);
    } finally {
      this.store.patch(id, { is_streaming: false, pending_request_id: null });
      this.hooks.changed();
    }
  }
  private async applyPlan(plan: AgentPlan, request: Submission) {
    const id = request.conversationId!;
    this.store.patch(id, { work_session_id: plan.work_session_id || null });
    if (plan.work_session_id)
      this.hooks.work(id, await this.sdk.loadWorkSession(plan.work_session_id));
    if (!plan.calls.length && plan.local_resolution?.candidates?.length)
      this.store.patch(id, { apps: plan, status: '请选择应用' });
    else if (plan.calls.some((call) => call.requires_confirmation))
      this.store.patch(id, {
        pending: { planId: plan.plan_id, calls: plan.calls, approved: [], conversationId: id },
        status: '等待操作授权',
      });
    else {
      await this.work(plan.plan_id, [], request);
      return;
    }
    if (this.pendingRequests.size >= 64 && !this.pendingRequests.has(plan.plan_id))
      throw Error('待确认任务过多，请先停止旧任务');
    this.pendingRequests.set(plan.plan_id, request);
  }
  approve(planId: string, approved: string[], request: Submission) {
    const original = this.pendingRequests.get(planId);
    if (original && original.conversationId !== request.conversationId) return;
    request = original || request;
    const id = request.conversationId!;
    if (request.stillCurrent && !request.stillCurrent()) {
      this.error(id, Error('原语音会话已停止，请重新发起任务'));
      return;
    }
    this.store.patch(id, { pending: null });
    void this.store
      .enqueue(id, () => this.work(planId, approved, request))
      .catch((cause) => this.error(id, cause));
  }
  async select(planId: string, appId: string, request: Submission) {
    const original = this.pendingRequests.get(planId);
    if (original && original.conversationId !== request.conversationId) return;
    request = original || request;
    const id = request.conversationId!;
    if (request.stillCurrent && !request.stillCurrent())
      throw Error('原语音会话已停止，请重新发起任务');
    this.store.patch(id, { apps: null });
    const plan = await this.sdk.selectApplication(planId, appId);
    this.pendingRequests.delete(planId);
    void this.store
      .enqueue(id, () => this.applyPlan(plan, request))
      .catch((cause) => this.error(id, cause));
  }
  private async work(planId: string, approved: string[], request: Submission) {
    this.pendingRequests.delete(planId);
    if (request.stillCurrent && !request.stillCurrent()) return;
    const ticket = crypto.randomUUID(),
      id = request.conversationId!;
    if (this.hooks.visible(id)) {
      this.silence();
      this.audible = ticket;
    }
    await this.consume(
      request,
      ticket,
      '',
      planId,
      (signal) =>
        this.sdk.streamAgent(
          planId,
          approved,
          request.voice,
          request.expression,
          request.speed,
          signal,
          request.voiceContext,
        ),
      true,
    );
  }
  /** 文字、语音共用有序事件流；文本完成与音频消费结束是不同生命周期。 */
  private async consume(
    request: Submission,
    ticket: string,
    userDraft: string,
    assistantDraft: string,
    eventSource: (signal: AbortSignal) => AsyncGenerator<api.ChatStreamEvent>,
    work: boolean,
  ) {
    const id = request.conversationId!,
      abort = new AbortController();
    this.requests.set(ticket, {
      conversationId: id,
      abort,
      workId: this.store.get(id).work_session_id || undefined,
    });
    let replyId = assistantDraft,
      completed = false;
    const voice = new VoicePresentationService(
      this.playback,
      request.volume,
      (detail) => {
        this.store.patch(id, { voice_state: 'failed' });
        this.error(id, Error(detail));
      },
      () => request.voice && this.hooks.visible(id) && this.audible === ticket,
    );
    const gate = new FirstSpeechGate(replyId, request.voice, (delta) =>
      this.rows(id, (rows) =>
        rows.map((row) => (row.id === replyId ? { ...row, content: row.content + delta } : row)),
      ),
    );
    this.gates.set(ticket, { id: replyId, gate });
    this.voices.add(voice);
    this.store.patch(id, { voice_state: request.voice ? 'generating' : 'idle' });
    // 文字保存即可释放本会话排队；长语音继续消费/缓存，不能在这里截断流。
    let resolveText!: () => void, rejectText!: (cause: unknown) => void;
    const textDone = new Promise<void>((resolve, reject) => {
      resolveText = resolve;
      rejectText = reject;
    });
    const drain = async () => {
      try {
        for await (const item of eventSource(abort.signal)) {
          if (abort.signal.aborted) throw Error('本次回复已停止');
          // 先核对会话，再交给语音消费者核对回复、声音会话及片段序号。
          if (item.data.conversation_id && item.data.conversation_id !== id) continue;
          if (await voice.accept(item)) continue;
          if (item.event === 'user_saved' && userDraft && item.data.message_id)
            this.rows(id, (rows) =>
              rows.map((row) =>
                row.id === userDraft ? { ...row, id: String(item.data.message_id) } : row,
              ),
            );
          else if (item.event === 'assistant_started') {
            replyId = String(item.data.message_id);
            request.onVoiceReply?.(replyId);
            gate.setMessageId(replyId);
            this.gates.set(ticket, { id: replyId, gate });
            this.rows(id, (rows) =>
              work
                ? [
                    ...rows,
                    {
                      id: replyId,
                      character_id: request.character.id,
                      role: 'assistant',
                      content: '',
                      created_at: new Date().toISOString(),
                    },
                  ]
                : rows.map((row) => (row.id === assistantDraft ? { ...row, id: replyId } : row)),
            );
          } else if (item.event === 'voice_state' && request.voiceContext) {
            const identity = item.data as unknown as import('./realtimeVoice/types').VoiceIdentity;
            if (identity.generation_id === request.voiceContext.generation_id) {
              request.onVoiceState?.(identity);
              this.store.patch(id, {
                voice_state: identity.turn_state === 'FAILED' ? 'failed' : 'idle',
              });
              this.rows(id, (rows) =>
                rows.map((row) =>
                  row.id === replyId
                    ? { ...row, voice_status: identity.turn_state === 'FAILED' ? 'failed' : 'none' }
                    : row,
                ),
              );
            }
          } else if (item.event === 'text_delta') gate.push(String(item.data.text));
          else if (item.event === 'completed') {
            const previous = replyId;
            replyId = String(item.data.message_id);
            gate.setMessageId(replyId);
            await gate.finish();
            this.rows(id, (rows) =>
              rows.map((row) =>
                row.id === previous
                  ? {
                      ...row,
                      id: replyId,
                      content: String(item.data.content || row.content),
                      voice_status: request.voice ? 'generating' : 'none',
                    }
                  : row,
              ),
            );
            completed = true;
            resolveText();
            if (work && item.data.work_session_id)
              this.hooks.work(
                id,
                await this.sdk.loadWorkSession(String(item.data.work_session_id)),
              );
            this.hooks.changed();
          } else if (item.event === 'voice_saved') {
            this.store.patch(id, { voice_state: 'ready' });
            this.rows(id, (rows) =>
              rows.map((row) => (row.id === replyId ? { ...row, voice_status: 'ready' } : row)),
            );
          } else if (item.event === 'error') throw Error(String(item.data.detail || '请求失败'));
        }
        if (!completed) throw Error('没有收到完整回复');
      } catch (cause) {
        this.error(id, cause);
        if (!completed) rejectText(cause);
      } finally {
        gate.stop();
        this.gates.delete(ticket);
        this.voices.delete(voice);
        this.requests.delete(ticket);
        this.hooks.changed();
      }
    };
    void drain();
    await textDone;
  }
  /** 只取消目标会话，不因一个页面的停止操作中断其他后台会话。 */
  async stop(id: string) {
    for (const [planId, request] of this.pendingRequests)
      if (request.conversationId === id) this.pendingRequests.delete(planId);
    for (const context of this.requests.values())
      if (context.conversationId === id) {
        context.abort.abort();
        if (context.workId) await this.sdk.stopWorkSession(context.workId);
      }
    const workId = this.store.get(id).work_session_id;
    if (workId) await this.sdk.stopWorkSession(workId);
    if (this.hooks.visible(id)) this.silence();
  }
}
