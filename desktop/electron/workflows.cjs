'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

function uuid(value) {
  if (typeof value !== 'string' || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value))
    throw Error('工作流编号无效');
  return value;
}
function object(value, keys, limit = 600000) {
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    JSON.stringify(value).length > limit ||
    Object.keys(value).some((k) => !keys.includes(k))
  )
    throw Error('工作流参数无效');
}
function revision(value) {
  if (!Number.isSafeInteger(value) || value < 0) throw Error('工作流修订号无效');
  return value;
}

module.exports = ({
  ipcMain,
  requireMainWindow,
  request,
  dialog,
  exportsDirectory,
  authorizeMCP,
  backendURL,
  controlToken,
}) => {
  const subscriptions = new Map();
  const watched = new WeakSet();
  function stop(sender) {
    subscriptions.get(sender.id)?.abort();
    subscriptions.delete(sender.id);
  }
  async function confirm(event, message, detail) {
    requireMainWindow(event);
    const result = await dialog.showMessageBox({
      type: 'warning',
      title: '工作流执行确认',
      message,
      detail,
      buttons: ['取消', '确认'],
      defaultId: 0,
      cancelId: 0,
      noLink: true,
    });
    requireMainWindow(event);
    if (result.response !== 1) throw Error('已取消，工作流未获得本次授权');
  }
  ipcMain.handle('workflow-read', (event, operation, options = {}) => {
    requireMainWindow(event);
    object(
      options,
      ['workflow_id', 'version_id', 'run_id', 'conversation_id', 'template_id', 'after'],
      2000,
    );
    if (['list', 'catalog', 'templates'].includes(operation))
      return request('/api/workflows' + (operation === 'list' ? '' : '/' + operation));
    if (operation === 'template') {
      if (!['project-review', 'code-test-report', 'mcp-research'].includes(options.template_id))
        throw Error('模板无效');
      return request('/api/workflows/templates/' + options.template_id);
    }
    if (operation === 'version')
      return request('/api/workflow-versions/' + uuid(options.version_id));
    if (['detail', 'versions'].includes(operation))
      return request(
        '/api/workflows/' +
          uuid(options.workflow_id) +
          (operation === 'versions' ? '/versions' : ''),
      );
    const query = new URLSearchParams({ conversation_id: uuid(options.conversation_id) });
    if (operation === 'runs') {
      if (options.workflow_id) query.set('workflow_id', uuid(options.workflow_id));
      return request('/api/workflow-runs?' + query);
    }
    let suffix = '';
    if (operation === 'resume-preview') suffix = '/resume-preview';
    else if (operation === 'events') {
      suffix = '/events';
      query.set('after', revision(options.after || 0));
    } else if (operation !== 'run') throw Error('工作流读取操作无效');
    return request('/api/workflow-runs/' + uuid(options.run_id) + suffix + '?' + query);
  });

  ipcMain.handle('workflow-control', async (event, operation, options = {}) => {
    requireMainWindow(event);
    object(options, [
      'definition',
      'revision',
      'workflow_id',
      'version_id',
      'run_id',
      'conversation_id',
      'inputs',
      'approval_id',
      'decision',
      'action',
    ]);
    if (operation === 'confirm-discard') {
      const result = await dialog.showMessageBox({
        type: 'question',
        title: '工作流执行确认',
        message: '当前草稿尚未保存，要放弃这些编辑吗？',
        buttons: ['保留编辑', '放弃编辑'],
        defaultId: 0,
        cancelId: 0,
        noLink: true,
      });
      requireMainWindow(event);
      return { discard: result.response === 1 };
    }
    if (operation === 'save') {
      object(options.definition, [
        'workflow_id',
        'name',
        'description',
        'schema_version',
        'version',
        'status',
        'nodes',
        'edges',
        'input_schema',
        'output_schema',
        'required_capabilities',
        'budget',
      ]);
      const rev = revision(options.revision);
      return request(
        '/api/workflows' + (rev ? '/' + uuid(options.definition.workflow_id) : ''),
        { definition: options.definition, revision: rev },
        rev ? 'PATCH' : 'POST',
      );
    }
    if (operation === 'validate' || operation === 'publish')
      return request('/api/workflows/' + uuid(options.definition?.workflow_id) + '/' + operation, {
        definition: options.definition,
        revision: revision(options.revision),
      });
    if (operation === 'delete') {
      await confirm(event, '删除这个工作流草稿？', '不可变版本和运行历史仍保留。');
      return request(
        '/api/workflows/' + uuid(options.workflow_id) + '?revision=' + revision(options.revision),
        {},
        'DELETE',
      );
    }
    if (operation === 'archive')
      return request('/api/workflows/' + uuid(options.workflow_id) + '/archive', {
        revision: revision(options.revision),
      });
    if (operation === 'import') {
      const selected = await dialog.showOpenDialog({
        title: '导入工作流 JSON（不会执行或授权）',
        properties: ['openFile'],
        filters: [{ name: 'Workflow JSON', extensions: ['json'] }],
      });
      requireMainWindow(event);
      if (selected.canceled || selected.filePaths.length !== 1) return { cancelled: true };
      const stat = await fs.lstat(selected.filePaths[0]);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 512000)
        throw Error('仅支持不超过 500 KB 的普通 JSON 文件');
      const definition = JSON.parse(await fs.readFile(selected.filePaths[0], 'utf8'));
      object(definition, [
        'workflow_id',
        'name',
        'description',
        'schema_version',
        'version',
        'status',
        'nodes',
        'edges',
        'input_schema',
        'output_schema',
        'required_capabilities',
        'budget',
      ]);
      return request('/api/workflows', {
        definition: { ...definition, workflow_id: randomUUID(), version: 0, status: 'DRAFT' },
        revision: 0,
      });
    }
    if (operation === 'export') {
      const saved = await request('/api/workflows/' + uuid(options.workflow_id));
      const directory = path.join(exportsDirectory, 'Workflows');
      await fs.mkdir(directory, { recursive: true });
      const filename = 'workflow-' + randomUUID() + '.json';
      await fs.writeFile(
        path.join(directory, filename),
        JSON.stringify(saved.definition, null, 2) + '\n',
        { flag: 'wx', encoding: 'utf8' },
      );
      return { filename, contains_run_data: false };
    }
    const owner = uuid(options.conversation_id);
    if (operation === 'start') {
      const version = uuid(options.version_id);
      const overview = await request(
        '/api/workflow-versions/' + version + '/overview?conversation_id=' + owner,
      );
      await confirm(
        event,
        '运行这个不可变工作流版本？',
        `名称：${overview.name} · v${overview.version}\n工作文件夹：${overview.workspace || '未选择（仅隔离草稿）'}\n节点、工具与能力：${JSON.stringify(overview.nodes)}\n本次输入：${JSON.stringify(options.inputs || {})}\n预算：${JSON.stringify(overview.budget)}\n可能调用当前配置的模型并产生费用。此确认不预先授权后续工具；参数解析后按原权限逐项确认。`,
      );
      return request('/api/workflows/' + uuid(options.workflow_id) + '/runs', {
        version_id: version,
        conversation_id: owner,
        inputs: options.inputs || {},
        renderer_id: String(event.sender.id),
      });
    }
    const runId = uuid(options.run_id);
    if (operation === 'approve') {
      if (typeof options.decision !== 'boolean') throw Error('审批选择无效');
      const run = await request('/api/workflow-runs/' + runId + '?conversation_id=' + owner);
      const pending = run.nodes.find(
        (n) => n.pending?.approval_id === uuid(options.approval_id),
      )?.pending;
      if (!pending || run.state !== 'WAITING_PERMISSION') throw Error('当前审批已过期');
      if (options.decision) {
        if (pending.kind === 'tool') {
          if (pending.call.name.startsWith('mcp.'))
            await authorizeMCP(event, pending.plan_id, [pending.call.id]);
          else
            await confirm(
              event,
              '授权这个节点的实际工具调用？',
              `工具：${pending.call.name}\n风险：${pending.risk}\n参数：${JSON.stringify(pending.call.arguments)}\n脚本摘要：${pending.call.workflow_script_sha256 || '不适用'}\n外部程序和脚本具有当前账号权限，其自身请求不属于宿主模型预算保证。\n仅授权本次节点与这些参数，不提升流程的后续权限。`,
            );
        } else
          await confirm(
            event,
            '批准这个人工检查节点？',
            `${pending.message}\n内容：${JSON.stringify(pending.value)}\n这个批准不授予后续工具权限。`,
          );
      }
      return request('/api/workflow-runs/' + runId + '/approve', {
        conversation_id: owner,
        approval_id: options.approval_id,
        decision: options.decision,
      });
    }
    if (operation === 'resume') {
      const preview = await request(
        '/api/workflow-runs/' + runId + '/resume-preview?conversation_id=' + owner,
      );
      if (!preview.can_resume || preview.revision !== revision(options.revision))
        throw Error('检查点已变化或需要人工核查');
      await confirm(
        event,
        '按重新核验的检查点续接？',
        JSON.stringify(preview.items) +
          '\n已确认完成的节点不重复执行；未知副作用不自动重放。后续工具仍需各自授权。',
      );
      return request('/api/workflow-runs/' + runId + '/resume', {
        conversation_id: owner,
        revision: preview.revision,
      });
    }
    if (operation === 'control' && ['pause', 'continue', 'cancel'].includes(options.action))
      return request('/api/workflow-runs/' + runId + '/control', {
        conversation_id: owner,
        action: options.action,
      });
    throw Error('工作流控制操作无效');
  });

  ipcMain.handle('workflow-watch', (event, options) => {
    requireMainWindow(event);
    stop(event.sender);
    if (options === null) return true;
    object(options, ['run_id', 'conversation_id', 'after'], 2000);
    const runId = uuid(options.run_id),
      owner = uuid(options.conversation_id);
    let after = revision(options.after || 0);
    const controller = new AbortController();
    subscriptions.set(event.sender.id, controller);
    if (!watched.has(event.sender)) {
      watched.add(event.sender);
      event.sender.once('destroyed', () => stop(event.sender));
      event.sender.once('render-process-gone', () => stop(event.sender));
    }
    const send = (data) => {
      if (!controller.signal.aborted && !event.sender.isDestroyed())
        event.sender.send('workflow-progress', { run_id: runId, conversation_id: owner, ...data });
    };
    (async () => {
      for (let attempt = 0; attempt < 3 && !controller.signal.aborted; attempt++) {
        try {
          const url = new URL('/api/workflow-runs/' + runId + '/stream', backendURL);
          url.searchParams.set('conversation_id', owner);
          url.searchParams.set('after', after);
          const response = await fetch(url, {
            headers: { 'X-Companion-Control-Token': controlToken },
            signal: controller.signal,
          });
          if (!response.ok || !response.body) throw Error('工作流事件订阅失败');
          const decoder = new TextDecoder();
          let buffer = '';
          for await (const data of response.body) {
            buffer += decoder.decode(data, { stream: true });
            if (buffer.length > 64000) throw Error('工作流事件超过限制');
            let end;
            while ((end = buffer.indexOf('\n\n')) >= 0) {
              const block = buffer.slice(0, end);
              buffer = buffer.slice(end + 2);
              const payload = block.split('\n').find((line) => line.startsWith('data: '));
              if (!payload) continue;
              const item = JSON.parse(payload.slice(6));
              if (!Number.isSafeInteger(item.cursor) || item.cursor <= after) continue;
              after = item.cursor;
              send({ cursor: after, node_id: item.node_id, state: item.state, kind: item.kind });
            }
          }
          return;
        } catch {
          if (controller.signal.aborted) return;
          if (attempt === 2) send({ disconnected: true, cursor: after });
        }
      }
    })();
    return true;
  });
  return () => {
    for (const controller of subscriptions.values()) controller.abort();
    subscriptions.clear();
  };
};
