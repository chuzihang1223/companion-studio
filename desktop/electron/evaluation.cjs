'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

const uuid = (value) => {
  if (typeof value !== 'string' || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value))
    throw Error('无效评测编号');
  return value;
};
const bounded = (value, max, fallback = 0) => {
  if (value === undefined) return fallback;
  if (!Number.isInteger(value) || value < 0 || value > max) throw Error('无效评测分页');
  return value;
};

module.exports = ({
  ipcMain,
  requireMainWindow,
  request,
  exportDirectory,
  backendURL,
  controlToken,
}) => {
  const subscriptions = new Map();
  const watched = new WeakSet();
  let exporting = false;
  const stop = (sender) => {
    subscriptions.get(sender.id)?.abort();
    subscriptions.delete(sender.id);
  };
  ipcMain.handle('evaluation-read', (event, operation, options = {}) => {
    requireMainWindow(event);
    if (!options || typeof options !== 'object' || Array.isArray(options))
      throw Error('无效评测查询');
    const query = new URLSearchParams();
    let route = '/api/evaluation/';
    if (operation === 'datasets' || operation === 'models') route += operation;
    else if (operation === 'dataset') {
      const dataset = options.dataset_id || 'studio-core';
      if (
        ![
          'studio-core',
          'recovery-core',
          'mcp-skills-core',
          'workflow-core',
          'knowledge-core',
          'computer-use-core',
        ].includes(dataset)
      )
        throw Error('未知内置数据集');
      route += 'datasets/' + dataset;
    } else if (operation === 'compare') {
      route += 'compare';
      query.set('left', uuid(options.left));
      query.set('right', uuid(options.right));
    } else if (operation === 'runs') {
      route += 'runs';
      query.set('cursor', bounded(options.cursor, 100));
      query.set('limit', bounded(options.limit, 100, 20));
    } else {
      route += 'runs/' + uuid(options.run_id);
      if (['results', 'metrics'].includes(operation)) route += '/' + operation;
      else if (['trace-list', 'trace-detail', 'trace-events', 'trace-summary'].includes(operation))
        route += '/attempts/' + uuid(options.attempt_id) + '/trace/' + operation.slice(6);
      else if (operation !== 'detail') throw Error('无效评测读取操作');
      if (['results', 'trace-list', 'trace-events'].includes(operation)) {
        query.set('cursor', bounded(options.cursor, 100000));
        query.set('limit', bounded(options.limit, operation.startsWith('trace-') ? 250 : 100, 20));
      }
      if (operation.startsWith('trace-') && options.trace_id)
        query.set('trace_id', uuid(options.trace_id));
    }
    return request(route + '?' + query);
  });
  ipcMain.handle('evaluation-start', (event, options) => {
    requireMainWindow(event);
    if (
      !options ||
      typeof options !== 'object' ||
      Array.isArray(options) ||
      JSON.stringify(options).length > 4096
    )
      throw Error('无效评测参数');
    const allowed = [
      'dataset_id',
      'case_ids',
      'mode',
      'model_role',
      'repetitions',
      'concurrency',
      'timeout_seconds',
      'max_duration_seconds',
      'max_requests',
      'token_budget',
      'live_consent',
      'knowledge_mode',
      'visual_consent',
    ];
    if (Object.keys(options).some((key) => !allowed.includes(key)))
      throw Error('不支持外部命令或目录');
    if (
      options.dataset_id === 'computer-use-core' &&
      options.mode === 'windows_gui' &&
      !options.visual_consent
    )
      throw Error('VISUAL_QA_CONSENT_REQUIRED');
    return request('/api/evaluation/runs', options);
  });
  ipcMain.handle('evaluation-control', (event, runId, action) => {
    requireMainWindow(event);
    if (!['pause', 'resume', 'cancel'].includes(action)) throw Error('无效评测控制');
    return request('/api/evaluation/runs/' + uuid(runId) + '/control', { action });
  });
  ipcMain.handle('evaluation-delete', (event, runId) => {
    requireMainWindow(event);
    return request('/api/evaluation/runs/' + uuid(runId), {}, 'DELETE');
  });
  ipcMain.handle('evaluation-export', async (event, runId, format) => {
    requireMainWindow(event);
    const id = uuid(runId);
    if (!['json', 'csv', 'markdown'].includes(format) || exporting || !exportDirectory)
      throw Error('无效导出或已有导出进行中');
    exporting = true;
    try {
      const report = await request('/api/evaluation/runs/' + id + '/export?format=' + format);
      if (typeof report.content !== 'string' || report.content.length > 3000000)
        throw Error('报告超过导出限制');
      const filename =
        'evaluation-' + id + '-' + randomUUID() + '.' + (format === 'markdown' ? 'md' : format);
      await fs.mkdir(exportDirectory, { recursive: true });
      await fs.writeFile(path.join(exportDirectory, filename), report.content, {
        encoding: 'utf8',
        flag: 'wx',
      });
      return { filename, partial: !!report.partial };
    } finally {
      exporting = false;
    }
  });
  ipcMain.handle('evaluation-watch', (event, runId) => {
    requireMainWindow(event);
    const sender = event.sender;
    stop(sender);
    if (runId === null) return true;
    const id = uuid(runId);
    const abort = new AbortController();
    subscriptions.set(sender.id, abort);
    if (!watched.has(sender)) {
      watched.add(sender);
      sender.once('destroyed', () => stop(sender));
    }
    // The control token stays here. The renderer receives only bounded typed
    // invalidations, never an arbitrary streamed response or generic HTTP proxy.
    void (async () => {
      let reader;
      try {
        const response = await fetch(backendURL + '/api/evaluation/runs/' + id + '/events', {
          headers: { 'X-Companion-Control-Token': controlToken },
          signal: abort.signal,
        });
        if (!response.ok || !response.body) throw Error('progress unavailable');
        reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          if (buffer.length > 16384) throw Error('progress overflow');
          let boundary;
          while ((boundary = buffer.indexOf('\n\n')) >= 0) {
            const block = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const name = block.match(/^event: (EVAL_[A-Z_]+)$/m)?.[1];
            const line = block.match(/^data: (.+)$/m)?.[1];
            if (!name || !line || sender.isDestroyed() || abort.signal.aborted) continue;
            const data = JSON.parse(line);
            if (data.run_id !== id) continue;
            sender.send('evaluation-progress', {
              event: name,
              run_id: id,
              case_id: /^EVAL-\d{3}$/.test(data.case_id || '') ? data.case_id : null,
            });
          }
        }
      } catch {
        if (!abort.signal.aborted && !sender.isDestroyed())
          sender.send('evaluation-progress', {
            event: 'EVAL_PROGRESS_RECONNECT',
            run_id: id,
            case_id: null,
          });
      } finally {
        await reader?.cancel().catch(() => {});
        if (subscriptions.get(sender.id) === abort) subscriptions.delete(sender.id);
      }
    })();
    return true;
  });
  return () => {
    for (const abort of subscriptions.values()) abort.abort();
    subscriptions.clear();
  };
};
