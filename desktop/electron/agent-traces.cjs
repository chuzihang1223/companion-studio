'use strict';

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
module.exports = ({ ipcMain, requireMainWindow, request }) => {
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
