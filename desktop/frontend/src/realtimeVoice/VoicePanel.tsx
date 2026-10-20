import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { VoiceController } from './VoiceController';
import type { VoiceBinding, VoiceCapabilities, VoiceView } from './types';
import './voice.css';
const TraceViewer = lazy(() => import('../trace/TraceViewer'));
const EvaluationDashboard = lazy(() => import('../evaluation/EvaluationDashboard'));

type Props = {
  conversationId: string | null;
  characterId: string;
  ensureConversation: () => Promise<string>;
  send: (text: string, binding: VoiceBinding, current: () => boolean) => Promise<void>;
  interrupt: (conversationId?: string) => Promise<void>;
  output: (id: string) => void;
  controller: (value: VoiceController | null) => void;
  close: () => void;
};
const labels: Record<string, string> = {
  IDLE: '就绪 · 麦克风关闭',
  INITIALIZING: '正在打开设备',
  LISTENING: '正在采集',
  SPEECH_DETECTED: '检测到声音',
  TRANSCRIBING: '终句后本地识别中',
  PAUSED: '已停止采集 · 等待校对',
  PROCESSING: '正在处理',
  SPEAKING: '正在播放',
  INTERRUPTING: '正在停止',
  ERROR: '需要重试',
  STOPPED: '已停止',
};
export default function VoicePanel(props: Props) {
  const latest = useRef(props);
  latest.current = props;
  const [view, setView] = useState<VoiceView>({
    identity: null,
    capturing: false,
    busy: false,
    transcript: '',
    error: '',
    duration: 0,
  });
  const [controller] = useState(
    () =>
      new VoiceController(
        setView,
        (id) => latest.current.interrupt(id),
        (text, binding, current) => latest.current.send(text, binding, current),
      ),
  );
  const [capabilities, setCapabilities] = useState<VoiceCapabilities | null>(null);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [input, setInput] = useState('');
  const [output, setOutput] = useState('');
  const selectedDevices = useRef({ input, output });
  selectedDevices.current = { input, output };
  const [language, setLanguage] = useState<'zh-CN' | 'en-US'>('zh-CN');
  const [provider, setProvider] = useState<'windows-sapi' | 'configured'>('windows-sapi');
  const [voice, setVoice] = useState('');
  const [remote, setRemote] = useState(false);
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [mode, setMode] = useState('click');
  const [showTrace, setShowTrace] = useState(false);
  const [showEvaluation, setShowEvaluation] = useState(false);
  const traceSource = useMemo(
    () => ({
      readAgentTraces: window.desktopAPI!.readVoiceTraces,
      exportAgentTrace: () => Promise.reject(Error('此处仅支持只读回放')),
      deleteAgentTrace: () => Promise.reject(Error('此处仅支持只读回放')),
    }),
    [],
  );
  useEffect(() => setText(view.transcript), [view.transcript]);
  useEffect(() => {
    let live = true;
    latest.current.controller(controller);
    const stop = window.desktopAPI?.onRealtimeVoiceRevoked(() => void controller.stop());
    const refresh = () =>
      void navigator.mediaDevices
        .enumerateDevices()
        .then((rows) => {
          if (!live) return;
          setDevices(rows);
          const selected = selectedDevices.current;
          if (
            controller.view.identity &&
            ((selected.input &&
              !rows.some((d) => d.kind === 'audioinput' && d.deviceId === selected.input)) ||
              (selected.output &&
                !rows.some((d) => d.kind === 'audiooutput' && d.deviceId === selected.output)))
          ) {
            void controller.stop();
            setError('所选音频设备已断开，语音会话已停止。请重新选择设备。');
          }
        })
        .catch(() => {
          if (live) setError('无法查询音频设备');
        });
    const hidden = () => {
      if (document.hidden) void controller.stop();
    };
    refresh();
    void window.desktopAPI
      ?.realtimeVoiceCapabilities()
      .then((result) => {
        if (live) setCapabilities(result);
      })
      .catch(() => {
        if (live) setError('本地语音能力查询失败');
      });
    navigator.mediaDevices.addEventListener('devicechange', refresh);
    document.addEventListener('visibilitychange', hidden);
    return () => {
      live = false;
      stop?.();
      latest.current.controller(null);
      void controller.stop();
      navigator.mediaDevices.removeEventListener('devicechange', refresh);
      document.removeEventListener('visibilitychange', hidden);
    };
  }, [controller]);
  useEffect(() => {
    const identity = controller.view.identity;
    if (
      identity &&
      (identity.conversation_id !== props.conversationId ||
        identity.character_id !== props.characterId)
    )
      void controller.stop();
  }, [controller, props.conversationId, props.characterId]);
  async function start() {
    setError('');
    try {
      const conversation = await props.ensureConversation();
      props.output(output);
      await controller.start({
        conversation_id: conversation,
        character_id: props.characterId,
        language,
        tts_provider: provider,
        voice_id: voice,
        speaking_rate: 1,
        remote_tts_consent: remote,
      });
      setDevices(await navigator.mediaDevices.enumerateDevices());
    } catch (cause) {
      setError(String(cause));
    }
  }
  const locked = !!view.identity || view.busy;
  return (
    <section className="realtime-voice-panel" aria-label="实时语音会话">
      <header>
        <strong>语音会话</strong>
        <span role="status">
          {view.capturing ? '● 麦克风正在采集' : '麦克风关闭'} ·{' '}
          {labels[view.identity?.state || 'IDLE'] || view.identity?.state}
        </span>
        <button type="button" onClick={props.close} aria-label="关闭语音面板">
          ×
        </button>
      </header>
      <p>Windows 本地识别 · 终句后转写，无增量字幕。原始录音不保存、不上传。中英混读请先校对。</p>
      <div className="voice-options">
        <label>
          麦克风
          <select
            aria-label="语音输入设备"
            value={input}
            disabled={view.capturing || view.busy}
            onChange={(e) => setInput(e.target.value)}
          >
            <option value="">系统默认输入</option>
            {devices
              .filter((d) => d.kind === 'audioinput' && d.deviceId !== 'default')
              .map((d, n) => (
                <option key={d.deviceId || n} value={d.deviceId}>
                  {d.label || `麦克风 ${n + 1}`}
                </option>
              ))}
          </select>
        </label>
        <label>
          输出设备
          <select
            aria-label="语音输出设备"
            value={output}
            disabled={locked}
            onChange={(e) => setOutput(e.target.value)}
          >
            <option value="">系统默认输出</option>
            {devices
              .filter((d) => d.kind === 'audiooutput' && d.deviceId !== 'default')
              .map((d, n) => (
                <option key={d.deviceId || n} value={d.deviceId}>
                  {d.label || `输出 ${n + 1}`}
                </option>
              ))}
          </select>
        </label>
        <label>
          识别语言
          <select
            value={language}
            disabled={locked}
            onChange={(e) => setLanguage(e.target.value as typeof language)}
          >
            <option value="zh-CN">中文</option>
            <option value="en-US">English</option>
          </select>
        </label>
        <label>
          朗读引擎
          <select
            value={provider}
            disabled={locked}
            onChange={(e) => setProvider(e.target.value as typeof provider)}
          >
            <option value="windows-sapi">系统语音 · 本地分段</option>
            <option value="configured" disabled={!capabilities?.configured_tts.available}>
              已配置引擎 · {capabilities?.configured_tts.provider_id || '未配置'}
            </option>
          </select>
        </label>
        {provider === 'windows-sapi' && (
          <label>
            系统音色
            <select
              aria-label="本地系统音色"
              value={voice}
              disabled={locked}
              onChange={(e) => setVoice(e.target.value)}
            >
              <option value="">跟随识别语言</option>
              {capabilities?.local.voices
                .filter((v) => v.language === language)
                .map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.id}
                  </option>
                ))}
            </select>
          </label>
        )}
        <label>
          输入方式
          <select
            value={mode}
            disabled={view.capturing || view.busy}
            onChange={(e) => setMode(e.target.value)}
          >
            <option value="click">点击开始 / 结束</option>
            <option value="push">按住说话</option>
            <option value="hands" disabled>
              免提 · 尚未完成回声验收
            </option>
          </select>
        </label>
      </div>
      {provider === 'configured' && capabilities?.configured_tts.requires_network && (
        <label>
          <input
            type="checkbox"
            checked={remote}
            disabled={locked}
            onChange={(e) => setRemote(e.target.checked)}
          />
          允许将回复文字交给已配置的云端语音服务；不发送麦克风音频
        </label>
      )}
      <div className="voice-actions">
        {!view.identity ? (
          <button
            type="button"
            disabled={view.busy || capabilities?.local.status !== 'available'}
            onClick={() => void start()}
          >
            开启语音会话
          </button>
        ) : (
          <>
            {mode === 'push' ? (
              <button
                type="button"
                aria-label="按住说话"
                onPointerDown={(e) => {
                  e.currentTarget.setPointerCapture(e.pointerId);
                  void controller.record(input);
                }}
                onPointerUp={() => void controller.finish()}
                onPointerCancel={() => void controller.interrupt()}
                onBlur={() => {
                  if (controller.view.capturing || controller.view.busy)
                    void controller.interrupt();
                }}
                onKeyDown={(e) => {
                  if (e.code === 'Space' && !e.repeat) {
                    e.preventDefault();
                    void controller.record(input);
                  }
                }}
                onKeyUp={(e) => {
                  if (e.code === 'Space') {
                    e.preventDefault();
                    void controller.finish();
                  }
                }}
              >
                按住说话
              </button>
            ) : (
              <button
                type="button"
                disabled={view.busy}
                onClick={() =>
                  void (view.capturing ? controller.finish() : controller.record(input))
                }
              >
                {view.capturing ? '结束并识别' : '开始录音'}
              </button>
            )}
            <button type="button" onClick={() => void controller.interrupt()}>
              打断回复 / 静音麦克风
            </button>
            <button type="button" onClick={() => void controller.stop()}>
              停止语音会话
            </button>
          </>
        )}
        <span>{(view.duration / 1000).toFixed(1)} / 30 秒 · 当前聊天 / 工作模型沿用设置</span>
        <button type="button" onClick={() => setShowTrace(!showTrace)}>
          语音追踪与只读回放
        </button>
        <button type="button" onClick={() => setShowEvaluation(!showEvaluation)}>
          语音评测
        </button>
      </div>
      {view.transcript && (
        <div className="voice-transcript">
          <label>
            识别终句 · 可校对
            <textarea
              aria-label="识别终句"
              value={text}
              maxLength={4000}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          <button
            type="button"
            disabled={!text.trim() || view.busy}
            onClick={() => void controller.submit(text)}
          >
            发送识别终句
          </button>
          <small>发送后沿用当前会话、角色、记忆、工具权限；说“确认”不会批准系统弹窗。</small>
        </div>
      )}
      <p>
        自动语音打断暂未启用。播放中按住说话会先停止原回复。VAD 仅提示声音活动，不自动执行指令。
      </p>
      {(error || view.error) && <p role="alert">{error || view.error}</p>}
      {showTrace && (
        <Suspense fallback={<p>正在读取追踪…</p>}>
          <TraceViewer
            conversationId={props.conversationId}
            sessionId={null}
            revision={0}
            visible={showTrace}
            source={traceSource}
            readOnly
          />
        </Suspense>
      )}
      {showEvaluation && (
        <Suspense fallback={<p>正在打开评测…</p>}>
          <EvaluationDashboard visible initialDataset="voice-core" />
        </Suspense>
      )}
    </section>
  );
}
