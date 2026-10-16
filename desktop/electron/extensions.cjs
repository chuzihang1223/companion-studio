'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

const uuid = (value) => {
  if (typeof value !== 'string' || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value))
    throw Error('扩展编号无效');
  return value;
};
const object = (value, keys, limit = 48000) => {
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    JSON.stringify(value).length > limit ||
    Object.keys(value).some((key) => !keys.includes(key))
  )
    throw Error('扩展参数无效');
};

module.exports = ({
  ipcMain,
  requireMainWindow,
  request,
  dialog,
  safeStorage,
  userData,
  exportsDirectory,
  examplesDirectory,
}) => {
  const selected = new Map();
  const secretFile = path.join(userData, 'extension-credentials.json');
  function secrets() {
    if (!fs.existsSync(secretFile)) return {};
    try {
      return JSON.parse(fs.readFileSync(secretFile, 'utf8'));
    } catch {
      throw Error('扩展凭据存储不可读，未覆盖原文件');
    }
  }
  function writeSecrets(data) {
    const temporary = `${secretFile}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(data), 'utf8');
    fs.renameSync(temporary, secretFile);
  }
  async function confirmed(event, message, detail) {
    requireMainWindow(event);
    const result = await dialog.showMessageBox({
      type: 'warning',
      title: '扩展权限确认',
      message,
      detail,
      buttons: ['取消', '确认'],
      defaultId: 0,
      cancelId: 0,
      noLink: true,
    });
    requireMainWindow(event);
    if (result.response !== 1) throw Error('用户取消，未执行扩展操作');
  }
  async function approvePlan(event, planId, approved) {
    if (
      !Array.isArray(approved) ||
      approved.length > 5 ||
      approved.some((id) => typeof id !== 'string' || id.length > 100)
    )
      throw Error('工具确认编号无效');
    const plan = await request('/api/mcp/plans/' + uuid(planId));
    for (const call of plan.calls.filter((row) => approved.includes(row.id))) {
      await confirmed(
        event,
        '授权本次外部 MCP 操作？',
        `服务器：${call.server_id}\n工具：${call.original_tool_name}\n副作用：${call.side_effect_class}\n参数：${JSON.stringify(call.arguments)}\n授权仅适用于这个任务、工具定义和这些参数。工具返回的受限结果会发送给当前配置的模型用于回答。`,
      );
      await request('/api/mcp/grants', {
        conversation_id: plan.conversation_id,
        work_session_id: plan.work_session_id,
        call_id: call.id,
        scope: 'once',
      });
    }
    return { authorized: true };
  }

  ipcMain.handle('extension-read', async (event, operation, options = {}) => {
    requireMainWindow(event);
    object(options, ['server_id', 'skill_id', 'file']);
    if (operation === 'servers') return request('/api/mcp/servers');
    if (operation === 'skills') return request('/api/skills');
    if (operation === 'template') return request('/api/mcp/export-template');
    if (['tools', 'catalog'].includes(operation))
      return request('/api/mcp/servers/' + uuid(options.server_id) + '/' + operation);
    if (operation === 'skill') return request('/api/skills/' + uuid(options.skill_id));
    if (operation === 'skill-file') {
      if (
        typeof options.file !== 'string' ||
        !options.file ||
        options.file.length > 200 ||
        options.file.split('/').some((p) => !p || p === '.' || p === '..') ||
        /[:\\]/.test(options.file)
      )
        throw Error('Skill 文件路径无效');
      return request(
        '/api/skills/' + uuid(options.skill_id) + '/file?name=' + encodeURIComponent(options.file),
      );
    }
    throw Error('扩展读取操作无效');
  });

  ipcMain.handle('extension-control', async (event, operation, options = {}) => {
    requireMainWindow(event);
    object(options, [
      'server_id',
      'skill_id',
      'tool_id',
      'config',
      'enabled',
      'risk_level',
      'name',
      'text',
      'kind',
      'arguments',
      'import_token',
      'credential',
      'environment',
      'scope',
      'work_session_id',
      'conversation_id',
      'call_id',
      'plan_id',
      'approved_ids',
    ]);
    const serverRoute = () => '/api/mcp/servers/' + uuid(options.server_id);
    if (operation === 'skill-example') {
      if (!['project-review', 'markdown-report', 'workspace-search'].includes(options.name))
        throw Error('示例名称无效');
      const folder = path.join(examplesDirectory, options.name);
      const preview = await request('/api/skills/preview', { folder });
      const token = randomUUID();
      if (selected.size >= 8) selected.delete(selected.keys().next().value);
      selected.set(token, {
        folder,
        hash: preview.content_hash,
        sender: event.sender.id,
        created: Date.now(),
      });
      return { ...preview, import_token: token };
    }
    if (operation === 'template-export') {
      const value = await request('/api/mcp/export-template');
      fs.mkdirSync(exportsDirectory, { recursive: true });
      const filename = 'mcp-template-' + randomUUID() + '.json';
      fs.writeFileSync(path.join(exportsDirectory, filename), JSON.stringify(value, null, 2), {
        encoding: 'utf8',
        flag: 'wx',
      });
      return { filename };
    }
    if (operation === 'template-select') {
      const chosen = await dialog.showOpenDialog({
        title: '预览 MCP 配置模板',
        filters: [{ name: 'JSON', extensions: ['json'] }],
        properties: ['openFile'],
      });
      requireMainWindow(event);
      if (chosen.canceled || !chosen.filePaths[0]) return null;
      const file = chosen.filePaths[0];
      if (!fs.statSync(file).isFile() || fs.statSync(file).size > 64000)
        throw Error('配置模板超过大小限制');
      const value = JSON.parse(fs.readFileSync(file, 'utf8'));
      object(value, ['schema_version', 'servers'], 64000);
      if (value.schema_version !== 1 || !Array.isArray(value.servers) || value.servers.length > 40)
        throw Error('配置模板格式无效');
      return {
        servers: value.servers.map((config) => {
          object(config, [
            'display_name',
            'transport',
            'endpoint_or_command',
            'args',
            'enabled',
            'allow_loopback',
            'environment_names',
            'credential_reference',
            'connect_timeout',
            'call_timeout',
            'protocol_mode',
          ]);
          return {
            ...config,
            enabled: false,
            allow_loopback: false,
            credential_reference: false,
            environment_names: [],
          };
        }),
      };
    }
    if (operation === 'approve-plan') {
      return approvePlan(event, options.plan_id, options.approved_ids);
    }
    if (operation === 'server-save') {
      object(options.config, [
        'display_name',
        'transport',
        'endpoint_or_command',
        'args',
        'enabled',
        'allow_loopback',
        'environment_names',
        'credential_reference',
        'connect_timeout',
        'call_timeout',
        'protocol_mode',
      ]);
      return request(
        options.server_id ? serverRoute() : '/api/mcp/servers',
        options.config,
        options.server_id ? 'PATCH' : 'POST',
      );
    }
    if (operation === 'connect') {
      const state = (await request('/api/mcp/servers')).items.find(
        (row) => row.server_id === uuid(options.server_id),
      );
      if (!state) throw Error('服务器不存在');
      const config = state.config;
      await confirmed(
        event,
        '连接这个 MCP Server？',
        config.transport === 'stdio'
          ? `程序：${config.endpoint_or_command}\n参数：${JSON.stringify(config.args)}\n环境变量名称：${config.environment_names.join(', ')}\n外部程序拥有当前 Windows 账号权限；工具授权无法限制程序本身访问电脑。`
          : `端点：${config.endpoint_or_command}\n数据仅在你明确调用、读取资源或预览 Prompt 时发送。不要连接不可信服务。`,
      );
      const saved = secrets()[state.server_id];
      if (saved && saved.binding === state.config_fingerprint) {
        if (!safeStorage.isEncryptionAvailable()) throw Error('Windows 安全存储不可用');
        let value;
        try {
          value = JSON.parse(safeStorage.decryptString(Buffer.from(saved.value, 'base64')));
        } catch {
          throw Error('无法解密此服务器凭据，请由档案所有者重新录入');
        }
        await request(serverRoute() + '/credentials', value);
      }
      return request(serverRoute() + '/connect', {
        approved_fingerprint: state.config_fingerprint,
      });
    }
    if (['disconnect', 'refresh'].includes(operation))
      return request(serverRoute() + '/' + operation, {});
    if (operation === 'server-remove') {
      await confirmed(
        event,
        '移除此服务器配置？',
        '连接将断开，自有子进程将停止；不会关闭其他同名进程。',
      );
      const result = await request(serverRoute(), {}, 'DELETE');
      const saved = secrets();
      delete saved[uuid(options.server_id)];
      writeSecrets(saved);
      return result;
    }
    if (operation === 'credentials-save') {
      uuid(options.server_id);
      if (!safeStorage.isEncryptionAvailable()) throw Error('Windows 安全存储不可用，凭据未保存');
      if (
        typeof options.credential !== 'string' ||
        options.credential.length > 4096 ||
        /[\r\n]/.test(options.credential)
      )
        throw Error('凭据格式无效');
      const environment = options.environment || {};
      if (
        typeof environment !== 'object' ||
        Array.isArray(environment) ||
        Object.keys(environment).length > 12 ||
        Object.entries(environment).some(
          ([k, v]) => !/^[A-Z][A-Z0-9_]{0,63}$/.test(k) || typeof v !== 'string' || v.length > 4096,
        )
      )
        throw Error('环境变量凭据无效');
      const state = (await request('/api/mcp/servers')).items.find(
        (row) => row.server_id === options.server_id,
      );
      if (!state) throw Error('服务器不存在');
      const value = {
        token: options.credential,
        environment,
        config_fingerprint: state.config_fingerprint,
      };
      const data = secrets();
      data[options.server_id] = {
        binding: state.config_fingerprint,
        value: safeStorage.encryptString(JSON.stringify(value)).toString('base64'),
      };
      writeSecrets(data);
      await request(serverRoute() + '/credentials', value);
      return { saved: true };
    }
    if (operation === 'tool-save') {
      if (typeof options.tool_id !== 'string' || !/^mcp\.[a-f0-9]{40}$/.test(options.tool_id))
        throw Error('工具编号无效');
      return request(
        '/api/mcp/tools/' + options.tool_id,
        { enabled: options.enabled, risk_level: options.risk_level },
        'PATCH',
      );
    }
    if (operation === 'preview') {
      await confirmed(
        event,
        '向这个服务器发送预览请求？',
        `类型：${options.kind}\n名称：${options.name}\n参数：${JSON.stringify(options.arguments || {})}\n返回内容属于低信任数据，不会自动执行。`,
      );
      return request(serverRoute() + '/preview', {
        kind: options.kind,
        name: options.name,
        arguments: options.arguments || {},
        approved: true,
      });
    }
    if (operation === 'skill-select-folder') {
      const chosen = await dialog.showOpenDialog({
        title: '选择含 SKILL.md 的文件夹',
        properties: ['openDirectory'],
      });
      requireMainWindow(event);
      if (chosen.canceled || !chosen.filePaths[0]) return null;
      const folder = chosen.filePaths[0];
      const preview = await request('/api/skills/preview', { folder });
      const token = randomUUID();
      // Short-lived owner-only import ticket; renderer never supplies an arbitrary path.
      for (const [key, item] of selected)
        if (Date.now() - item.created > 300000) selected.delete(key);
      if (selected.size >= 8) selected.delete(selected.keys().next().value);
      selected.set(token, {
        folder,
        hash: preview.content_hash,
        sender: event.sender.id,
        created: Date.now(),
      });
      return { ...preview, import_token: token };
    }
    if (operation === 'skill-import' || operation === 'skill-update') {
      const item = selected.get(uuid(options.import_token));
      if (!item || item.sender !== event.sender.id || Date.now() - item.created > 300000)
        throw Error('导入预览已过期');
      selected.delete(options.import_token);
      return request('/api/skills/import', {
        folder: item.folder,
        expected_hash: item.hash,
        ...(operation === 'skill-update' ? { skill_id: uuid(options.skill_id) } : {}),
      });
    }
    if (operation === 'skill-create' || operation === 'skill-edit') {
      if (
        typeof options.name !== 'string' ||
        typeof options.text !== 'string' ||
        options.text.length > 32000
      )
        throw Error('Skill 内容无效');
      return request(
        operation === 'skill-create' ? '/api/skills' : '/api/skills/' + uuid(options.skill_id),
        { name: options.name, text: options.text },
        operation === 'skill-create' ? 'POST' : 'PUT',
      );
    }
    if (operation === 'skill-toggle')
      return request(
        '/api/skills/' + uuid(options.skill_id),
        { enabled: options.enabled },
        'PATCH',
      );
    if (operation === 'skill-remove')
      return request('/api/skills/' + uuid(options.skill_id), {}, 'DELETE');
    if (operation === 'skill-export') {
      const value = await request('/api/skills/' + uuid(options.skill_id));
      if (value.source !== 'self-authored') throw Error('仅支持导出自建 Skill');
      const raw = await request('/api/skills/' + value.id + '/file?name=SKILL.md');
      // A fixed export directory and filename. No renderer-supplied write path.
      const folder = path.join(exportsDirectory, 'Skills', value.name);
      fs.mkdirSync(folder, { recursive: true });
      const file = path.join(folder, 'SKILL.md');
      if (fs.existsSync(file)) throw Error('导出文件已存在，请先手动归档');
      fs.writeFileSync(file, raw.text, { encoding: 'utf8', flag: 'wx' });
      return { filename: `Skills/${value.name}/SKILL.md` };
    }
    if (operation === 'grant') {
      return request('/api/mcp/grants', {
        conversation_id: uuid(options.conversation_id),
        work_session_id: uuid(options.work_session_id),
        call_id: options.call_id,
        scope: options.scope,
      });
    }
    throw Error('扩展操作无效');
  });
  return { approvePlan };
};
