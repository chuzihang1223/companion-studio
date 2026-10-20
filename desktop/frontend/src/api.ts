export type ChatMessage = {
  id: string;
  character_id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  conversation_id?: string | null;
  reply_to_message_id?: string | null;
  variant_of_message_id?: string | null;
  voice_status?: string;
  voice_engine?: string | null;
  attachments?: AttachmentMeta[];
};
export type AttachmentMeta = {
  id: string;
  name: string;
  kind: 'image' | 'file';
  media_type: string;
  size: number;
  extension: string;
};
export type AttachmentInput = {
  name: string;
  data_base64: string;
  preview?: string;
  kind?: 'image' | 'file';
  size?: number;
};

export type Conversation = {
  id: string;
  character_id: string;
  title: string;
  preferred_mode: 'chat' | 'work';
  conversation_type: 'chat' | 'work' | 'project';
  project_id?: string | null;
  work_session_id?: string | null;
  parent_conversation_id: string | null;
  fork_message_id: string | null;
  created_at: string;
  updated_at: string;
};

type ChatReply = {
  message_id: string;
  character_id: string;
  content: string;
  created_at: string;
};

export type CharacterInfo = {
  identity?: string;
  id: string;
  name: string;
  english_name: string;
  tagline: string;
  welcome_message: string;
  avatar_url?: string | null;
};

export type CharacterDraft = {
  name: string;
  identity: string;
  personality: string[];
  speech_style: string[];
  behavior_rules: string[];
  relationship: Record<string, string>;
  system_prompt: string;
  welcome_message: string;
  tagline: string;
};
export type CharacterCard = CharacterInfo & {
  profile: {
    identity: string;
    personality: string[];
    speech_style: string[];
    behavior_rules: string[];
    relationship: Record<string, string>;
  };
  system_prompt: string;
  source?: string;
  card_format?: string;
  import_warnings?: string[];
};
export type CharacterLibrary = { active_id: string; characters: CharacterCard[] };

export async function characterLibrary(): Promise<CharacterLibrary> {
  if (window.desktopAPI) return window.desktopAPI.characterLibrary();
  return readResponse(await fetch(`${await backendUrl()}/api/characters`));
}
export async function characterDetail(id: string): Promise<CharacterCard> {
  if (window.desktopAPI) return window.desktopAPI.characterDetail(id);
  return readResponse(
    await fetch(`${await backendUrl()}/api/characters/${encodeURIComponent(id)}`),
  );
}
export async function createCustomCharacter(draft: CharacterDraft): Promise<CharacterCard> {
  if (!window.desktopAPI) throw new Error('请在桌面应用中保存角色。');
  return window.desktopAPI.createCustomCharacter(draft);
}
export async function updateCharacter(id: string, draft: CharacterDraft): Promise<CharacterCard> {
  if (!window.desktopAPI) throw new Error('请在桌面应用中保存角色。');
  return window.desktopAPI.updateCharacter(id, draft);
}
export async function activateCharacter(id: string): Promise<CharacterCard> {
  if (!window.desktopAPI) throw new Error('请在桌面应用中切换角色。');
  return window.desktopAPI.activateCharacter(id);
}
export async function importCharacterCard(file: File): Promise<CharacterCard> {
  const isJson = /\.json$/i.test(file.name);
  if (!isJson && !/\.png$/i.test(file.name)) throw new Error('请选择 JSON 或 PNG 角色卡。');
  if (file.size > (isJson ? 256 * 1024 : 6 * 1024 * 1024))
    throw new Error(isJson ? 'JSON 角色卡不能超过 256 KB。' : 'PNG 角色卡不能超过 6 MB。');
  if (!window.desktopAPI) throw new Error('请在桌面应用中导入角色卡。');
  const data_base64 = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
    reader.onerror = () => reject(new Error('无法读取角色卡文件。'));
    reader.readAsDataURL(file);
  });
  return window.desktopAPI.importCharacterCard({ filename: file.name, data_base64 });
}

