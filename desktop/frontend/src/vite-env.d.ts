/// <reference types="vite/client" />

interface Window {
  desktopAPI?: {
    readKnowledge: <K extends keyof import('./knowledge/types').Reads>(
      operation: K,
      options?: Record<string, unknown>,
    ) => Promise<import('./knowledge/types').Reads[K]>;
    controlKnowledge: (
      operation: import('./knowledge/types').Operation,
      options?: Record<string, unknown>,
    ) => Promise<unknown>;
    readWorkflows: <K extends keyof import('./workflows/types').WorkflowReads>(
      operation: K,
      options?: import('./workflows/types').WorkflowQuery,
    ) => Promise<import('./workflows/types').WorkflowReads[K]>;
    controlWorkflows: (
      operation: import('./workflows/types').WorkflowControl,
      options?: Record<string, unknown>,
    ) => Promise<unknown>;
    watchWorkflow: (
      options: { run_id: string; conversation_id: string; after?: number } | null,
    ) => Promise<boolean>;
    onWorkflowProgress: (
      callback: (event: import('./workflows/types').WorkflowProgress) => void,
    ) => () => void;
    readExtensions: <K extends keyof import('./extensions/types').ExtensionReads>(
      operation: K,
      options?: { server_id?: string; skill_id?: string; file?: string },
    ) => Promise<import('./extensions/types').ExtensionReads[K]>;
    controlExtensions: (
      operation: import('./extensions/types').ExtensionControl,
      options?: Record<string, unknown>,
    ) => Promise<unknown>;
    readAgentRecovery: <K extends keyof import('./recovery/types').RecoveryReads>(
      operation: K,
      options: import('./recovery/types').RecoveryScope,
    ) => Promise<import('./recovery/types').RecoveryReads[K]>;
    controlAgentRecovery: (
      taskId: string,
      action: import('./recovery/types').RecoveryAction,
      options: import('./recovery/types').RecoveryControl,
    ) => Promise<Record<string, unknown>>;
    readEvaluation: <K extends keyof import('./evaluation/types').EvaluationReads>(
      operation: K,
      options?: import('./evaluation/types').EvaluationQuery,
    ) => Promise<import('./evaluation/types').EvaluationReads[K]>;
    startEvaluation: (
      options: import('./evaluation/types').EvaluationOptions,
    ) => Promise<import('./evaluation/types').EvaluationRun>;
    controlEvaluation: (
      runId: string,
      action: 'pause' | 'resume' | 'cancel',
    ) => Promise<import('./evaluation/types').EvaluationRun>;
    deleteEvaluation: (runId: string) => Promise<{ deleted: boolean }>;
    exportEvaluation: (
      runId: string,
      format: 'json' | 'csv' | 'markdown',
    ) => Promise<{ filename: string; partial: boolean }>;
    watchEvaluation: (runId: string | null) => Promise<boolean>;
    onEvaluationProgress: (
      callback: (event: import('./evaluation/types').EvaluationProgress) => void,
    ) => () => void;
    readAgentTraces: {
      (
        operation: 'list',
        options: import('./trace/types').TraceQuery,
      ): Promise<import('./trace/types').TracePage<import('./trace/types').AgentTrace>>;
      (
        operation: 'detail',
        options: import('./trace/types').TraceQuery,
      ): Promise<import('./trace/types').AgentTrace>;
      (
        operation: 'events',
        options: import('./trace/types').TraceQuery,
      ): Promise<import('./trace/types').TracePage<import('./trace/types').TraceEvent>>;
      (
        operation: 'summary',
        options: import('./trace/types').TraceQuery,
      ): Promise<import('./trace/types').TraceSummary>;
    };
    deleteAgentTrace: (
      options: import('./trace/types').TraceQuery,
    ) => Promise<{ deleted: boolean }>;
    exportAgentTrace: (
      options: import('./trace/types').TraceQuery,
    ) => Promise<{ filename: string; event_count: number; partial: boolean }>;
    appInfo: () => Promise<{
      version: string;
      build_label: string;
      build_id: string;
      built_at: string | null;
      channel: string;
      app_id: string;
      source_fingerprint: string | null;
      packaged: boolean;
      product_name?: string;
      clean_distribution?: boolean;
      default_character?: string;
    }>;
    characterLibrary: () => Promise<import('./api').CharacterLibrary>;
    characterDetail: (id: string) => Promise<import('./api').CharacterCard>;
    createCustomCharacter: (
      draft: import('./api').CharacterDraft,
    ) => Promise<import('./api').CharacterCard>;
    updateCharacter: (
      id: string,
      draft: import('./api').CharacterDraft,
    ) => Promise<import('./api').CharacterCard>;
    activateCharacter: (id: string) => Promise<import('./api').CharacterCard>;
    importCharacterCard: (file: {
      filename: string;
      data_base64: string;
    }) => Promise<import('./api').CharacterCard>;
    importCharacter: () => Promise<{ id: string; name: string } | null>;
    rendererContext: (
      id?: string | null,
    ) => Promise<{ renderer_id: string; window_id: string; active_conversation_id: string | null }>;
    clipboardWriteText: (text: string) => Promise<boolean>;
    nativePcmCapability: () => Promise<{ supported: boolean }>;
    startNativePcm: (body: {
      message_id: string;
      sample_rate: number;
      channels: number;
      volume: number;
      session_id?: string;
      initial_buffer_ms?: number;
    }) => Promise<{ status: string }>;
    feedNativePcm: (body: {
      pcm_base64: string;
      segment_id: string;
      end_segment?: boolean;
      session_id?: string;
      sequence?: number;
    }) => Promise<{ status: string }>;
    finishNativePcm: () => Promise<unknown>;
    cancelVoiceStream: (session_id: string) => Promise<unknown>;
    // 读取可能返回旧版布局；由 migrateLayout 从 unknown 兼容恢复。
    workbenchLayout: (
      key: string,
      value?: import('./WorkbenchLayout').WorkbenchLayout,
    ) => Promise<unknown>;
    getVirtualDocument: (
      sessionId: string,
      id: string,
    ) => Promise<import('./CodeModule').VirtualCodeDocument>;
    editVirtualDocument: (
      sessionId: string,
      id: string,
      content: string,
    ) => Promise<Record<string, unknown>>;
    saveVirtualDocument: (
      sessionId: string,
      id: string,
      mode: 'save' | 'saveAs' | 'apply',
    ) => Promise<{ cancelled?: boolean; path?: string; dirty?: boolean }>;
    discardVirtualDocument: (sessionId: string, id: string) => Promise<{ status: string }>;
    clearWorkFolder: (conversationId: string) => Promise<import('./api').WorkSession>;
    windowState: () => Promise<{ maximized: boolean; minimized: boolean }>;
    windowControl: (action: 'minimize' | 'toggleMaximize' | 'close') => Promise<boolean>;
    onWindowState: (
      callback: (state: { maximized: boolean; minimized: boolean }) => void,
    ) => () => void;
    themeSettings: (
      config?: import('./AppThemeProvider').ThemeConfig,
    ) => Promise<import('./AppThemeProvider').ThemeConfig>;
    scanApplications: () => Promise<ApplicationDiscovery>;
    applicationDiscovery: () => Promise<ApplicationDiscovery>;
    diagnoseApplication: (target: string) => Promise<Record<string, unknown>>;
    diagnoseBrowser: (args: {
      site: string;
      query: string;
      url?: string;
    }) => Promise<Record<string, unknown>>;
    mediaSnapshot: (selectedId?: string) => Promise<MediaSnapshot>;
    mediaControl: (
      action: 'play' | 'pause' | 'next' | 'previous' | 'seek' | 'volume',
      sessionId: string,
      value?: number,
    ) => Promise<MediaSnapshot>;
    diagnoseMusic: (options: {
      query: string;
      launch?: boolean;
      search?: boolean;
    }) => Promise<Record<string, unknown>>;
    workFiles: (
      sessionId: string,
      options?: { path?: string; query?: string; read?: boolean; show_ignored?: boolean },
    ) => Promise<WorkspaceFiles>;
    openWorkFolder: (sessionId: string) => Promise<boolean>;
    watchWorkFiles: (sessionId: string | null) => Promise<boolean>;
    onWorkFilesChanged: (callback: (state: { sessionId: string }) => void) => () => void;
    getBackendUrl: () => Promise<string>;
    listApiPresets: () => Promise<{ active_id: string; presets: ApiPreset[] }>;
    setModelRoles: (
      roles: Record<string, string>,
    ) => Promise<{ roles: Record<string, string>; role_status: ModelRoleStatus }>;
    modelRoleStatus: () => Promise<ModelRoleStatus>;
    testApiConnection: (
      draft: ApiDraft,
    ) => Promise<{ ok: boolean; model: string; latency_ms: number }>;
    applyApiPreset: (
      draft: ApiDraft,
      force?: boolean,
    ) => Promise<{ active_id: string; presets: ApiPreset[] }>;
    activateApiPreset: (id: string) => Promise<{ active_id: string; presets: ApiPreset[] }>;
    deleteApiPreset: (id: string) => Promise<{ active_id: string; presets: ApiPreset[] }>;
    fetchApiModels: (draft: ApiDraft) => Promise<{ models: string[] }>;
    getFishSettings: () => Promise<FishSettings>;
    applyFishSettings: (
      draft: FishSettings & { api_key?: string; clear_key?: boolean },
    ) => Promise<FishSettings>;
    testFishVoice: () => Promise<{
      ok: boolean;
      http_status: number;
      model: string;
      bytes: number;
      ttfa_ms: number;
      generation_ms: number;
      audio_url: string;
    }>;
    testFishStream: () => Promise<{
      ok: boolean;
      model: string;
      transport: 'stream' | 'standard';
      generation_ms: number;
      audio_url: string;
    }>;
    testFishCharacter: () => Promise<{
      samples: Array<{
        index: number;
        text: string;
        provider: string;
        model: string;
        bytes: number;
        ttfa_ms: number;
        generation_ms: number;
        audio_url: string;
        cached?: boolean;
      }>;
      complete: boolean;
      failed_index?: number;
      error?: string;
    }>;
    compareFishVoice: () => Promise<{
      samples: Array<{
        name: string;
        temperature: number;
        latency: string;
        text: string;
        ttfa_ms: number;
        generation_ms: number;
        audio_url: string;
        cached: boolean;
      }>;
      complete: boolean;
      error?: string;
    }>;
    testFishScenario: (index: number) => Promise<{
      ok: boolean;
      index: number;
      name: string;
      cached: boolean;
      intents: string[];
      bytes: number;
      generation_ms: number;
      audio_url: string;
    }>;
    selectMusicFolder: () => Promise<{ folder: string; tracks: MusicTrack[] } | null>;
    selectWorkFolder: (conversationId: string) => Promise<string | null>;
    workPreferences: (patch?: Partial<WorkPreferences>) => Promise<WorkPreferences>;
    selectAppearance: (kind: 'background' | 'work_background' | 'avatar') => Promise<string | null>;
    openOfficialMusic: (provider: 'netease' | 'qqmusic', query: string) => Promise<string>;
    listKnownApps: () => Promise<KnownApp[]>;
    registerKnownApp: () => Promise<KnownApp | null>;
    launchKnownApp: (id: string, taskId?: string) => Promise<KnownApp>;
    listTrustedActions: () => Promise<Array<{ action: string; scope: string; risk: string }>>;
    setTrustedAction: (
      action: string,
      scope: 'always' | 'task' | 'deny' | 'revoke',
      taskId?: string,
    ) => Promise<{ ok: boolean }>;
    showContextMenu: (type: string, state: Record<string, unknown>) => Promise<string | null>;
    logDiagnostic: (file: string, detail: Record<string, unknown>) => Promise<boolean>;
    knownAppStatus: (id: string) => Promise<KnownApp>;
    focusKnownApp: (id: string) => Promise<KnownApp & { focused: boolean }>;
    closeKnownApp: (id: string) => Promise<KnownApp & { closed: boolean }>;
    workSystemStatus: () => Promise<{
      cpu: string;
      cores: number;
      memory_total: number;
      memory_free: number;
      disk: { total: number; free: number } | null;
      network: boolean;
      gpu: string;
    }>;
    openAgentUrl: (
      url: string,
      rect: { x: number; y: number; width: number; height: number },
    ) => Promise<{ url: string; title: string }>;
    openWorkspaceBrowser: (
      url: string,
      rect: { x: number; y: number; width: number; height: number },
      context?: { tabId?: string; taskId?: string; background?: boolean },
    ) => Promise<BrowserTabState>;
    resizeWorkspaceBrowser: (rect: {
      x: number;
      y: number;
      width: number;
      height: number;
    }) => Promise<boolean>;
    workspaceBrowserAction: (
      action: 'back' | 'forward' | 'reload' | 'external' | 'show' | 'hide' | 'close',
      id?: string,
    ) => Promise<BrowserTabState>;
    onWorkspaceBrowserState: (callback: (state: BrowserTabState) => void) => () => void;
    playNativeAudio: (url: string, volume: number) => Promise<{ status: string; duration: number }>;
    pauseNativeAudio: () => Promise<{ status: string }>;
    resumeNativeAudio: () => Promise<{ status: string }>;
    stopNativeAudio: () => Promise<{ status: string }>;
    nativeAudioStatus: () => Promise<{ status: string; position: number; duration: number }>;
  };
}

