const { contextBridge, ipcRenderer } = require('electron');

/**
 * 主窗口的固定能力入口：参数交给主进程验证，不暴露 Node、原始 IPC 或控制 Token。
 * 事件订阅返回取消函数，调用者在组件/会话清理时解除对应监听。
 * 增减方法时同步 main.cjs handler、前端 vite-env.d.ts 与相关回归。
 */
contextBridge.exposeInMainWorld('desktopAPI', {
  readKnowledge: (operation, options) => ipcRenderer.invoke('knowledge-read', operation, options),
  controlKnowledge: (operation, options) =>
    ipcRenderer.invoke('knowledge-control', operation, options),
  readWorkflows: (operation, options) => ipcRenderer.invoke('workflow-read', operation, options),
  controlWorkflows: (operation, options) =>
    ipcRenderer.invoke('workflow-control', operation, options),
  watchWorkflow: (options) => ipcRenderer.invoke('workflow-watch', options),
  onWorkflowProgress: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on('workflow-progress', listener);
    return () => ipcRenderer.removeListener('workflow-progress', listener);
  },
  readExtensions: (operation, options) => ipcRenderer.invoke('extension-read', operation, options),
  controlExtensions: (operation, options) =>
    ipcRenderer.invoke('extension-control', operation, options),
  readAgentRecovery: (operation, options) =>
    ipcRenderer.invoke('agent-recovery-read', operation, options),
  controlAgentRecovery: (taskId, action, options) =>
    ipcRenderer.invoke('agent-recovery-control', taskId, action, options),
  appInfo: () => ipcRenderer.invoke('app-info'),
  readEvaluation: (operation, options) => ipcRenderer.invoke('evaluation-read', operation, options),
  startEvaluation: (options) => ipcRenderer.invoke('evaluation-start', options),
  controlEvaluation: (runId, action) => ipcRenderer.invoke('evaluation-control', runId, action),
  deleteEvaluation: (runId) => ipcRenderer.invoke('evaluation-delete', runId),
  exportEvaluation: (runId, format) => ipcRenderer.invoke('evaluation-export', runId, format),
  watchEvaluation: (runId) => ipcRenderer.invoke('evaluation-watch', runId),
  onEvaluationProgress: (callback) => {
    const handler = (_event, data) => callback(data);
    ipcRenderer.on('evaluation-progress', handler);
    return () => ipcRenderer.removeListener('evaluation-progress', handler);
  },
  readAgentTraces: (operation, options) =>
    ipcRenderer.invoke('agent-traces-read', operation, options),
  deleteAgentTrace: (options) => ipcRenderer.invoke('agent-trace-delete', options),
  exportAgentTrace: (options) => ipcRenderer.invoke('agent-trace-export', options),
  importCharacter: () => ipcRenderer.invoke('character-import'),
  characterLibrary: () => ipcRenderer.invoke('character-library'),
  characterDetail: (id) => ipcRenderer.invoke('character-detail', id),
  createCustomCharacter: (body) => ipcRenderer.invoke('character-create-custom', body),
  updateCharacter: (id, body) => ipcRenderer.invoke('character-update', id, body),
  activateCharacter: (id) => ipcRenderer.invoke('character-activate', id),
  importCharacterCard: (body) => ipcRenderer.invoke('character-import-card', body),
  rendererContext: (id) => ipcRenderer.invoke('renderer-context', id),
  clipboardWriteText: (text) => ipcRenderer.invoke('clipboard-write-text', text),
  windowState: () => ipcRenderer.invoke('window-state'),
  windowControl: (action) => ipcRenderer.invoke('window-control', action),
  onWindowState: (callback) => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('window-state', listener);
    return () => ipcRenderer.removeListener('window-state', listener);
  },
  themeSettings: (value) => ipcRenderer.invoke('theme-settings', value),
  scanApplications: () => ipcRenderer.invoke('applications-scan'),
  applicationDiscovery: () => ipcRenderer.invoke('applications-discovery'),
  diagnoseApplication: (target) => ipcRenderer.invoke('applications-diagnose', target),
  diagnoseBrowser: (args) => ipcRenderer.invoke('browser-diagnose', args),
  mediaSnapshot: (selectedId) => ipcRenderer.invoke('media-snapshot', selectedId),
  mediaControl: (action, sessionId, value) =>
    ipcRenderer.invoke('media-control', action, sessionId, value),
  diagnoseMusic: (options) => ipcRenderer.invoke('music-diagnostics', options),
  workFiles: (sessionId, options) => ipcRenderer.invoke('work-files', sessionId, options),
  openWorkFolder: (sessionId) => ipcRenderer.invoke('work-open-folder', sessionId),
  watchWorkFiles: (sessionId) => ipcRenderer.invoke('work-watch-files', sessionId),
  onWorkFilesChanged: (callback) => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('work-files-changed', listener);
    return () => ipcRenderer.removeListener('work-files-changed', listener);
  },
  getBackendUrl: () => ipcRenderer.invoke('backend-url'),
  workbenchLayout: (key, value) => ipcRenderer.invoke('workbench-layout', key, value),
  getVirtualDocument: (session, id) => ipcRenderer.invoke('virtual-document-get', session, id),
  editVirtualDocument: (session, id, content) =>
    ipcRenderer.invoke('virtual-document-edit', session, id, content),
  saveVirtualDocument: (session, id, mode) =>
    ipcRenderer.invoke('virtual-document-save', session, id, mode),
  discardVirtualDocument: (session, id) =>
    ipcRenderer.invoke('virtual-document-discard', session, id),
  clearWorkFolder: (conversationId) => ipcRenderer.invoke('work-clear-folder', conversationId),
  listApiPresets: () => ipcRenderer.invoke('api-presets'),
  setModelRoles: (roles) => ipcRenderer.invoke('api-model-roles', roles),
  modelRoleStatus: () => ipcRenderer.invoke('api-model-role-status'),
  testApiConnection: (draft) => ipcRenderer.invoke('api-test', draft),
  applyApiPreset: (draft, force = false) => ipcRenderer.invoke('api-apply', draft, force),
  activateApiPreset: (id) => ipcRenderer.invoke('api-activate', id),
  deleteApiPreset: (id) => ipcRenderer.invoke('api-delete', id),
  fetchApiModels: (draft) => ipcRenderer.invoke('api-models', draft),
  getFishSettings: () => ipcRenderer.invoke('fish-settings'),
  applyFishSettings: (draft) => ipcRenderer.invoke('fish-apply', draft),
  testFishVoice: () => ipcRenderer.invoke('fish-test'),
  testFishStream: () => ipcRenderer.invoke('fish-stream-test'),
  testFishCharacter: () => ipcRenderer.invoke('fish-character-test'),
  compareFishVoice: () => ipcRenderer.invoke('fish-compare'),
  testFishScenario: (index) => ipcRenderer.invoke('fish-v72-scenario', index),
  selectMusicFolder: () => ipcRenderer.invoke('music-select-folder'),
  selectWorkFolder: (conversationId) => ipcRenderer.invoke('work-select-folder', conversationId),
  workPreferences: (patch) => ipcRenderer.invoke('work-preferences', patch),
  selectAppearance: (kind) => ipcRenderer.invoke('appearance-select', kind),
  openOfficialMusic: (provider, query) =>
    ipcRenderer.invoke('music-open-official', provider, query),
  listKnownApps: () => ipcRenderer.invoke('known-apps'),
  registerKnownApp: () => ipcRenderer.invoke('known-app-register'),
  launchKnownApp: (id, taskId) => ipcRenderer.invoke('known-app-launch', id, taskId),
  listTrustedActions: () => ipcRenderer.invoke('permissions-list'),
  setTrustedAction: (action, scope, taskId) =>
    ipcRenderer.invoke('permissions-set', action, scope, taskId),
  showContextMenu: (type, state) => ipcRenderer.invoke('context-menu', type, state),
  logDiagnostic: (file, detail) => ipcRenderer.invoke('ui-diagnostic', file, detail),
  knownAppStatus: (id) => ipcRenderer.invoke('known-app-status', id),
  focusKnownApp: (id) => ipcRenderer.invoke('known-app-focus', id),
  closeKnownApp: (id) => ipcRenderer.invoke('known-app-close', id),
  workSystemStatus: () => ipcRenderer.invoke('work-system-status'),
  openAgentUrl: (url, rect) => ipcRenderer.invoke('agent-open-url', url, rect),
  openWorkspaceBrowser: (url, rect, context) =>
    ipcRenderer.invoke('workspace-browser-open', url, rect, context),
  resizeWorkspaceBrowser: (rect) => ipcRenderer.invoke('workspace-browser-bounds', rect),
  workspaceBrowserAction: (action, id) =>
    ipcRenderer.invoke('workspace-browser-action', action, id),
  onWorkspaceBrowserState: (callback) => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('workspace-browser-state', listener);
    return () => ipcRenderer.removeListener('workspace-browser-state', listener);
  },
  playNativeAudio: (url, volume) => ipcRenderer.invoke('native-audio-play', url, volume),
  pauseNativeAudio: () => ipcRenderer.invoke('native-audio-pause'),
  resumeNativeAudio: () => ipcRenderer.invoke('native-audio-resume'),
  stopNativeAudio: () => ipcRenderer.invoke('native-audio-stop'),
  nativeAudioStatus: () => ipcRenderer.invoke('native-audio-status'),
  nativePcmCapability: () => ipcRenderer.invoke('native-pcm-capability'),
  startNativePcm: (body) => ipcRenderer.invoke('native-pcm-start', body),
  feedNativePcm: (body) => ipcRenderer.invoke('native-pcm-feed', body),
  finishNativePcm: () => ipcRenderer.invoke('native-pcm-finish'),
  cancelVoiceStream: (session_id) => ipcRenderer.invoke('voice-stream-cancel', { session_id }),
});
