'use strict';
const UUID = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;
/** Fixed main-window routes; no raw audio, URL, command or key is ever logged. */
module.exports = ({ ipcMain, requireMainWindow, request, getWindow, rendererSessions, dialog }) => {
  let active = null,
    microphoneAllowed = false,
    epoch = 0,
    starting = false;
  const owner = (event) => {
    requireMainWindow(event);
    return rendererSessions.context(event.sender).renderer_id;
  };
  const scope = (event, body) => {
    const owner_id = owner(event);
    if (
      !active ||
      active.owner_id !== owner_id ||
      body?.voice_session_id !== active.voice_session_id
    )
      throw Error('语音会话已失效');
    const result = { owner_id, voice_session_id: active.voice_session_id };
    for (const key of ['turn_id', 'generation_id']) {
      if (body[key] != null && !UUID.test(body[key])) throw Error('语音轮次标识无效');
      if (body[key] != null) result[key] = body[key];
    }
    return result;
  };
  async function revoke() {
    epoch++;
    microphoneAllowed = false;
    const old = active;
    active = null;
    const win = getWindow();
    if (win && !win.isDestroyed()) win.webContents.send('realtime-voice-revoked');
    if (old) await request('/api/realtime-voice/stop', old).catch(() => {});
  }
  ipcMain.handle('realtime-voice-capabilities', (event) => {
    owner(event);
    return request('/api/realtime-voice/capabilities');
  });
  ipcMain.handle('realtime-voice-traces', (event, operation, options) => {
    owner(event);
    if (
      !['list', 'detail', 'events', 'summary'].includes(operation) ||
      !options ||
      !UUID.test(options.conversation_id)
    )
      throw Error('语音追踪查询无效');
    const body = { operation, conversation_id: options.conversation_id };
    for (const key of [
      'trace_id',
      'task_id',
      'status',
      'cursor',
      'after',
      'limit',
      'since',
      'until',
      'tool_name',
    ])
      if (options[key] !== undefined && options[key] !== '') body[key] = options[key];
    return request('/api/realtime-voice/trace/read', body);
  });
  ipcMain.handle('realtime-voice-start', async (event, body) => {
    const owner_id = owner(event),
      win = getWindow();
    if (starting || active) throw Error('请先停止当前语音会话');
    if (
      !body ||
      !UUID.test(body.conversation_id) ||
      typeof body.character_id !== 'string' ||
      body.character_id.length > 100 ||
      !['zh-CN', 'en-US'].includes(body.language) ||
      !['windows-sapi', 'configured'].includes(body.tts_provider)
    )
      throw Error('语音配置无效');
    starting = true;
    const token = ++epoch;
    try {
      const answer = await dialog.showMessageBox(win, {
        type: 'question',
        title: '启用本次语音会话',
        message: '允许按下录音按钮时使用麦克风？',
        detail:
          '识别使用本机 Windows 引擎。原始录音不落盘、不上传；终句需要校对并发送。关闭或隐藏窗口会停止采集。' +
          (body.tts_provider === 'configured' && body.remote_tts_consent === true
            ? '\n已选择允许将回复文字发送给当前配置的云端语音引擎；不会发送麦克风音频。'
            : ''),
        buttons: ['允许本次', '取消'],
        defaultId: 1,
        cancelId: 1,
      });
      if (answer.response !== 0 || token !== epoch || win.isDestroyed() || !win.isVisible())
        throw Error('未启用麦克风');
      const result = await request('/api/realtime-voice/start', {
        owner_id,
        conversation_id: body.conversation_id,
        character_id: body.character_id,
        language: body.language,
        tts_provider: body.tts_provider,
        voice_id: typeof body.voice_id === 'string' ? body.voice_id.slice(0, 160) : '',
        speaking_rate: body.speaking_rate ?? 1,
        remote_tts_consent: body.remote_tts_consent === true,
      });
      if (token !== epoch || win.isDestroyed() || !win.isVisible()) {
        await request('/api/realtime-voice/stop', {
          owner_id,
          voice_session_id: result.voice_session_id,
        });
        throw Error('语音会话已停止');
      }
      active = { owner_id, voice_session_id: result.voice_session_id };
      microphoneAllowed = true;
      return result;
    } finally {
      starting = false;
    }
  });
  for (const action of ['begin', 'frame', 'finalize', 'interrupt', 'event', 'stop'])
    ipcMain.handle('realtime-voice-' + action, async (event, body) => {
      if (action === 'stop' && !active) {
        owner(event);
        return { state: 'STOPPED' };
      }
      const identity = scope(event, body);
      let payload = identity;
      if (action === 'frame') {
        if (typeof body.pcm_base64 !== 'string' || body.pcm_base64.length > 26000)
          throw Error('音频帧超限');
        payload = {
          ...identity,
          pcm_base64: body.pcm_base64,
          sequence: body.sequence,
          sample_rate: body.sample_rate,
          channels: body.channels,
          sample_format: body.sample_format,
          frame_duration_ms: body.frame_duration_ms,
          timestamp: body.timestamp,
        };
      } else if (action === 'event') payload = { ...identity, event: body.event };
      if (action === 'stop') microphoneAllowed = false;
      const value = await request('/api/realtime-voice/' + action, payload);
      if (action === 'stop') active = null;
      return value;
    });
  function allowed(contents, permission, origin, details, requesting = false) {
    const win = getWindow();
    if (
      !microphoneAllowed ||
      !active ||
      !win ||
      win.isDestroyed() ||
      !win.isVisible() ||
      contents !== win.webContents ||
      permission !== 'media'
    )
      return false;
    let sameOrigin = false;
    try {
      sameOrigin = new URL(origin).origin === new URL(contents.getURL()).origin;
    } catch {
      return false;
    }
    if (!sameOrigin || details?.isMainFrame === false) return false;
    return requesting
      ? details?.mediaTypes?.length === 1 && details.mediaTypes[0] === 'audio'
      : details?.mediaType === 'audio';
  }
  return { revoke, allowed };
};
