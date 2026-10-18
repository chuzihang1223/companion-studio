'use strict';
const { owner, object, OPERATIONS } = require('./computer-use.cjs');
const { randomUUID } = require('node:crypto');
module.exports = ({ ipcMain, requireMainWindow, request, dialog, getComputer, getQA }) => {
  const computer = () => {
    const value = getComputer();
    if (!value) throw Error('COMPUTER_UNAVAILABLE');
    value.onRevoke = (grant) => {
      if (grant.vision_task_id)
        void request(
          '/api/computer/vision/cancel',
          { ...grant.scope, task_id: grant.vision_task_id },
          'POST',
          5000,
        ).catch(() => {});
    };
    return value;
  };
  const confirm = async (event, message, detail) => {
    requireMainWindow(event);
    const result = await dialog.showMessageBox({
      type: 'warning',
      title: '视觉操作授权',
      message,
      detail,
      buttons: ['取消', '允许这次操作'],
      defaultId: 0,
      cancelId: 0,
      noLink: true,
    });
    requireMainWindow(event);
    if (result.response !== 1) throw Error('COMPUTER_PERMISSION_DENIED');
  };
  ipcMain.handle('computer-qa-consent', async (event) => {
    requireMainWindow(event);
    if (process.env.COMPANION_VISUAL_QA_PROFILE !== '1')
      throw Error('VISUAL_QA_ISOLATED_PROFILE_REQUIRED');
    await confirm(
      event,
      '运行项目自有 GUI 测试窗口？',
      '仅在隔离审核档案中编译并操作本项目原创测试窗口，不访问私人软件或外部模型。会自动输入、点击、改变测试窗口焦点，约需几分钟。无需改变 DPI 或系统安全设置。',
    );
    return getQA().authorize();
  });
  ipcMain.handle('computer-qa-fixture', async (event, options) => {
    requireMainWindow(event);
    object(options, ['conversation_id', 'character_id', 'consent_id', 'fixture_id', 'operation']);
    if (process.env.COMPANION_VISUAL_QA_PROFILE !== '1')
      throw Error('VISUAL_QA_ISOLATED_PROFILE_REQUIRED');
    owner(options);
    return request('/api/computer/qa/fixture', options, 'POST', 45000);
  });
  ipcMain.handle('computer-read', async (event, operation, options) => {
    requireMainWindow(event);
    object(options, ['conversation_id', 'character_id', 'app']);
    const scope = owner(options);
    await request('/api/computer/owner', scope);
    if (operation === 'windows') {
      if (typeof options.app !== 'string' || options.app.length > 100)
        throw Error('COMPUTER_ARGUMENT_INVALID');
      return computer().windows(scope, options.app);
    }
    if (operation === 'state') return computer().state(scope);
    throw Error('COMPUTER_OPERATION_UNSUPPORTED');
  });
  ipcMain.handle('computer-control', async (event, operation, options) => {
    requireMainWindow(event);
    object(options, [
      'conversation_id',
      'character_id',
      'choice_id',
      'target_id',
      'preview',
      'coordinate_fallback',
      'action',
    ]);
    const scope = owner(options);
    await request('/api/computer/owner', scope);
    if (operation === 'enable') {
      const choice = computer().choice(scope, options.choice_id);
      await confirm(
        event,
        '开启所选窗口的视觉操作？',
        `目标：${choice.window.title}\n仅在本会话授权此窗口，15 分钟后失效。可读取 UIA；勾选预览后可在内存截图与本地 OCR，约 8 秒后清除。默认不保存、不上传云端。\n${options.coordinate_fallback ? '已选择受限 OCR 坐标点击，仍需唯一候选与执行验证。' : '输入优先使用语义控件，不启用 OCR 坐标点击。'}\n可随时暂停、停止或接管。`,
      );
      return computer().enable(scope, options.choice_id, options);
    }
    if (operation === 'control')
      return computer().control(scope, options.target_id, options.action);
    throw Error('COMPUTER_OPERATION_UNSUPPORTED');
  });
  ipcMain.handle('computer-action', async (event, options) => {
    requireMainWindow(event);
    object(options, ['conversation_id', 'character_id', 'operation', 'arguments']);
    const scope = owner(options);
    if (!OPERATIONS.has(options.operation)) throw Error('COMPUTER_OPERATION_UNSUPPORTED');
    object(options.arguments, [
      'target_id',
      'observation_id',
      'candidate_id',
      'locator',
      'text',
      'direction',
      'verification',
      'timeout_seconds',
      'capture_scope',
      'region',
      'display_id',
    ]);
    const grant = computer().get(scope, options.arguments.target_id),
      generation = grant.nonce;
    const isInput = ['click', 'type_text', 'scroll'].includes(options.operation);
    const previous = isInput ? grant.frames.get(options.arguments.observation_id) : null;
    const selected = previous?.candidates?.find(
      (row) => row.candidate_id === options.arguments.candidate_id,
    );
    if (
      isInput &&
      (!previous ||
        !selected ||
        previous.candidates.length !== 1 ||
        !previous.locator ||
        Date.now() - previous.created_ms > 8000)
    )
      throw Error('COMPUTER_OBSERVATION_EXPIRED');
    if (['click', 'type_text', 'scroll', 'focus_window'].includes(options.operation)) {
      await confirm(
        event,
        '执行所选窗口操作？',
        `目标：${grant.window.title}\n操作：${options.operation}\n${options.operation === 'type_text' ? '输入：' + String(options.arguments.text || '').slice(0, 500) : '将重新检查元素、窗口与焦点，并验证操作结果。'}\n允许后将聚焦这个窗口并重新定位同一控件；发生变化时停止。结果未知时不会自动重复操作。`,
      );
    }
    requireMainWindow(event);
    if (computer().get(scope, grant.id) !== grant || grant.nonce !== generation)
      throw Error('COMPUTER_PAUSED');
    return request(
      '/api/computer/execute',
      { ...scope, operation: options.operation, arguments: options.arguments },
      'POST',
      45000,
    );
  });
  ipcMain.handle('computer-vision', async (event, options) => {
    requireMainWindow(event);
    object(options, ['conversation_id', 'character_id', 'target_id']);
    const scope = owner(options),
      grant = computer().get(scope, options.target_id);
    if (!grant.preview) throw Error('COMPUTER_CAPTURE_PERMISSION_REQUIRED');
    const model = await request('/api/computer/vision/target', scope);
    if (!model.configured_vision) throw Error('VISION_MODEL_UNAVAILABLE');
    await confirm(
      event,
      '将所选窗口截图发送给视觉模型？',
      `模型：${model.model}\n窗口：${grant.window.title}\n将重新采集已授权窗口的脱敏图片，仅发送一次；服务方会处理图像，可能产生 API 费用。其他窗口不会上传。结果仅为建议，不执行动作。首次有效响应才确认视觉兼容性。`,
    );
    await request(
      '/api/computer/execute',
      { ...scope, operation: 'observe', arguments: { target_id: grant.id } },
      'POST',
      45000,
    );
    requireMainWindow(event);
    const current = computer().get(scope, grant.id),
      frame = current.latest;
    if (
      current !== grant ||
      !frame?.image ||
      !frame.privacy_checked ||
      Date.now() - frame.created_ms > 8000
    )
      throw Error('COMPUTER_OBSERVATION_EXPIRED');
    const taskId = randomUUID();
    if (current.vision_task_id) throw Error('COMPUTER_CAPACITY_LIMIT');
    current.vision_task_id = taskId;
    try {
      return await request(
        '/api/computer/vision/analyze',
        {
          ...scope,
          task_id: taskId,
          window_id: current.id,
          observation_id: frame.observation_id,
          target_fingerprint: model.target_fingerprint,
          image: frame.image.data_uri,
        },
        'POST',
        95000,
      );
    } finally {
      if (current.vision_task_id === taskId) delete current.vision_task_id;
    }
  });
};
