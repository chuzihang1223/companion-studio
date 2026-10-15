const {
  app,
  BrowserWindow,
  WebContentsView,
  dialog,
  ipcMain,
  Menu,
  session,
  shell,
} = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const paths = require('./paths.cjs');
const backendManager = require('./backend.cjs');
const apiConfig = require('./api-config.cjs');
const fishConfig = require('./fish-config.cjs');
const knownApps = require('./known-apps.cjs');
const permissions = require('./permissions.cjs');
const appearanceConfig = require('./appearance-config.cjs');
const contextMenus = require('./context-menu.cjs');
const { WorkspaceBrowserManager } = require('./workspace-browser.cjs');
const { safeHttps } = require('./url-policy.cjs');
const { DesktopAppAgent, DesktopCommandPump } = require('./desktop-app-agent.cjs');
const crypto = require('node:crypto');
const os = require('node:os');
const brand = require('./brand.cjs');
const buildInfo = brand.buildInfo;
app.setName(brand.appName);
app.setAppUserModelId(brand.appId);

const isDev = process.argv.includes('--dev');
const compatibilityMode =
  process.argv.includes('--compat') || (app.isPackaged && process.platform === 'win32');
const backend = backendManager.address;
let mainWindow = null;
let workspaceBrowser = null;
let desktopPump = null;
let mediaService = null;
const browserWindows = new Set();
const guardedPartitions = new Set();
const rendererSessions = new (require('./renderer-session-registry.cjs').RendererSessionRegistry)();

function guardWebSession(partition) {
  const isolated = session.fromPartition(partition);
  if (!guardedPartitions.has(partition)) {
    isolated.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    isolated.on('will-download', (event) => event.preventDefault());
    guardedPartitions.add(partition);
  }
}

function browserBounds(rect) {
  const size = mainWindow.getContentBounds();
  const x = Math.max(0, Math.min(size.width - 100, Math.round(Number(rect?.x) || 0)));
  const y = Math.max(0, Math.min(size.height - 100, Math.round(Number(rect?.y) || 0)));
  return {
    x,
    y,
    width: Math.max(100, Math.min(size.width - x, Math.round(Number(rect?.width) || 400))),
    height: Math.max(100, Math.min(size.height - y, Math.round(Number(rect?.height) || 300))),
  };
}

async function openWorkspaceBrowser(url, rect, context) {
  if (!workspaceBrowser)
    workspaceBrowser = new WorkspaceBrowserManager(mainWindow, compatibilityMode, safeHttps, audit);
  if (
    !(await permissions.authorize(
      mainWindow,
      'browser.open:any',
      `打开网页 ${new URL(url).hostname}？`,
      context?.taskId,
    ))
  )
    return { url, title: '', status: 'denied', error: '已拒绝打开网页' };
  return workspaceBrowser.open(url, rect, context);
}

async function openSafePage(url) {
  const safe = safeHttps(url);
  const partition = 'persist:agent-browser';
  guardWebSession(partition);
  const browser = new BrowserWindow({
    title: brand.appName + ' · 网页',
    width: 980,
    height: 720,
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true, partition },
  });
  browserWindows.add(browser);
  browser.on('closed', () => browserWindows.delete(browser));
  browser.webContents.setWindowOpenHandler(({ url: destination }) => {
    try {
      void openSafePage(safeHttps(destination)).catch((error) =>
        log(`Browser popup failed: ${error.message}`),
      );
    } catch {
      /* ignore unsafe URL */
    }
    return { action: 'deny' };
  });
  browser.webContents.on('will-navigate', (event, destination) => {
    try {
      safeHttps(destination);
      return;
    } catch {
      /* deny */
    }
    event.preventDefault();
  });
  browser.webContents.on('will-redirect', (event, destination) => {
    try {
      safeHttps(destination);
      return;
    } catch {
      /* deny unsafe redirects */
    }
    event.preventDefault();
  });
  browser.webContents.on('did-fail-load', (_event, code, description, failedUrl, isMainFrame) => {
    if (isMainFrame) audit('browser.log', 'load_failed', `${code} ${new URL(safe).hostname}`);
  });
  await browser.loadURL(safe);
  audit('browser.log', 'external_loaded', new URL(safe).hostname);
  return true;
}

