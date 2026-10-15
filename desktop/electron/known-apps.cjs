const fs = require('node:fs');
const path = require('node:path');
const { shell } = require('electron');
const paths = require('./paths.cjs');
const registryFile = path.join(paths.userData, 'known-applications.json');
const { ApplicationDiscoveryService } = require('./application-discovery.cjs');
const { normalizeRecord, windowsPath } = require('./application-record.cjs');
const { AppLauncher, applicationWindows } = require('./app-launcher.cjs');
let controller = null,
  launcher = null;
const discovery = new ApplicationDiscoveryService(paths);
const resolver = new (require('./runtime-application-resolver.cjs').RuntimeApplicationResolver)(
  discovery,
);
let registry = { custom: {}, last_used: {} };
try {
  registry = { ...registry, ...JSON.parse(fs.readFileSync(registryFile, 'utf8')) };
} catch {}
for (const [id, item] of Object.entries(registry.custom))
  if (!discovery.find(id)) discovery.addCustom(id, item.name, item.path);
function saveRegistry() {
  const tmp = registryFile + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(registry), 'utf8');
  fs.renameSync(tmp, registryFile);
}

const DEFINITIONS = Object.freeze({
  netease: { label: '网易云音乐', exe: ['cloudmusic.exe'], official: 'https://music.163.com/' },
  qqmusic: { label: 'QQ 音乐', exe: ['QQMusic.exe'], official: 'https://y.qq.com/' },
  vscode: {
    label: 'Visual Studio Code',
    exe: ['Code.exe'],
    official: 'https://code.visualstudio.com/',
  },
  notepad: {
    label: '记事本',
    exe: ['notepad.exe'],
    app_user_model_ids: ['Microsoft.WindowsNotepad_8wekyb3d8bbwe!App'],
    official: '',
  },
  explorer: { label: '文件资源管理器', exe: ['explorer.exe'], dirs: [], official: '' },
  browser: {
    label: '浏览器',
    exe: ['msedge.exe', 'chrome.exe', 'firefox.exe'],
    dirs: ['Microsoft/Edge/Application', 'Google/Chrome/Application', 'Mozilla Firefox'],
    official: '',
  },
  calculator: { label: '计算器', exe: ['CalculatorApp.exe'], dirs: [], official: '' },
  settings: { label: 'Windows 设置', exe: [], dirs: [], official: '' },
  qq: { label: 'QQ', exe: ['QQ.exe'], dirs: ['Tencent/QQNT', 'Tencent/QQ/Bin'], official: '' },
});

/** 按登记 ID/客户端别名读取安装信息；不把 installed 当作启动成功。 */
function detect(id) {
  if (registry.custom[id] && discovery.find(id)) {
    const row = discovery.find(id);
    return { ...row, id, path: row.executable || null, last_used: registry.last_used[id] || null };
  }
  const direct = discovery.find(id);
  const installed = direct ? [] : discovery.list().filter((row) => row.installed);
  const discovered =
    direct ||
    // 现代记事本可能只有系统登记的 AppsFolder 身份，没有 EXE 路径。
    // 保留确切 AUMID 供启动及后续进程归属验证；不按显示名称猜测身份。
    installed.find((row) =>
      DEFINITIONS[id]?.app_user_model_ids?.some(
        (aumid) => aumid.toLowerCase() === String(row.app_user_model_id || '').toLowerCase(),
      ),
    ) ||
    installed.find((row) =>
      DEFINITIONS[id]?.exe.some(
        (exe) => path.basename(row.executable || '').toLowerCase() === exe.toLowerCase(),
      ),
    );
  if (discovered) {
    // Explorer's registered Shell entry uses an implicit identity, not a package
    // process ID. Windows defines its host; an arbitrary path cannot replace it.
    const systemHost =
      discovered.app_user_model_id === 'Microsoft.Windows.Explorer'
        ? path.join(process.env.WINDIR || 'C:\\Windows', 'explorer.exe')
        : null;
    const executable =
      discovered.executable || (systemHost && fs.existsSync(systemHost) ? systemHost : null);
    return {
      ...discovered,
      id,
      name: discovered.name,
      installed: discovered.installed,
      path: executable,
      official: DEFINITIONS[id]?.official || '',
      display_name: discovered.name,
    };
  }
  const item = DEFINITIONS[id];
  if (!item) throw new Error('未知应用');
  if (id === 'settings' || id === 'calculator')
    return normalizeRecord({
      app_id: id,
      name: item.label,
      source: 'system',
      protocol: id === 'settings' ? 'ms-settings:' : 'ms-calculator:',
    });
  if (id === 'notepad' || id === 'explorer') {
    const native =
      id === 'explorer'
        ? path.join(process.env.WINDIR || 'C:\\Windows', 'explorer.exe')
        : path.join(process.env.WINDIR || 'C:\\Windows', 'System32', 'notepad.exe');
    return {
      id,
      name: item.label,
      installed: fs.existsSync(native),
      path: fs.existsSync(native) ? native : null,
      official: item.official,
    };
  }
  return { id, name: item.label, installed: false, path: null, official: item.official };
}

