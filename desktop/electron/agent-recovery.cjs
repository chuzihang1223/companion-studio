'use strict';

const uuid = (value) => {
  if (typeof value !== 'string' || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value))
    throw Error('无效恢复编号');
  return value;
};
const object = (value, allowed) => {
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    JSON.stringify(value).length > 4096 ||
    Object.keys(value).some((key) => !allowed.includes(key))
  )
    throw Error('无效恢复参数');
};
module.exports = ({ ipcMain, requireMainWindow, request }) => {
  ipcMain.handle('agent-recovery-read', (event, operation, options) => {
    requireMainWindow(event);
    object(options, ['task_id', 'conversation_id', 'work_session_id']);
    const query = new URLSearchParams({ conversation_id: uuid(options.conversation_id) });
    if (options.work_session_id) query.set('work_session_id', uuid(options.work_session_id));
    let route = '/api/agent/recovery/tasks';
    if (operation !== 'list') {
      if (!['detail', 'checkpoints', 'preview'].includes(operation))
        throw Error('无效恢复读取操作');
      route += '/' + uuid(options.task_id);
      if (operation !== 'detail') route += '/' + operation;
    }
    return request(route + '?' + query);
  });
  ipcMain.handle('agent-recovery-control', (event, taskId, action, options) => {
    requireMainWindow(event);
    if (
      !['resume', 'cancel', 'revalidate', 'restart', 'pause', 'continue', 'takeover'].includes(
        action,
      )
    )
      throw Error('无效恢复操作');
    object(options, [
      'conversation_id',
      'work_session_id',
      'request_id',
      'revision',
      'approved_ids',
      'confirm_model_requests',
    ]);
    const approved = options.approved_ids || [];
    if (
      !Array.isArray(approved) ||
      approved.length > 5 ||
      approved.some((id) => typeof id !== 'string' || !/^[A-Za-z0-9_.:-]{1,100}$/.test(id)) ||
      !Number.isInteger(options.revision) ||
      options.revision < 1 ||
      options.revision > 1000000 ||
      (options.confirm_model_requests !== undefined &&
        typeof options.confirm_model_requests !== 'boolean')
    )
      throw Error('无效的恢复授权或版本');
    return request('/api/agent/recovery/tasks/' + uuid(taskId) + '/' + action, {
      conversation_id: uuid(options.conversation_id),
      work_session_id: uuid(options.work_session_id),
      request_id: uuid(options.request_id),
      revision: options.revision,
      approved_ids: approved,
      renderer_id: 'renderer-' + event.sender.id,
      confirm_model_requests: options.confirm_model_requests === true,
    });
  });
};
