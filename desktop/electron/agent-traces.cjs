'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

// Fixed history routes only. No token, generic HTTP proxy or execution channel
// is exposed to the renderer. Backend independently checks conversation ownership.
const uuid = (value) => {
  if (typeof value !== 'string' || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value))
    throw Error('无效的会话或轨迹编号');
  return value;
};
const bounded = (value, max, fallback) => {
  if (value === undefined) return fallback;
  if (!Number.isInteger(value) || value < 0 || value > max) throw Error('无效的分页参数');
  return value;
};
module.exports = ({ ipcMain, requireMainWindow, request, exportDirectory }) => {
  let exporting = false;
  ipcMain.handle('agent-trace-export', async (event, options = {}) => {
    requireMainWindow(event);
    const id = uuid(options.trace_id);
    const query = new URLSearchParams({ conversation_id: uuid(options.conversation_id) });
    if (options.work_session_id) query.set('work_session_id', uuid(options.work_session_id));
    if (exporting || !exportDirectory) throw Error('导出暂不可用或正在进行');
    exporting = true;
    try {
      const route = '/api/agent/traces/' + id;
      // Read DTOs are allowlisted again by the backend. Never accept a path or
      // arbitrary document body from a renderer, nor relax navigation guards.
      const trace = await request(route + '?' + query);
      const summary = await request(route + '/summary?' + query);
      const events = [];
      let after = 0;
      for (let page = 0; page < 20; page++) {
        const result = await request(route + '/events?' + query + '&limit=250&after=' + after);
        events.push(...result.items);
        if (!result.has_more || result.next_cursor == null || result.next_cursor <= after) break;
        after = result.next_cursor;
      }
      const partial =
        !!trace.incomplete ||
        !['completed', 'failed', 'cancelled', 'interrupted'].includes(trace.status) ||
        trace.event_count !== events.length;
      const filename = 'agent-trace-' + id + '-' + randomUUID() + '.json';
      await fs.mkdir(exportDirectory, { recursive: true });
      await fs.writeFile(
        path.join(exportDirectory, filename),
        JSON.stringify({ schema_version: 1, trace, summary, events, partial }, null, 2),
        { encoding: 'utf8', flag: 'wx' },
      );
      return { filename, event_count: events.length, partial };
    } finally {
      exporting = false;
    }
  });
  ipcMain.handle('agent-traces-read', (event, operation, options = {}) => {
    requireMainWindow(event);
    if (!options || typeof options !== 'object' || Array.isArray(options)) throw Error('无效查询');
    const query = new URLSearchParams({ conversation_id: uuid(options.conversation_id) });
    if (options.work_session_id) query.set('work_session_id', uuid(options.work_session_id));
    let route = '/api/agent/traces';
    if (operation === 'list') {
      if (options.task_id) query.set('task_id', uuid(options.task_id));
      if (options.status) {
        if (
          ![
            'planning',
            'ready',
            'queued',
            'running',
            'paused',
            'waiting_permission',
            'completed',
            'failed',
            'cancelled',
            'interrupted',
          ].includes(options.status)
        )
          throw Error('无效状态');
        query.set('status', options.status);
      }
      if (options.tool_name) {
        if (
          typeof options.tool_name !== 'string' ||
          !/^[a-z][a-z0-9_.]{0,79}$/.test(options.tool_name)
        )
          throw Error('无效工具名称');
        query.set('tool_name', options.tool_name);
      }
      for (const key of ['since', 'until'])
        if (options[key]) {
          if (
            typeof options[key] !== 'string' ||
            options[key].length > 40 ||
            !Number.isFinite(Date.parse(options[key]))
          )
            throw Error('无效日期');
          query.set(key, new Date(options[key]).toISOString());
        }
      query.set('cursor', bounded(options.cursor, 100000, 0));
      const limit = bounded(options.limit, 100, 30);
      if (!limit) throw Error('无效分页大小');
      query.set('limit', limit);
    } else if (['detail', 'events', 'summary'].includes(operation)) {
      route += '/' + uuid(options.trace_id);
      if (operation !== 'detail') route += '/' + operation;
      if (operation === 'events') {
        query.set('after', bounded(options.after, 100000, 0));
        const limit = bounded(options.limit, 250, 250);
        if (!limit) throw Error('无效分页大小');
        query.set('limit', limit);
      }
    } else throw Error('只允许读取执行轨迹');
    return request(route + '?' + query.toString());
  });
  ipcMain.handle('agent-trace-delete', (event, options = {}) => {
    requireMainWindow(event);
    const query = new URLSearchParams({ conversation_id: uuid(options.conversation_id) });
    if (options.work_session_id) query.set('work_session_id', uuid(options.work_session_id));
    return request('/api/agent/traces/' + uuid(options.trace_id) + '?' + query, {}, 'DELETE');
  });
};