// 主进程拥有窗口、系统能力和凭据；渲染层只能调用下面注册的固定通道。
const logFile = path.join(paths.logs, 'electron.log');
function log(message) {
  fs.appendFileSync(logFile, `${new Date().toISOString()} ${message}\n`);
}
function audit(filename, event, detail = '') {
  const clean = String(detail)
    .replace(/[\r\n\x00-\x1f]/g, ' ')
    .slice(0, 2400);
  fs.appendFileSync(
    path.join(paths.logs, filename),
    `${new Date().toISOString()} ${event} ${clean}\n`,
  );
}
process.on('uncaughtException', (error) => {
  log(`Uncaught exception: ${error.stack || error}`);
  dialog.showErrorBox(brand.appName, String(error));
});
process.on('unhandledRejection', (error) => log(`Unhandled rejection: ${error}`));

if (compatibilityMode) {
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch('in-process-gpu');
}

const haveLock = app.requestSingleInstanceLock();
if (!haveLock) app.quit();
else
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });

/** 创建可信主窗口；网站内容使用独立视图，不能继承本窗口 preload。 */
function createWindow() {
  mainWindow = new BrowserWindow({
    title: brand.appName,
    width: 1040,
    height: 720,
    minWidth: 620,
    minHeight: 480,
    frame: false,
    resizable: true,
    maximizable: true,
    icon: path.join(__dirname, '..', 'assets', brand.icon),
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: !compatibilityMode,
      backgroundThrottling: false,
    },
  });
  const page = isDev ? 'http://127.0.0.1:5173' : `${backend.url}/app/`;
  rendererSessions.register(mainWindow);
  mainWindow.webContents.on('will-navigate', (event) => event.preventDefault());
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  mainWindow.webContents.once('did-finish-load', () => {
    mainWindow.show();
    audit('ui.log', 'renderer_loaded');
  });
  for (const event of ['maximize', 'unmaximize', 'minimize', 'restore'])
    mainWindow.on(event, () =>
      mainWindow?.webContents.send('window-state', {
        maximized: mainWindow?.isMaximized() || false,
        minimized: mainWindow?.isMinimized() || false,
      }),
    );
  mainWindow.webContents.on('render-process-gone', (_event, detail) =>
    audit('ui.log', 'renderer_gone', detail.reason),
  );
  mainWindow.on('closed', () => {
    workspaceBrowser?.dispose();
    workspaceBrowser = null;
    mainWindow = null;
    for (const browser of browserWindows) if (!browser.isDestroyed()) browser.close();
    app.quit();
  });
  mainWindow.loadURL(page);
}