export type PersonalizationSettings = {
  personality: string;
  relationship: string;
  appellation: string;
  user_name: string;
  user_nickname: string;
  user_status: string;
  user_interests: string;
  user_games: string;
  user_foods: string;
  user_dislikes: string;
  user_notes: string;
  custom_rules: string;
  reply_style: 'short' | 'natural' | 'detailed';
  intimacy: 'natural' | 'close' | 'very_close';
};
export type ManualMemory = { id: string; content: string; enabled: boolean; source: 'manual' };
export type PersonalizationData = {
  settings: PersonalizationSettings;
  manual_memories: ManualMemory[];
  conversation_memories: string[];
};

export async function loadPersonalization(id: string): Promise<PersonalizationData> {
  return readResponse(
    await fetch(`${await backendUrl()}/api/personalization/${encodeURIComponent(id)}`),
  );
}
export async function savePersonalization(
  id: string,
  settings: PersonalizationSettings,
): Promise<void> {
  await readResponse(
    await fetch(`${await backendUrl()}/api/personalization/${encodeURIComponent(id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }),
  );
}
export async function createManualMemory(id: string, content: string): Promise<ManualMemory> {
  return readResponse(
    await fetch(`${await backendUrl()}/api/personalization/${encodeURIComponent(id)}/memories`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, enabled: true }),
    }),
  );
}
export async function changeManualMemory(
  id: string,
  memoryId: string,
  patch: { content?: string; enabled?: boolean },
): Promise<ManualMemory> {
  return readResponse(
    await fetch(
      `${await backendUrl()}/api/personalization/${encodeURIComponent(id)}/memories/${encodeURIComponent(memoryId)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      },
    ),
  );
}
export async function removeManualMemory(id: string, memoryId: string): Promise<void> {
  await fetch(
    `${await backendUrl()}/api/personalization/${encodeURIComponent(id)}/memories/${encodeURIComponent(memoryId)}`,
    { method: 'DELETE' },
  ).then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
  });
}
export async function resetPersonalization(id: string): Promise<void> {
  await fetch(`${await backendUrl()}/api/personalization/${encodeURIComponent(id)}/reset`, {
    method: 'DELETE',
  }).then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
  });
}

const fallbackBackend = 'http://127.0.0.1:8000';

async function backendUrl(): Promise<string> {
  return window.desktopAPI ? window.desktopAPI.getBackendUrl() : fallbackBackend;
}

async function readResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export type AgentCall = {
  server_id?: string;
  original_tool_name?: string;
  schema_fingerprint?: string;
  side_effect_class?: string;
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  risk_level: string;
  requires_confirmation: boolean;
  action_id?: string;
};
export type ApplicationChoice = { app_id: string; name: string; score: number };
export type AgentPlan = {
  plan_id: string;
  calls: AgentCall[];
  workspace: string;
  conversation_id: string;
  work_session_id?: string;
  local_resolution?: { error?: string; candidates?: ApplicationChoice[] } | null;
};
export type AgentResult = {
  user_message_id: string;
  message_id: string;
  conversation_id: string;
  content: string;
  managed_by_runtime?: boolean;
  goal_verified?: boolean;
  created_at: string;
  tools: Array<{ name: string; status: string; result: Record<string, unknown> }>;
  voice_job?: VoiceJob;
  voice_error?: string;
  work_session_id?: string;
};

export type WorkSession = {
  trace_revision?: number;
  recovery_revision?: number;
  id: string;
  conversation_id: string | null;
  goal: string;
  status: string;
  workspace_root: string | null;
  pending_plan?: {
    plan_id: string;
    calls: AgentCall[];
    local_resolution?: AgentPlan['local_resolution'];
  } | null;
  tool_results?: AgentResult['tools'];
  steps: Array<{ id: string; title: string; status: string }>;
  events: Array<{ id: number; type: string; detail: Record<string, unknown>; created_at: string }>;
  tabs: Array<{ id: string; kind: string; title: string; data: Record<string, unknown> }>;
  permissions: Array<{ call_id: string; tool_name: string; decision: string }>;
};

