'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { safeStorage } = require('electron');
const paths = require('./paths.cjs');
const backend = require('./backend.cjs');
const brand = require('./brand.cjs');

const settingsPath = path.join(paths.userData, 'api-settings.json');
const secretsPath = path.join(paths.userData, 'api-keys.json');
let lastTest = '';

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return fallback;
  }
}
function atomicJson(file, value) {
  const temporary = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(value, null, 2), 'utf8');
  fs.renameSync(temporary, file);
}
function legacyEnvironment() {
  if (brand.clean)
    return {
      file: path.join(paths.userData, '.env'),
      content: '',
      key: '',
      model: '',
      base_url: '',
    };
  const file = fs.existsSync(path.join(paths.userData, '.env'))
    ? path.join(paths.userData, '.env')
    : path.join(paths.projectRoot, '.env');
  const content = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '';
  const get = (name, fallback = '') =>
    content.match(new RegExp(`^${name}=(.*)$`, 'm'))?.[1]?.trim() || fallback;
  return {
    file,
    content,
    key: get('OPENAI_API_KEY'),
    model: get('OPENAI_MODEL', 'deepseek-chat'),
    base_url: get('OPENAI_BASE_URL', 'https://api.deepseek.com/v1'),
  };
}
function decrypt(entry) {
  if (!entry) return '';
  if (!safeStorage.isEncryptionAvailable()) throw new Error('Windows 安全存储目前不可用');
  return safeStorage.decryptString(Buffer.from(entry, 'base64'));
}
function encrypt(value) {
  if (!safeStorage.isEncryptionAvailable())
    throw new Error('Windows 安全存储目前不可用，API Key 未保存');
  return safeStorage.encryptString(value).toString('base64');
}
function defaultStore() {
  if (brand.clean) return { active_id: '', presets: [], roles: {} };
  const legacy = legacyEnvironment();
  return {
    active_id: 'legacy',
    presets: [
      {
        id: 'legacy',
        name: '当前配置',
        provider: 'openai-compatible',
        base_url: legacy.base_url,
        model: legacy.model,
        temperature: null,
        max_tokens: null,
        timeout_seconds: 60,
        capabilities: {
          text: true,
          vision: false,
          tool_calling: null,
          web_search: false,
          structured_output: false,
        },
      },
    ],
  };
}
function store() {
  return readJson(settingsPath, defaultStore());
}
function secrets() {
  return readJson(secretsPath, {});
}