ipcMain.handle('backend-url', () => backend.url);
ipcMain.handle('app-info', (event) => {
  requireMainWindow(event);
  return {
    version: app.getVersion(),
    build_label: buildInfo.build_label || 'Development',
    build_id: buildInfo.build_id || 'unbuilt',
    built_at: buildInfo.built_at || null,
    channel: buildInfo.channel || 'development',
    app_id: brand.appId,
    product_name: brand.appName,
    clean_distribution: brand.clean,
    default_character: 'assistant',
    source_fingerprint: buildInfo.source_fingerprint || null,
    packaged: app.isPackaged,
  };
});
ipcMain.handle('renderer-context', (event, id) => {
  requireMainWindow(event);
  return rendererSessions.context(event.sender, id);
});
ipcMain.handle('clipboard-write-text', async (event, text) => {
  requireMainWindow(event);
  try {
    const result = await require('./clipboard-service.cjs').writeText(text);
    audit('clipboard.log', 'write_success', 'chars=' + text.length);
    return result;
  } catch (error) {
    audit('clipboard.log', 'write_failed');
    throw error;
  }
});
ipcMain.handle('workbench-layout', (event, key, value) => {
  requireMainWindow(event);
  return require('./workbench-layout.cjs')(key, value);
});
const characterConfig = require('./character-config.cjs');
ipcMain.handle('character-library', (event) => {
  requireMainWindow(event);
  return characterConfig.library();
});
ipcMain.handle('character-detail', (event, id) => {
  requireMainWindow(event);
  return characterConfig.detail(id);
});
ipcMain.handle('character-create-custom', (event, body) => {
  requireMainWindow(event);
  return characterConfig.create(body);
});
ipcMain.handle('character-update', (event, id, body) => {
  requireMainWindow(event);
  return characterConfig.update(id, body);
});
ipcMain.handle('character-activate', (event, id) => {
  requireMainWindow(event);
  return characterConfig.activate(id);
});
ipcMain.handle('character-import-card', (event, body) => {
  requireMainWindow(event);
  return characterConfig.importCard(body);
});
ipcMain.handle('character-import', async (event) => {
  requireMainWindow(event);
  const selected = await dialog.showOpenDialog(mainWindow, {
    title: '导入角色卡',
    properties: ['openFile'],
    filters: [{ name: '角色卡 JSON / PNG', extensions: ['json', 'png'] }],
  });
  if (selected.canceled || !selected.filePaths[0]) return null;
  const file = selected.filePaths[0];
  if (fs.statSync(file).size > 6 * 1024 * 1024) throw Error('角色卡文件不能超过 6 MB');
  return characterConfig.importCard({
    filename: path.basename(file),
    data_base64: fs.readFileSync(file).toString('base64'),
  });
});
ipcMain.handle('work-clear-folder', (event, conversationId) => {
  requireMainWindow(event);
  return privateBackend('/api/work/workspace', { conversation_id: conversationId, root: null });
});
/**
 * IPC 边界检查：只接受已登记主窗口的 WebContents，不接受网页视图。
 * 后端还会对控制接口核验控制 Token，并对工作目录/权限单独检查。
 * @param {Electron.IpcMainInvokeEvent} event
 */