export async function loadWorkSession(id: string): Promise<WorkSession> {
  return readResponse(await fetch(`${await backendUrl()}/api/work/${encodeURIComponent(id)}`));
}
export async function loadWorkForConversation(id: string): Promise<WorkSession | null> {
  const response = await fetch(
    `${await backendUrl()}/api/work/by-conversation/${encodeURIComponent(id)}`,
  );
  if (response.status === 404) return null;
  return readResponse(response);
}
export async function workEventsUrl(id: string): Promise<string> {
  return `${await backendUrl()}/api/work/${encodeURIComponent(id)}/events`;
}
export async function stopWorkSession(id: string): Promise<void> {
  await readResponse(
    await fetch(`${await backendUrl()}/api/work/${encodeURIComponent(id)}/stop`, {
      method: 'POST',
    }),
  );
}
export async function controlWorkSession(
  id: string,
  action: 'pause' | 'resume' | 'takeover' | 'cancel',
): Promise<void> {
  await readResponse(
    await fetch(`${await backendUrl()}/api/work/${encodeURIComponent(id)}/control`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    }),
  );
}
export async function addWorkTab(
  id: string,
  kind: string,
  title: string,
  data: Record<string, unknown>,
): Promise<string> {
  const result = await readResponse<{ id: string }>(
    await fetch(`${await backendUrl()}/api/work/${encodeURIComponent(id)}/tabs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, title, data }),
    }),
  );
  return result.id;
}

export async function routeAgent(
  message: string,
  mode: 'auto' | 'chat' | 'agent',
): Promise<'chat' | 'agent'> {
  const result = await readResponse<{ mode: 'chat' | 'agent' }>(
    await fetch(`${await backendUrl()}/api/agent/route`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, mode }),
    }),
  );
  return result.mode;
}
export async function planAgent(
  characterId: string,
  conversationId: string | null,
  message: string,
  attachments: AttachmentInput[] = [],
  workspaceFolder: string | null = null,
): Promise<AgentPlan> {
  const renderer_id = await (await import('./RendererIdentity')).rendererIdentity();
  return readResponse(
    await fetch(`${await backendUrl()}/api/agent/plan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        character_id: characterId,
        conversation_id: conversationId,
        message,
        attachments: attachments.map(({ name, data_base64 }) => ({ name, data_base64 })),
        workspace_folder: workspaceFolder,
        renderer_id,
      }),
    }),
  );
}
export async function executeAgent(
  planId: string,
  approvedIds: string[],
  voiceEnabled: boolean,
  expression: ExpressionLevel,
  speedFactor: number,
): Promise<AgentResult> {
  if (window.desktopAPI?.controlExtensions)
    await window.desktopAPI.controlExtensions('approve-plan', {
      plan_id: planId,
      approved_ids: approvedIds,
    });
  return readResponse(
    await fetch(`${await backendUrl()}/api/agent/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        plan_id: planId,
        approved_ids: approvedIds,
        voice_enabled: voiceEnabled,
        expression,
        speed_factor: speedFactor,
      }),
    }),
  );
}
export async function selectApplication(planId: string, appId: string): Promise<AgentPlan> {
  return readResponse(
    await fetch((await backendUrl()) + '/api/agent/select-application', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan_id: planId, app_id: appId }),
    }),
  );
}
export async function* streamAgent(
  planId: string,
  approvedIds: string[],
  voiceEnabled: boolean,
  expression: ExpressionLevel,
  speedFactor: number,
  signal?: AbortSignal,
  voiceContext?: import('./realtimeVoice/types').VoiceBinding,
): AsyncGenerator<ChatStreamEvent> {
  if (window.desktopAPI?.controlExtensions)
    await window.desktopAPI.controlExtensions('approve-plan', {
      plan_id: planId,
      approved_ids: approvedIds,
    });
  yield* readEventStream(
    await fetch(`${await backendUrl()}/api/agent/execute/stream`, {
      signal,
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        plan_id: planId,
        approved_ids: approvedIds,
        voice_enabled: voiceEnabled,
        expression,
        speed_factor: speedFactor,
        voice_context: voiceContext,
      }),
    }),
  );
}
export async function loadMusicLibrary(): Promise<{ folder: string | null; tracks: MusicTrack[] }> {
  return readResponse(await fetch(`${await backendUrl()}/api/music/library`));
}
export async function loadAppearance(): Promise<{
  background: string | null;
  avatar: string | null;
}> {
  return readResponse(await fetch(`${await backendUrl()}/api/appearance`));
}

export async function loadCharacter(): Promise<CharacterInfo> {
  const response = await fetch(`${await backendUrl()}/api/character`);
  return readResponse<CharacterInfo>(response);
}

export async function loadHistory(characterId: string): Promise<ChatMessage[]> {
  const response = await fetch(
    `${await backendUrl()}/api/chat/history?character_id=${encodeURIComponent(characterId)}`,
  );
  return readResponse<ChatMessage[]>(response);
}

export async function listConversations(characterId: string, search = ''): Promise<Conversation[]> {
  const query = new URLSearchParams({ character_id: characterId, search });
  return readResponse(await fetch(`${await backendUrl()}/api/conversations?${query}`));
}

export async function createConversation(
  characterId: string,
  conversationType: Conversation['conversation_type'] = 'chat',
): Promise<Conversation> {
  return readResponse(
    await fetch(`${await backendUrl()}/api/conversations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ character_id: characterId, conversation_type: conversationType }),
    }),
  );
}

