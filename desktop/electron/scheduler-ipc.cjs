'use strict';

const uuid = (value) => {
  if (typeof value !== 'string' || !/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i.test(value))
    throw Error('定时任务编号无效');
  return value;
};
function object(value, keys) {
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    JSON.stringify(value).length > 90000 ||
    Object.keys(value).some((key) => !keys.includes(key))
  )
    throw Error('定时任务参数无效');
}

module.exports = ({ ipcMain, requireMainWindow, request, dialog, background }) => {
  const scope = (options) =>
    new URLSearchParams({ conversation_id: uuid(options.conversation_id) });
  async function confirm(event, message, detail) {
    requireMainWindow(event);
    const answer = await dialog.showMessageBox({
      type: 'warning',
      title: '定时任务确认',
      message,
      detail,
      buttons: ['取消', '确认'],
      defaultId: 0,
      cancelId: 0,
      noLink: true,
    });
    requireMainWindow(event);
    if (answer.response !== 1) throw Error('已取消');
  }
  ipcMain.handle('scheduler-read', (event, operation, options = {}) => {
    requireMainWindow(event);
    object(options, ['conversation_id', 'schedule_id', 'occurrence_id']);
    if (operation === 'status' || operation === 'owners' || operation === 'recent')
      return request('/api/scheduler/' + operation);
    if (['schedules', 'sources', 'notifications'].includes(operation))
      return request('/api/scheduler/' + operation + '?' + scope(options));
    if (operation === 'history')
      return request(
        '/api/scheduler/schedules/' + uuid(options.schedule_id) + '/history?' + scope(options),
      );
    if (operation === 'recovery-preview')
      return request(
        '/api/scheduler/occurrences/' +
          uuid(options.occurrence_id) +
          '/recovery-preview?' +
          scope(options),
      );
    throw Error('定时任务读取操作无效');
  });
  ipcMain.handle('scheduler-control', async (event, operation, options = {}) => {
    requireMainWindow(event);
    object(options, [
      'conversation_id',
      'schedule_id',
      'occurrence_id',
      'definition',
      'revision',
      'state',
      'paused',
      'kind',
      'workflow_run_id',
      'source_id',
    ]);
    if (operation === 'save' || operation === 'preview')
      return request('/api/scheduler/' + (operation === 'save' ? 'schedules' : 'preview'), {
        definition: options.definition,
        revision: options.revision || 0,
      });
    if (operation === 'pause-all') {
      if (typeof options.paused !== 'boolean') throw Error('暂停状态无效');
      return request('/api/scheduler/pause', { paused: options.paused });
    }
    const owner = uuid(options.conversation_id);
    if (operation === 'source') {
      await confirm(
        event,
        '允许监控这个受控来源？',
        '仅监控当前会话选择的项目元数据或所选工作流状态；授权有效 24 小时。文件正文不用于更改任务。',
      );
      return request('/api/scheduler/sources', {
        conversation_id: owner,
        kind: options.kind,
        workflow_run_id: options.workflow_run_id || null,
      });
    }
    if (operation === 'revoke-source')
      return request('/api/scheduler/sources/' + uuid(options.source_id) + '/revoke', {
        conversation_id: owner,
      });
    if (operation === 'state' || operation === 'run-now') {
      if (options.state === 'ARCHIVED')
        await confirm(
          event,
          '删除这个定时任务？',
          '停止未来触发，保留执行历史；在途操作不能被删除撤销。',
        );
      return request(
        '/api/scheduler/schedules/' +
          uuid(options.schedule_id) +
          '/' +
          (operation === 'state' ? 'state' : 'run-now'),
        operation === 'state'
          ? { conversation_id: owner, revision: options.revision, state: options.state }
          : { conversation_id: owner },
      );
    }
    if (['approve', 'cancel', 'resume'].includes(operation)) {
      let binding;
      if (operation !== 'cancel') {
        const overview = await request(
          '/api/scheduler/occurrences/' +
            uuid(options.occurrence_id) +
            '/approval-overview?' +
            scope(options),
        );
        if (
          overview.occurrence_id !== options.occurrence_id ||
          !/^[a-f0-9]{64}$/.test(overview.binding)
        )
          throw Error('授权目标已变化');
        binding = overview.binding;
        await confirm(
          event,
          operation === 'resume' ? '恢复这一次已核验运行？' : '允许启动这一次固定工作流？',
          `任务：${String(overview.name).slice(0, 80)}\n本次：${overview.occurrence_id}\n计划时间：${overview.scheduled_for}\n固定工作流版本：${overview.workflow_version_id}\n模型节点：${overview.may_use_models ? '包含，可能外传输入并计费' : '未包含'}\n输入字段：${overview.input_fields}\n\n确认有效 5 分钟，不授权未来运行。工具仍按实际参数逐项审批；未知副作用不可重放。`,
        );
      }
      return request(
        '/api/scheduler/occurrences/' + uuid(options.occurrence_id) + '/' + operation,
        {
          conversation_id: owner,
          ...(binding ? { binding } : {}),
          ...(operation === 'resume' ? { revision: options.revision } : {}),
        },
      );
    }
    throw Error('定时任务控制操作无效');
  });
  ipcMain.handle('scheduler-background', async (event, options) => {
    requireMainWindow(event);
    if (options === undefined) return background().state();
    object(options, ['enabled', 'hide']);
    if (options.hide === true) return background().hide();
    if (typeof options.enabled !== 'boolean') throw Error('后台模式参数无效');
    if (options.enabled)
      await confirm(
        event,
        '关闭窗口后保持托盘运行？',
        '启用后，关闭按钮只隐藏窗口，定时任务仍可运行。请从托盘选择“完全退出”停止；关机、睡眠或完全退出期间不能按时运行。不会添加开机启动项。',
      );
    return background().configure(options.enabled);
  });
  ipcMain.handle('scheduler-navigation', (event) => {
    requireMainWindow(event);
    return background().takeNavigation();
  });
};