function initialize() {
  if (fs.existsSync(settingsPath)) return;
  const initial = defaultStore();
  const legacy = legacyEnvironment();
  if (legacy.key && safeStorage.isEncryptionAvailable()) {
    const encrypted = encrypt(legacy.key);
    if (decrypt(encrypted) !== legacy.key) throw new Error('API Key 加密校验失败');
    atomicJson(secretsPath, { legacy: encrypted });
  }
  atomicJson(settingsPath, initial);
  // Migrate only the F-drive UserData file. Never edit a source-tree .env.
  if (
    legacy.key &&
    legacy.file === path.join(paths.userData, '.env') &&
    fs.existsSync(secretsPath)
  ) {
    const sanitized = legacy.content.replace(/^OPENAI_API_KEY=.*$/m, 'OPENAI_API_KEY=');
    fs.writeFileSync(`${legacy.file}.${process.pid}.tmp`, sanitized, 'utf8');
    fs.renameSync(`${legacy.file}.${process.pid}.tmp`, legacy.file);
  }
}
/** 渲染层只接收预设元数据与 has_key，不返回明文或加密后的凭据内容。 */
function listPresets() {
  initialize();
  const data = store(),
    saved = secrets();
  return {
    active_id: data.active_id,
    roles: data.roles || {},
    routing: data.routing || null,
    presets: data.presets.map((item) => ({
      ...item,
      capabilities: item.capabilities || {
        text: true,
        vision: false,
        tool_calling: null,
        web_search: false,
        structured_output: false,
      },
      has_key: Boolean(saved[item.id] || (item.id === 'legacy' && legacyEnvironment().key)),
    })),
  };
}
/** 合并待编辑配置与安全存储的密钥，只用于主进程向后端发起配置请求。 */
function configFor(draft) {
  if (!draft || typeof draft !== 'object') throw new Error('缺少 API 配置');
  const saved = secrets();
  const old = draft.id ? store().presets.find((item) => item.id === draft.id) : null;
  const key = draft.clear_key
    ? ''
    : draft.api_key ||
      decrypt(saved[draft.id]) ||
      (draft.id === 'legacy' ? legacyEnvironment().key : '');
  return {
    provider: draft.provider || old?.provider || 'openai-compatible',
    base_url: String(draft.base_url || ''),
    model: String(draft.model || ''),
    api_key: key,
    temperature:
      draft.temperature === '' || draft.temperature == null ? null : Number(draft.temperature),
    max_tokens:
      draft.max_tokens === '' || draft.max_tokens == null ? null : Number(draft.max_tokens),
    timeout_seconds: Number(draft.timeout_seconds || 60),
    capabilities: {
      text: true,
      vision: false,
      tool_calling: null,
      structured_output: null,
      reasoning: null,
      streaming: null,
      ...old?.capabilities,
      ...draft.capabilities,
    },
  };
}
async function callApi(endpoint, body) {
  const response = await fetch(`${backend.address.url}/api/llm/settings${endpoint}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Companion-Control-Token': backend.controlToken,
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(190000),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    // Validation errors may contain rejected configuration. Never reflect a
    // structured body, which can include the main-process API key, to renderer.
    const code =
      detail && typeof detail.code === 'string' && /^[A-Z][A-Z0-9_]{0,79}$/.test(detail.code)
        ? detail.code
        : null;
    throw new Error(typeof detail === 'string' ? detail : code || `HTTP ${response.status}`);
  }
  return data;
}
async function testConnection(draft) {
  const config = configFor(draft);
  const result = await callApi('/test', config);
  lastTest = JSON.stringify(config);
  return result;
}
/**
 * 按测试/保存流程应用配置。加密可行后才变更后端，成功后保存预设及角色路由。
 * force 只控制配置测试步骤，不能作为工具或项目写入的授权。
 */
async function apply(draft, force = false) {
  const config = configFor(draft);
  // Check secure storage before changing the running backend configuration.
  // A successful connection test must never leave an unsaved active key.
  const id = draft.id || randomUUID();
  const encryptedKey = config.api_key ? encrypt(config.api_key) : null;
  if (!force && lastTest !== JSON.stringify(config)) await testConnection(draft);
  await callApi(`/apply?force=${force ? 'true' : 'false'}`, config);
  const data = store(),
    saved = secrets();
  const preset = {
    id,
    config_revision: randomUUID(),
    name: String(draft.name || '自定义 API').slice(0, 50),
    provider: config.provider,
    base_url: config.base_url,
    model: config.model,
    temperature: config.temperature,
    max_tokens: config.max_tokens,
    timeout_seconds: config.timeout_seconds,
    capabilities: {
      text: true,
      vision: Boolean(draft.capabilities?.vision),
      tool_calling:
        draft.capabilities?.tool_calling === true
          ? true
          : draft.capabilities?.tool_calling === false
            ? false
            : null,
      web_search: Boolean(draft.capabilities?.web_search),
      structured_output: draft.capabilities?.structured_output ?? null,
      reasoning: draft.capabilities?.reasoning ?? null,
      streaming: draft.capabilities?.streaming ?? null,
      coding: draft.capabilities?.coding ?? null,
    },
  };
  data.presets = [...data.presets.filter((item) => item.id !== id), preset];
  data.active_id = id;
  if (encryptedKey) saved[id] = encryptedKey;
  else delete saved[id];
  atomicJson(secretsPath, saved);
  atomicJson(settingsPath, data);
  await restoreRoles();
  await restoreRouting();
  lastTest = '';
  return listPresets();
}
async function activate(id) {
  const item = store().presets.find((preset) => preset.id === id);
  if (!item) throw new Error('未找到 API 预设');
  return apply({ ...item, api_key: '' });
}
function deletePreset(id) {
  const data = store();
  if (Object.values(data.roles || {}).includes(id))
    throw Error('请先更改这个预设的模型职责，再删除配置');
  if (
    data.routing?.policy?.enabled &&
    (data.routing.policy.manual_model === id ||
      data.routing.models?.some((row) => row.model_id === id && row.enabled))
  )
    throw new Error('请先在智能模型路由中停用这个预设');
  if (id === data.active_id) throw new Error('请先切换到其他 API 配置，再删除当前配置');
  if (!data.presets.some((item) => item.id === id)) throw new Error('未找到 API 配置');
  data.presets = data.presets.filter((item) => item.id !== id);
  const saved = secrets();
  delete saved[id];
  atomicJson(secretsPath, saved);
  atomicJson(settingsPath, data);
  return listPresets();
}
async function restoreActive() {
  initialize();
  const data = store();
  const item = data.presets.find((preset) => preset.id === data.active_id);
  if (item) await callApi('/apply?force=true', configFor(item));
  await restoreRoles();
  await restoreRouting();
}
const roleNames = ['character_model', 'planning_model', 'tool_execution_model', 'coding_model'];
/** 模型职责引用预设 ID；解密密钥留在本地控制请求，不写入公开角色状态。 */
async function restoreRoles(selection = store().roles || {}) {
  const data = store(),
    roles = {};
  for (const [role, id] of Object.entries(selection)) {
    if (!roleNames.includes(role)) throw Error('未知模型职责');
    if (!id) continue;
    const preset = data.presets.find((item) => item.id === id);
    if (!preset) throw Error('模型职责使用的 API 预设已不存在');
    roles[role] = configFor(preset);
  }
  return callApi('/roles', { roles });
}
async function setRoles(selection) {
  if (!selection || typeof selection !== 'object' || Array.isArray(selection))
    throw Error('缺少模型职责配置');
  const status = await restoreRoles(selection),
    data = store();
  data.roles = selection;
  await restoreRouting(data.routing, data);
  atomicJson(settingsPath, data);
  return { ...listPresets(), role_status: status };
}
async function roleStatus() {
  return callApi('/roles/status', {});
}
async function fetchModels(draft) {
  return callApi('/models', configFor(draft));
}

const routingRecordKeys = new Set([
  'model_id',
  'display_name',
  'model_type',
  'enabled',
  'allow_auto',
  'allow_remote_text',
  'allow_attachments',
  'context_window',
  'input_cost',
  'output_cost',
  'cost_currency',
  'price_source',
  'price_verified_at',
  'priority',
  'config_revision',
]);
/** Routing metadata references existing presets. It cannot supply endpoints or keys. */
async function restoreRouting(selection = store().routing, data = store()) {
  if (!selection) return null;
  const models = [];
  if (!Array.isArray(selection.models) || selection.models.length > 32)
    throw new Error('模型路由最多保存 32 项模型');
  for (const record of selection.models) {
    if (
      !record ||
      typeof record !== 'object' ||
      Object.keys(record).some((key) => !routingRecordKeys.has(key))
    )
      throw new Error('模型路由元数据无效');
    const preset = data.presets.find((item) => item.id === record.model_id);
    if (!preset) {
      if (!selection.policy?.enabled) continue;
      throw new Error('路由引用的 API 预设已不存在');
    }
    models.push({
      record: { ...record, config_revision: preset.config_revision || 'initial' },
      config: configFor(preset),
    });
  }
  const roles = {};
  for (const role of roleNames) {
    const id = data.roles?.[role] || data.roles?.character_model || data.active_id;
    if (id && models.some((row) => row.record.model_id === id)) roles[role] = id;
  }
  return callApi('/routing', { models, policy: selection.policy, roles });
}
async function setRouting(selection) {
  if (
    !selection ||
    typeof selection !== 'object' ||
    Array.isArray(selection) ||
    Object.keys(selection).some((key) => !['models', 'policy'].includes(key))
  )
    throw new Error('缺少有效的模型路由配置');
  const status = await restoreRouting(selection);
  const data = store();
  data.routing = {
    models: status.models.map((row) =>
      Object.fromEntries(Object.entries(row).filter(([key]) => routingRecordKeys.has(key))),
    ),
    policy: status.policy,
  };
  atomicJson(settingsPath, data);
  return status;
}
async function routingStatus() {
  return callApi('/routing/status', {});
}

module.exports = {
  setRouting,
  routingStatus,
  listPresets,
  testConnection,
  apply,
  activate,
  deletePreset,
  restoreActive,
  fetchModels,
  setRoles,
  roleStatus,
};