export async function getConversation(id: string): Promise<Conversation> {
  return readResponse(
    await fetch(`${await backendUrl()}/api/conversations/${encodeURIComponent(id)}`),
  );
}
export async function changeWorkTab(
  session: string,
  id: string,
  data?: Record<string, unknown>,
): Promise<void> {
  await readResponse(
    await fetch(`${await backendUrl()}/api/work/${session}/tabs/${id}`, {
      method: data ? 'PATCH' : 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: data ? JSON.stringify({ data }) : undefined,
    }),
  );
}

export async function loadConversation(id: string): Promise<ChatMessage[]> {
  return readResponse(
    await fetch(`${await backendUrl()}/api/conversations/${encodeURIComponent(id)}/messages`),
  );
}

export async function renameConversation(id: string, title: string): Promise<Conversation> {
  return readResponse(
    await fetch(`${await backendUrl()}/api/conversations/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    }),
  );
}

export async function setConversationMode(
  id: string,
  preferredMode: 'chat' | 'work',
): Promise<Conversation> {
  return readResponse(
    await fetch(`${await backendUrl()}/api/conversations/${encodeURIComponent(id)}/mode`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preferred_mode: preferredMode }),
    }),
  );
}

export async function deleteConversation(id: string): Promise<void> {
  await readResponse(
    await fetch(`${await backendUrl()}/api/conversations/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),
  );
}

export async function createBranch(
  id: string,
  forkMessageId: string | null,
): Promise<Conversation> {
  return readResponse(
    await fetch(`${await backendUrl()}/api/conversations/${encodeURIComponent(id)}/branches`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fork_message_id: forkMessageId }),
    }),
  );
}

export async function listBranches(id: string): Promise<Conversation[]> {
  return readResponse(
    await fetch(`${await backendUrl()}/api/conversations/${encodeURIComponent(id)}/siblings`),
  );
}

export async function retryMessage(id: string): Promise<ChatReply> {
  return readResponse(
    await fetch(`${await backendUrl()}/api/chat/${encodeURIComponent(id)}/retry`, {
      method: 'POST',
    }),
  );
}

export type VoiceJob = {
  job_id: string;
  message_id: string;
  status: string;
  engine: string | null;
  error_code: string | null;
  error_message: string | null;
  segments_done: number;
  segments_total: number;
  segments: Array<{ index: number; audio_url: string }>;
  audio_url: string | null;
  queue_length: number;
};

export async function voiceInfo(id: string): Promise<{
  status: string;
  audio_url: string | null;
  job_id?: string | null;
  error_message?: string | null;
}> {
  return readResponse(
    await fetch(`${await backendUrl()}/api/voice/messages/${encodeURIComponent(id)}`),
  );
}