/** 由桌面 Agent 注入本实例的窗口控制器，保持索引和操作的目标来源一致。 */
function setController(value, audit = () => {}) {
  controller = value;
  discovery.audit = audit;
  launcher = new AppLauncher({ discovery, shell, controller, audit });
}
/** 已授权应用经 AppLauncher 验证后才更新最后使用时间。 */
async function launch(id, options = {}) {
  if (!launcher) throw Error('桌面应用验证服务尚未就绪');
  const found = normalizeRecord(detect(id));
  found.id = id;
  if (!found.installed) return { ...found, status: 'not_installed', verified: false };
  const state = await launcher.launch(found, options);
  if (state.verified) {
    registry.last_used[id] = new Date().toISOString();
    saveRegistry();
  }
  return state;
}
async function status(id) {
  const found = detect(id);
  const state =
    found.installed && controller
      ? await controller.observe_app(id)
      : { processes: [], windows: [] };
  return {
    ...found,
    ...state,
    pid: state.processes[0]?.pid || null,
    status: state.processes.length ? 'running' : found.installed ? 'installed' : 'not_installed',
  };
}
async function focus(id) {
  const state = await status(id),
    window = applicationWindows(state, state)[0];
  if (!window) return { ...state, focused: false, verified: false };
  return { ...state, ...(await controller.focus_window(id, window.handle)), verified: true };
}
async function close(id) {
  const state = await status(id);
  if (!state.windows.length)
    return { ...state, closed: false, verified: false, error: '未找到可关闭的应用窗口' };
  for (const window of state.windows) await controller.call(id, 'close', { handle: window.handle });
  const deadline = Date.now() + 5000;
  let remaining;
  do {
    remaining = await controller.observe_app(id);
    if (!remaining.windows.length) break;
    await new Promise((resolve) => setTimeout(resolve, 200));
  } while (Date.now() < deadline);
  return {
    ...state,
    ...remaining,
    closed: !remaining.windows.length,
    verified: !remaining.windows.length,
    error: remaining.windows.length ? '窗口仍然打开；请检查保存或退出提示。' : null,
  };
}
function register(name, filename) {
  const resolved = windowsPath(fs.realpathSync(filename)),
    extension = path.extname(resolved).toLowerCase();
  if (!['.exe', '.lnk', '.url'].includes(extension) || !fs.statSync(resolved).isFile())
    throw Error('请选择已安装程序或其快捷方式');
  const existing = Object.entries(registry.custom).find(
    ([, item]) => item.path.toLowerCase() === resolved.toLowerCase(),
  );
  const id = existing?.[0] || 'custom_' + require('node:crypto').randomUUID().replaceAll('-', '');
  const label = String(name || path.basename(resolved, extension)).slice(0, 80);
  if (extension === '.url') throw Error('请先重新扫描已登记的协议快捷方式，或选择 EXE／LNK');
  registry.custom[id] = { name: label, path: resolved };
  saveRegistry();
  discovery.addCustom(
    id,
    label,
    extension === '.lnk' ? '' : resolved,
    extension === '.lnk' ? { shortcut_path: resolved } : {},
  );
  return detect(id);
}
// Listing a catalog must not launch one synchronous tasklist process per entry.
// Live process observations are requested only by the explicit status operation.
function info(id) {
  const state = detect(id);
  return {
    ...state,
    status: state.installed ? 'installed' : 'not_installed',
    display_name: state.name,
    executable: state.path,
    icon: state.icon || state.path,
    last_used: registry.last_used[id] || null,
  };
}
module.exports = {
  detect,
  launch,
  status,
  focus,
  close,
  register,
  info,
  discovery,
  resolver,
  setController,
  get ids() {
    return [
      ...new Set([
        ...Object.keys(DEFINITIONS),
        ...Object.keys(registry.custom),
        ...discovery.list().map((r) => r.app_id),
      ]),
    ];
  },
};