interface ApiDraft {
  id?: string;
  name: string;
  provider: string;
  base_url: string;
  api_key: string;
  clear_key?: boolean;
  model: string;
  temperature: number | null;
  max_tokens: number | null;
  timeout_seconds: number;
  capabilities?: {
    text: boolean;
    vision: boolean;
    tool_calling: boolean | null;
    web_search: boolean;
    structured_output: boolean | null;
    reasoning?: boolean | null;
    streaming?: boolean | null;
  };
}

type ModelRoleStatus = Record<
  string,
  { configured: boolean; capabilities: Record<string, { value: boolean | null; source: string }> }
>;

interface ApiPreset extends Omit<ApiDraft, 'api_key'> {
  has_key: boolean;
}
interface WorkPreferences {
  automation_enabled: boolean;
  auto_work_mode: boolean;
  music_player: 'netease' | 'qqmusic';
  coding_provider: 'internal' | 'codex';
  permission_mode: 'automatic' | 'smart' | 'ask';
  browser_media_enabled: boolean;
}
interface ApplicationDiscovery {
  applications: Array<{
    app_id: string;
    name: string;
    executable: string | null;
    app_user_model_id?: string;
    installed: boolean;
    sources: string[];
  }>;
  counts: Record<string, number>;
  scanned_at: string | null;
}
interface WorkspaceFiles {
  root?: string;
  path?: string;
  content?: string;
  items?: Array<{
    name: string;
    path: string;
    directory: boolean;
    ignored: boolean;
    size: number | null;
  }>;
  matches?: Array<{ path: string; line?: number; text?: string; match?: string }>;
  truncated?: boolean;
}
interface MediaSession {
  id: string;
  source_app: string;
  app_id: string;
  title: string;
  artist: string;
  thumbnail: string | null;
  status: 'playing' | 'paused' | 'stopped' | 'unknown';
  position_seconds: number | null;
  duration_seconds: number | null;
  source: string;
  controls: {
    play: boolean;
    pause: boolean;
    next: boolean;
    previous: boolean;
    seek: boolean;
    volume: boolean;
  };
  verified: boolean;
  observed_at: string;
}
interface MediaSnapshot {
  available: boolean;
  sessions: MediaSession[];
  active_id: string | null;
  selected_id: string | null;
  current: MediaSession | null;
  allow_browser_media: boolean;
  observed_at: string;
  diagnostics: unknown;
}

interface FishSettings {
  engine: 'fish' | 'index';
  reference_id: string;
  model: string;
  speed: number;
  fallback_enabled: boolean;
  transport_mode: 'auto' | 'stream' | 'standard';
  latency: 'balanced' | 'normal';
  has_key: boolean;
}

interface MusicTrack {
  id: string;
  title: string;
  name: string;
  format: string;
  audio_url: string;
}
interface KnownApp {
  id: string;
  name: string;
  installed: boolean;
  path: string | null;
  official: string;
  status: string;
  started_at?: string | null;
  pid?: number | null;
}
interface BrowserTabState {
  id?: string;
  url: string;
  title: string;
  error?: string;
  status?: string;
  visible?: boolean;
  code?: number;
  description?: string;
  isMainFrame?: boolean;
  popup_url?: string;
}