export async function generateVoice(
  id: string,
  expression: ExpressionLevel,
  regenerate = false,
  speedFactor = 1,
): Promise<VoiceJob> {
  return readResponse(
    await fetch(`${await backendUrl()}/api/voice/messages/${encodeURIComponent(id)}/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expression, regenerate, speed_factor: speedFactor }),
    }),
  );
}

export async function voiceJobInfo(id: string): Promise<VoiceJob> {
  return readResponse(
    await fetch(`${await backendUrl()}/api/voice/jobs/${encodeURIComponent(id)}`),
  );
}

export async function voiceHealth(): Promise<{
  status: string;
  engine: string;
  primary: string;
  fish: { configured: boolean; authorized: boolean; reference_ready: boolean };
  indextts: { ready: boolean };
  model_loaded: boolean;
  speaker_loaded: boolean;
  device: string | null;
  warmup_ms: number | null;
  queue_length: number;
  last_error: string | null;
  cache_path: string;
}> {
  return readResponse(await fetch(`${await backendUrl()}/api/voice/health`));
}

export async function testVoice(): Promise<string> {
  const response = await fetch(`${await backendUrl()}/api/voice/test`, { method: 'POST' });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `HTTP ${response.status}`);
  }
  const data = (await response.json()) as { audio_url: string };
  return `${await backendUrl()}${data.audio_url}`;
}

export async function restartVoiceWorker(): Promise<void> {
  await readResponse(
    await fetch(`${await backendUrl()}/api/voice/worker/restart`, { method: 'POST' }),
  );
}

export async function voiceAudioUrl(id: string): Promise<string> {
  return `${await backendUrl()}/api/voice/audio/${encodeURIComponent(id)}`;
}

export async function voiceAudioDiagnostic(id: string): Promise<Record<string, unknown>> {
  return readResponse(
    await fetch(`${await backendUrl()}/api/voice/audio/${encodeURIComponent(id)}/diagnostic`),
  );
}

export async function voiceCacheInfo(): Promise<{ path: string; files: number; bytes: number }> {
  return readResponse(await fetch(`${await backendUrl()}/api/voice/cache`));
}

export async function clearVoiceCache(): Promise<{ removed: number }> {
  return readResponse(await fetch(`${await backendUrl()}/api/voice/cache`, { method: 'DELETE' }));
}

export async function loadVoiceStatus(): Promise<{
  status: 'unavailable' | 'preparing' | 'online' | 'error';
  engine: string;
}> {
  const response = await fetch(`${await backendUrl()}/api/voice/status`);
  return readResponse<{ status: 'unavailable' | 'preparing' | 'online' | 'error'; engine: string }>(
    response,
  );
}

export async function selectVoiceEngine(engine: 'auto' | 'onnx'): Promise<void> {
  await readResponse(
    await fetch(`${await backendUrl()}/api/voice/engine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ engine }),
    }),
  );
}

export async function sendMessage(characterId: string, message: string): Promise<ChatReply> {
  const response = await fetch(`${await backendUrl()}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ character_id: characterId, message }),
  });
  return readResponse<ChatReply>(response);
}

export type ExpressionLevel = 'restrained' | 'natural' | 'rich';

export type ChatStreamEvent = {
  event: string;
  data: Record<string, unknown>;
};

export async function* streamMessage(
  characterId: string,
  message: string,
  expression: ExpressionLevel,
  voiceEnabled: boolean,
  conversationId?: string,
  replyToMessageId?: string,
  speedFactor = 1,
  voicePrefetch = true,
  attachments: AttachmentInput[] = [],
  signal?: AbortSignal,
  voiceContext?: import('./realtimeVoice/types').VoiceBinding,
): AsyncGenerator<ChatStreamEvent> {
  const renderer_id = await (await import('./RendererIdentity')).rendererIdentity();
  const response = await fetch(`${await backendUrl()}/api/chat/stream`, {
    signal,
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      character_id: characterId,
      renderer_id,
      message,
      expression,
      voice_enabled: voiceEnabled,
      voice_prefetch: voicePrefetch,
      conversation_id: conversationId,
      reply_to_message_id: replyToMessageId,
      speed_factor: speedFactor,
      voice_context: voiceContext,
      attachments: attachments.map(({ name, data_base64 }) => ({ name, data_base64 })),
    }),
  });
  yield* readEventStream(response);
}

/** 分网络块保留未完成 SSE 事件；按到达顺序产出，并在结束/取消时释放 reader。 */
async function* readEventStream(response: Response): AsyncGenerator<ChatStreamEvent> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`);
  }
  if (!response.body) throw new Error('当前环境不支持流式回复。');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done }).replace(/\r\n/g, '\n');
      let boundary = buffer.indexOf('\n\n');
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const event = block
          .split('\n')
          .find((line) => line.startsWith('event: '))
          ?.slice(7);
        const data = block
          .split('\n')
          .find((line) => line.startsWith('data: '))
          ?.slice(6);
        if (event && data) yield { event, data: JSON.parse(data) as Record<string, unknown> };
        boundary = buffer.indexOf('\n\n');
      }
      if (done) break;
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