function requireMainWindow(event) {
  if (!mainWindow || event.sender !== mainWindow.webContents)
    throw new Error('Unauthorized window');
}
ipcMain.handle('window-state', (event) => {
  requireMainWindow(event);
  return { maximized: mainWindow.isMaximized(), minimized: mainWindow.isMinimized() };
});
ipcMain.handle('window-control', (event, action) => {
  requireMainWindow(event);
  if (action === 'minimize') mainWindow.minimize();
  else if (action === 'toggleMaximize') {
    mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize();
  } else if (action === 'close') mainWindow.close();
  else throw Error('无效窗口操作');
  return true;
});
ipcMain.handle('theme-settings', (event, value) => {
  requireMainWindow(event);
  return value ? appearanceConfig.apply(value) : appearanceConfig.current();
});
/** 代理本地控制接口；Token 留在主进程，不返回给渲染层。 */
async function privateBackend(route, body, method = 'POST') {
  const response = await fetch(backend.url + route, {
    method: body ? method : 'GET',
    headers: {
      'Content-Type': 'application/json',
      'X-Companion-Control-Token': backendManager.controlToken,
    },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(15000),
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.detail || '操作未完成');
  return result;
}
require('./virtual-documents.cjs')({
  ipcMain,
  requireMainWindow,
  request: privateBackend,
  getWindow: () => mainWindow,
});
require('./agent-traces.cjs')({ ipcMain, requireMainWindow, request: privateBackend });
// 目录切换先关闭旧观察器；revision 防止较慢的旧请求重新接管当前目录。
let projectWatcher = null,
  projectWatchTimer = null,
  projectWatchRevision = 0;
ipcMain.handle('work-files', async (event, sessionId, options) => {
  requireMainWindow(event);
  const query = new URLSearchParams();
  for (const key of ['path', 'query', 'read', 'show_ignored'])
    if (options?.[key] !== undefined) query.set(key, String(options[key]));
  return privateBackend(`/api/work/${encodeURIComponent(sessionId)}/files?${query}`);
});
ipcMain.handle('work-open-folder', async (event, sessionId) => {
  requireMainWindow(event);
  const row = await privateBackend(`/api/work/${encodeURIComponent(sessionId)}`);
  if (!row.workspace_root) throw Error('尚未选择工作文件夹');
  const error = await shell.openPath(row.workspace_root);
  if (error) throw Error(error);
  return true;
});
ipcMain.handle('work-watch-files', async (event, sessionId) => {
  requireMainWindow(event);
  const revision = ++projectWatchRevision;
  projectWatcher?.close();
  projectWatcher = null;
  clearTimeout(projectWatchTimer);
  if (!sessionId) return true;
  const row = await privateBackend(`/api/work/${encodeURIComponent(sessionId)}`);
  if (revision !== projectWatchRevision) return false;
  if (row.workspace_root && fs.existsSync(row.workspace_root)) {
    projectWatcher = fs.watch(row.workspace_root, { recursive: true }, (_type, filename) => {
      if (
        revision !== projectWatchRevision ||
        !filename ||
        String(filename)
          .split(/[\\/]/)
          .some((part) =>
            ['.git', 'node_modules', '.venv', 'venv', 'dist', 'build', 'cache'].includes(
              part.toLowerCase(),
            ),
          )
      )
        return;
      clearTimeout(projectWatchTimer);
      projectWatchTimer = setTimeout(
        () => mainWindow?.webContents.send('work-files-changed', { sessionId }),
        300,
      );
    });
    projectWatcher.on('error', () => audit('work.log', 'watch_failed', 'Project watcher stopped'));
  }
  return true;
});
ipcMain.handle('applications-scan', async (event) => {
  requireMainWindow(event);
  return knownApps.discovery.scan();
});
ipcMain.handle('applications-discovery', (event) => {
  requireMainWindow(event);
  return { ...knownApps.discovery.cache, applications: knownApps.discovery.list() };
});
ipcMain.handle('applications-diagnose', async (event, target) => {
  requireMainWindow(event);
  return knownApps.resolver.resolve(target, true);
});
ipcMain.handle('browser-diagnose', async (event, args) => {
  requireMainWindow(event);
  if (!workspaceBrowser)
    workspaceBrowser = new WorkspaceBrowserManager(mainWindow, compatibilityMode, safeHttps, audit);
  return workspaceBrowser.automate(
    'browser.search_site',
    args,
    'diagnostic-' + crypto.randomUUID(),
  );
});
async function configuredMedia() {
  if (!mediaService) throw Error('音乐服务正在准备');
  const preferences = await privateBackend('/api/desktop/preferences');
  mediaService.configure({ allowBrowserMedia: preferences.browser_media_enabled === true });
  return mediaService;
}
ipcMain.handle('media-snapshot', async (event, selectedId) => {
  requireMainWindow(event);
  if (selectedId !== undefined && (typeof selectedId !== 'string' || selectedId.length > 500))
    throw Error('无效媒体来源');
  return (await configuredMedia()).snapshot({ selectedId });
});
ipcMain.handle('media-control', async (event, action, sessionId, value) => {
  requireMainWindow(event);
  if (
    !['play', 'pause', 'next', 'previous', 'seek', 'volume'].includes(action) ||
    typeof sessionId !== 'string' ||
    sessionId.length > 500 ||
    (value !== undefined && (!Number.isFinite(value) || value < 0))
  )
    throw Error('无效媒体操作');
  return (await configuredMedia()).control(action, sessionId, value);
});
ipcMain.handle('music-diagnostics', async (event, options) => {
  requireMainWindow(event);
  if (!options || typeof options.query !== 'string' || options.query.length > 160)
    throw Error('请输入 160 字以内的歌曲');
  return (await configuredMedia()).diagnose({
    query: options.query,
    launch: options.launch === true,
    search: options.search === true,
  });
});
ipcMain.handle('api-presets', (event) => {
  requireMainWindow(event);
  return apiConfig.listPresets();
});
ipcMain.handle('api-model-roles', (event, roles) => {
  requireMainWindow(event);
  return apiConfig.setRoles(roles);
});
ipcMain.handle('api-model-role-status', (event) => {
  requireMainWindow(event);
  return apiConfig.roleStatus();
});
ipcMain.handle('api-test', (event, draft) => {
  requireMainWindow(event);
  return apiConfig.testConnection(draft);
});
ipcMain.handle('api-apply', (event, draft, force) => {
  requireMainWindow(event);
  return apiConfig.apply(draft, force);
});
ipcMain.handle('api-activate', (event, id) => {
  requireMainWindow(event);
  return apiConfig.activate(id);
});
ipcMain.handle('api-delete', (event, id) => {
  requireMainWindow(event);
  return apiConfig.deletePreset(id);
});
ipcMain.handle('api-models', (event, draft) => {
  requireMainWindow(event);
  return apiConfig.fetchModels(draft);
});
ipcMain.handle('fish-settings', (event) => {
  requireMainWindow(event);
  return fishConfig.current();
});
ipcMain.handle('fish-apply', (event, draft) => {
  requireMainWindow(event);
  return fishConfig.apply(draft);
});
ipcMain.handle('fish-test', (event) => {
  requireMainWindow(event);
  return fishConfig.testVoice();
});
ipcMain.handle('fish-stream-test', (event) => {
  requireMainWindow(event);
  return fishConfig.testStream();
});
ipcMain.handle('fish-character-test', (event) => {
  requireMainWindow(event);
  return fishConfig.testCharacter();
});
ipcMain.handle('fish-compare', (event) => {
  requireMainWindow(event);
  return fishConfig.compare();
});
ipcMain.handle('fish-v72-scenario', (event, index) => {
  requireMainWindow(event);
  return fishConfig.testScenario(index);
});
ipcMain.handle('music-select-folder', async (event) => {
  requireMainWindow(event);
  const choice = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'],
    title: '选择本地音乐文件夹',
  });
  if (choice.canceled || !choice.filePaths[0]) return null;
  const response = await fetch(`${backend.url}/api/music/folder`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Companion-Control-Token': backendManager.controlToken,
    },
    body: JSON.stringify({ folder: choice.filePaths[0] }),
    signal: AbortSignal.timeout(20000),
  });
  if (!response.ok) throw new Error(`读取音乐文件夹失败：HTTP ${response.status}`);
  return response.json();
});
ipcMain.handle('work-select-folder', async (event, conversationId) => {
  requireMainWindow(event);
  if (!conversationId) throw Error('请先建立工作会话');
  const choice = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'],
    title: '选择工作项目文件夹',
  });
  if (choice.canceled || !choice.filePaths[0]) return null;
  const selected = fs.realpathSync(choice.filePaths[0]);
  await privateBackend('/api/work/workspace', { conversation_id: conversationId, root: selected });
  return selected;
});
ipcMain.handle('work-preferences', async (event, patch) => {
  requireMainWindow(event);
  const response = await fetch(backend.url + '/api/desktop/preferences', {
    method: patch ? 'PATCH' : 'GET',
    headers: {
      'Content-Type': 'application/json',
      'X-Companion-Control-Token': backendManager.controlToken,
    },
    body: patch ? JSON.stringify(patch) : undefined,
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw Error('工作设置未能保存');
  return response.json();
});
ipcMain.handle('appearance-select', async (event, kind) => {
  requireMainWindow(event);
  if (!['background', 'work_background', 'avatar'].includes(kind))
    throw new Error('Invalid appearance type');
  const choice = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    title:
      kind === 'background'
        ? '选择聊天背景'
        : kind === 'work_background'
          ? '选择工作背景'
          : '选择角色头像',
    filters: [{ name: '图片', extensions: ['png', 'jpg', 'jpeg', 'webp'] }],
  });
  if (choice.canceled || !choice.filePaths[0]) return null;
  const source = choice.filePaths[0];
  const ext = path.extname(source).toLowerCase();
  if (
    !['.png', '.jpg', '.jpeg', '.webp'].includes(ext) ||
    fs.statSync(source).size > 20 * 1024 * 1024
  )
    throw new Error('图片格式错误或超过 20 MB');
  const base = path.join(paths.userData, 'themes');
  const folder = path.join(base, kind.endsWith('background') ? 'backgrounds' : 'avatars');
  fs.mkdirSync(folder, { recursive: true });
  const filename = crypto.randomUUID() + ext;
  fs.copyFileSync(source, path.join(folder, filename));
  const themeFile = path.join(base, 'theme.json');
  const theme = appearanceConfig.assets();
  theme[kind] = filename;
  fs.writeFileSync(themeFile, JSON.stringify(theme), 'utf8');
  return `${backend.url}/api/appearance/${kind}?v=${path.parse(filename).name}`;
});
ipcMain.handle('music-open-official', async (event, provider, query) => {
  requireMainWindow(event);
  if (!['netease', 'qqmusic'].includes(provider)) throw new Error('Unknown music provider');
  if (!knownApps.detect(provider).installed)
    throw new Error('未找到音乐桌面客户端，请先安装或添加已知应用');
  if (!(await permissions.authorize(mainWindow, `app.launch:${provider}`)))
    throw new Error('已取消打开音乐客户端');
  const result = await knownApps.launch(provider);
  if (!result.verified) throw Error(result.error || '没有验证到音乐客户端启动');
  return result.name;
});
ipcMain.handle('known-apps', (event) => {
  requireMainWindow(event);
  return knownApps.ids.map((id) => knownApps.info(id));
});
ipcMain.handle('known-app-register', async (event) => {
  requireMainWindow(event);
  const choice = await dialog.showOpenDialog(mainWindow, {
    title: '添加已安装的应用',
    properties: ['openFile'],
    filters: [{ name: 'Windows 应用／快捷方式', extensions: ['exe', 'lnk'] }],
  });
  if (choice.canceled) return null;
  return knownApps.register(null, choice.filePaths[0]);
});
ipcMain.handle('known-app-launch', async (event, id, taskId) => {
  requireMainWindow(event);
  if (!knownApps.ids.includes(id)) throw new Error('未知应用');
  const state = knownApps.detect(id);
  if (!state.installed) {
    audit('app-launch.log', 'not_installed', id);
    return { ...state, status: 'not_installed' };
  }
  if (
    !(await permissions.authorize(mainWindow, `app.launch:${id}`, `打开 ${state.name}？`, taskId))
  ) {
    audit('permissions.log', 'denied', `app.launch:${id}`);
    return { ...state, status: 'cancelled' };
  }
  const launched = await knownApps.launch(id);
  audit('app-launch.log', 'launch_requested', id);
  return launched;
});
ipcMain.handle('known-app-status', (event, id) => {
  requireMainWindow(event);
  return knownApps.status(id);
});
ipcMain.handle('known-app-focus', async (event, id) => {
  requireMainWindow(event);
  if (!(await permissions.authorize(mainWindow, `app.focus:${id}`, `聚焦 ${id}？`)))
    return { ...(await knownApps.status(id)), focused: false };
  return knownApps.focus(id);
});
ipcMain.handle('known-app-close', async (event, id) => {
  requireMainWindow(event);
  const state = await knownApps.status(id);
  if (!state.pid) return { ...state, closed: false };
  const choice = await dialog.showMessageBox(mainWindow, {
    type: 'warning',
    buttons: ['取消', '关闭应用'],
    defaultId: 0,
    cancelId: 0,
    title: '确认关闭应用',
    message: `关闭 ${state.name}？`,
    detail: '未保存的内容可能丢失。',
  });
  if (choice.response !== 1) {
    audit('app-launch.log', 'close_cancelled', id);
    return { ...state, closed: false };
  }
  const closed = await knownApps.close(id);
  audit('app-launch.log', 'close_requested', id);
  return closed;
});
ipcMain.handle('work-system-status', async (event) => {
  requireMainWindow(event);
  const cpus = os.cpus();
  let disk = null;
  let gpu = '当前兼容模式未提供';
  try {
    const info = await Promise.race([
      app.getGPUInfo('basic'),
      new Promise((resolve) => setTimeout(() => resolve(null), 1500)),
    ]);
    const device = info?.gpuDevice?.[0];
    if (device)
      gpu = String(
        device.deviceString ||
          device.vendorString ||
          `Vendor ${device.vendorId ?? '?'} · Device ${device.deviceId ?? '?'}`,
      ).slice(0, 120);
  } catch {
    /* GPU process may be unavailable in compatibility mode. */
  }
  try {
    const info = fs.statfsSync(paths.home);
    disk = { total: info.bsize * info.blocks, free: info.bsize * info.bavail };
  } catch {
    /* volume unavailable */
  }
  return {
    cpu: cpus[0]?.model || '未知',
    cores: cpus.length,
    memory_total: os.totalmem(),
    memory_free: os.freemem(),
    disk,
    network: Object.values(os.networkInterfaces()).some((entries) =>
      entries?.some((entry) => !entry.internal),
    ),
    gpu,
  };
});
ipcMain.handle('permissions-list', (event) => {
  requireMainWindow(event);
  return permissions.request('');
});
ipcMain.handle('permissions-set', (event, action, scope, task_id) => {
  requireMainWindow(event);
  return permissions.request('', { action, scope, task_id });
});
ipcMain.handle('context-menu', (event, type, state) => {
  requireMainWindow(event);
  if (!['input', 'text', 'assistant', 'user'].includes(type)) throw Error('无效菜单');
  return contextMenus.show(mainWindow, type, state, audit);
});
ipcMain.handle('ui-diagnostic', (event, file, detail) => {
  requireMainWindow(event);
  if (
    ![
      'conversation-routing.log',
      'voice-sync.log',
      'voice-continuity.log',
      'voice-latency.log',
      'audio-chunks.log',
      'workbench-ui.log',
    ].includes(file)
  )
    throw Error('无效日志');
  audit(file, 'renderer', JSON.stringify(detail));
  return true;
});
ipcMain.handle('agent-open-url', async (event, url, rect, context) => {
  requireMainWindow(event);
  return openWorkspaceBrowser(url, rect, context);
});
ipcMain.handle('workspace-browser-open', async (event, url, rect, context) => {
  requireMainWindow(event);
  return openWorkspaceBrowser(url, rect, context);
});
ipcMain.handle('workspace-browser-bounds', (event, rect) => {
  requireMainWindow(event);
  workspaceBrowser?.bounds(rect);
  return true;
});
ipcMain.handle('workspace-browser-action', async (event, action, id) => {
  requireMainWindow(event);
  return workspaceBrowser?.action(action, id) || { url: '', title: '', visible: false };
});
async function nativeAudio(action, body) {
  const response = await fetch(`${backend.url}/api/voice/native/${action}`, {
    method: action === 'status' ? 'GET' : 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Companion-Control-Token': backendManager.controlToken,
    },
    body: action === 'status' ? undefined : JSON.stringify(body || {}),
    signal: AbortSignal.timeout(10000),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
  return result;
}
ipcMain.handle('native-audio-play', (event, url, volume) => {
  requireMainWindow(event);
  const parsed = new URL(url);
  if (
    parsed.origin !== backend.url ||
    !(
      /^\/api\/voice\//.test(parsed.pathname) ||
      /^\/api\/music\/audio\/[0-9a-f]{24}$/.test(parsed.pathname)
    )
  )
    throw new Error('Invalid audio source');
  return nativeAudio('play', { audio_url: parsed.pathname, volume });
});
for (const action of ['pause', 'resume', 'stop', 'status']) {
  ipcMain.handle(`native-audio-${action}`, (event) => {
    requireMainWindow(event);
    return nativeAudio(action);
  });
}
ipcMain.handle('native-pcm-capability', (event) => {
  requireMainWindow(event);
  return { supported: process.platform === 'win32' };
});
ipcMain.handle('voice-stream-cancel', (event, body) => {
  requireMainWindow(event);
  return nativeAudio('stream/cancel', body);
});
for (const action of ['start', 'feed', 'finish'])
  ipcMain.handle('native-pcm-' + action, (event, body) => {
    requireMainWindow(event);
    if (
      action === 'feed' &&
      (!body || typeof body.pcm_base64 !== 'string' || body.pcm_base64.length > 1400000)
    )
      throw Error('无效 PCM 缓冲');
    return nativeAudio('stream/' + action, body);
  });
if (haveLock)
  app.whenReady().then(async () => {
    try {
      Menu.setApplicationMenu(null);
      session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) =>
        callback(false),
      );
      if (
        !isDev &&
        !app.isPackaged &&
        !fs.existsSync(path.join(paths.projectRoot, 'desktop', 'frontend', 'dist', 'index.html'))
      ) {
        throw new Error('Frontend build missing. Run npm run build in desktop first.');
      }
      await backendManager.startBackend(() => app.quit());
      try {
        await apiConfig.restoreActive();
      } catch (error) {
        log(`API preset restore failed: ${error.message}`);
      }
      try {
        await fishConfig.restore();
      } catch (error) {
        log(`Fish settings restore failed: ${error.message}`);
      }
      log('Backend healthy; opening window');
      createWindow();
      if (
        knownApps.discovery.originalVersion < 5 ||
        !knownApps.discovery.cache.scanned_at ||
        Date.now() - Date.parse(knownApps.discovery.cache.scanned_at) > 86400000
      )
        setTimeout(
          () =>
            void knownApps.discovery
              .scan()
              .catch((error) => log(`Application discovery: ${error.message}`)),
          4000,
        );
      const desktopAgent = new DesktopAppAgent(
        knownApps,
        paths,
        audit,
        async (operation, args, taskId) => {
          if (!workspaceBrowser)
            workspaceBrowser = new WorkspaceBrowserManager(
              mainWindow,
              compatibilityMode,
              safeHttps,
              audit,
            );
          return workspaceBrowser.automate(operation, args, taskId);
        },
      );
      const { MediaSessionService } = require('./media-session-service.cjs');
      mediaService = new MediaSessionService({
        controller: desktopAgent.controller,
        registry: knownApps,
        cache: paths.cache,
        audit,
        adapterFor: (app) => desktopAgent.adapter(app, 'music-center'),
      });
      desktopPump = new DesktopCommandPump(backend.url, backendManager.controlToken, desktopAgent);
      void desktopPump.start();
    } catch (error) {
      log(`Startup failed: ${error.stack || error}`);
      dialog.showErrorBox(brand.appName + ' 无法启动', String(error));
      app.quit();
    }
  });

app.on('window-all-closed', () => app.quit());
// 释放本实例拥有的资源；禁止按应用通用名称结束其他安装版/个人版进程。
app.on('before-quit', () => {
  projectWatcher?.close();
  clearTimeout(projectWatchTimer);
  mediaService?.stop();
  desktopPump?.stop();
  backendManager.stopBackend();
});
app.on('will-quit', () => backendManager.stopBackend());
