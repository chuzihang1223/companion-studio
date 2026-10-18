import { useEffect, useState } from 'react';
import type { Action, Candidate, Choice, Observation, Result, State } from './types';
import './computer.css';

const statusText: Record<string, string> = {
  SUCCESS: '已验证',
  FAILED: '未完成',
  UNKNOWN: '结果未知，请接管',
  NEEDS_REVIEW: '需要重新观察或检查',
};
export default function VisualObserver({
  conversationId,
  characterId,
  visible,
  onTrace,
}: {
  conversationId: string | null;
  characterId: string;
  visible: boolean;
  onTrace: () => void;
}) {
  const [state, setState] = useState<State | null>(null),
    [apps, setApps] = useState<Array<{ id: string; name: string; installed: boolean }>>([]);
  const [app, setApp] = useState(''),
    [choices, setChoices] = useState<Choice[]>([]),
    [choice, setChoice] = useState('');
  const [preview, setPreview] = useState(false),
    [coordinate, setCoordinate] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [result, setResult] = useState<Result | null>(null);
  const [description, setDescription] = useState(''),
    [automationId, setAutomationId] = useState(''),
    [text, setText] = useState('');
  const [verificationId, setVerificationId] = useState(''),
    [expected, setExpected] = useState('');
  const [candidates, setCandidates] = useState<Candidate[]>([]),
    [source, setSource] = useState('auto');
  const [visionResult, setVisionResult] = useState('');
  const target = state?.targets[0],
    frame = target?.observation;
  const owner = conversationId
    ? { conversation_id: conversationId, character_id: characterId }
    : null;
  const observedId = frame?.observation_id,
    observedAt = frame?.created_ms;
  useEffect(() => {
    if (!observedId || !observedAt) return;
    const timer = window.setTimeout(
      () => {
        setState((old) =>
          old
            ? {
                ...old,
                targets: old.targets.map((value) =>
                  value.observation?.observation_id === observedId
                    ? { ...value, observation: null }
                    : value,
                ),
              }
            : old,
        );
        setCandidates([]);
      },
      Math.max(0, observedAt + 8000 - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [observedId, observedAt]);
  useEffect(() => {
    if (!visible || !conversationId || !window.desktopAPI) return;
    let live = true;
    const scope = { conversation_id: conversationId, character_id: characterId };
    const refresh = () =>
      window
        .desktopAPI!.computerRead('state', scope)
        .then((value) => {
          if (live) setState(value as State);
        })
        .catch((e) => {
          if (live) setError(String(e));
        });
    void refresh();
    void window.desktopAPI
      .listKnownApps()
      .then((value) => {
        if (live) setApps(value.filter((row) => row.installed));
      })
      .catch((e) => {
        if (live) setError(String(e));
      });
    // Poll cached metadata only. Capture/OCR occur solely on explicit actions;
    // keeping this state inside the module avoids repainting the Workbench.
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [visible, conversationId, characterId]);
  async function run(callback: () => Promise<unknown>) {
    setBusy(true);
    setError('');
    try {
      await callback();
      if (owner) setState((await window.desktopAPI!.computerRead('state', owner)) as State);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function action(operation: Action['operation'], args: Record<string, unknown> = {}) {
    if (!owner || !target) return;
    await run(async () => {
      const value = await window.desktopAPI!.computerAction({
        ...owner,
        operation,
        arguments: { target_id: target.target_id, ...args },
      });
      setResult(value);
      setCandidates(value.candidates || []);
    });
  }
  const selected = candidates.length === 1 ? candidates[0] : null;
  const fresh =
    selected &&
    frame &&
    selected.observation_id === frame.observation_id &&
    Date.now() - frame.created_ms < 8000;
  const identity = fresh
    ? { observation_id: selected.observation_id, candidate_id: selected.candidate_id }
    : {};
  const verification = { kind: 'text_equals', automation_id: verificationId, expected };
  if (!window.desktopAPI) return <p>屏幕观察需要在 Windows 桌面程序中使用。</p>;
  if (!owner) return <p>请先创建工作会话，再为该会话选择受控窗口。</p>;
  return (
    <section className="visual-observer" aria-label="屏幕观察">
      <header>
        <h2>屏幕观察</h2>
        <button onClick={onTrace}>查看 Trace</button>
      </header>
      <p>
        默认关闭。仅访问你选择的窗口；截图在内存短暂保留，不保存到聊天记录，不发送云端模型。密码区域会遮罩。终端、安全设置、私人聊天及外部浏览器不支持操作。
      </p>
      <p>截图最多保留约 8 秒；授权 15 分钟后失效。暂停、停止、接管后丢弃旧定位。</p>
      <fieldset disabled={busy}>
        <legend>选择本会话的窗口</legend>
        <label>
          应用
          <select
            aria-label="视觉目标应用"
            value={app}
            onChange={(e) => {
              setApp(e.target.value);
              setChoices([]);
              setChoice('');
            }}
          >
            <option value="">请选择</option>
            {apps.map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </select>
        </label>
        <button
          disabled={!app}
          onClick={() =>
            void run(async () => {
              const rows = (await window.desktopAPI!.computerRead('windows', {
                ...owner,
                app,
              })) as Choice[];
              setChoices(rows);
              setChoice(rows[0]?.choice_id || '');
            })
          }
        >
          列出受控窗口
        </button>
        <label>
          窗口
          <select
            aria-label="视觉目标窗口"
            value={choice}
            onChange={(e) => setChoice(e.target.value)}
          >
            <option value="">请选择</option>
            {choices.map((row) => (
              <option key={row.choice_id} value={row.choice_id}>
                {row.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          <input type="checkbox" checked={preview} onChange={(e) => setPreview(e.target.checked)} />
          允许窗口预览与本地 OCR
        </label>
        <label>
          <input
            type="checkbox"
            checked={coordinate}
            onChange={(e) => setCoordinate(e.target.checked)}
          />
          允许唯一 OCR 候选的受限坐标点击
        </label>
        <button
          disabled={!choice}
          onClick={() =>
            void run(async () => {
              await window.desktopAPI!.computerControl('enable', {
                ...owner,
                choice_id: choice,
                preview,
                coordinate_fallback: coordinate,
              });
              setCandidates([]);
            })
          }
        >
          开启 Visual Computer Use
        </button>
      </fieldset>
      {target && (
        <>
          <h3>
            {target.title} · {target.status === 'enabled' ? '已启用' : '已暂停'}
          </h3>
          <p>
            窗口授权 ID：<code>{target.target_id}</code>（可用于工作流，不包含窗口句柄）
          </p>
          <div className="visual-actions">
            {(['pause', 'resume', 'stop', 'takeover'] as const).map((control, i) => (
              <button
                key={control}
                onClick={() =>
                  void run(async () => {
                    await window.desktopAPI!.computerControl('control', {
                      ...owner,
                      target_id: target.target_id,
                      action: control,
                    });
                    setCandidates([]);
                  })
                }
              >
                {['暂停', '继续', '停止', '接管'][i]}
              </button>
            ))}
          </div>
          <fieldset disabled={busy || target.status !== 'enabled'}>
            <legend>观察与定位</legend>
            <button onClick={() => void action('focus_window')}>聚焦所选窗口</button>
            <button onClick={() => void action('observe')}>重新观察</button>
            <label>
              目标说明
              <input
                aria-label="目标说明"
                value={description}
                maxLength={180}
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>
            <label>
              AutomationId
              <input
                aria-label="AutomationId"
                value={automationId}
                maxLength={120}
                onChange={(e) => setAutomationId(e.target.value)}
              />
            </label>
            <label>
              来源
              <select value={source} onChange={(e) => setSource(e.target.value)}>
                <option value="auto">UIA 优先，OCR 后备</option>
                <option value="uia">UIA</option>
                <option value="ocr">本地 OCR</option>
              </select>
            </label>
            <button
              disabled={!description}
              onClick={() =>
                void action('find_element', {
                  locator: {
                    description,
                    source,
                    matching: 'exact',
                    ...(automationId ? { automation_id: automationId } : {}),
                  },
                })
              }
            >
              定位元素
            </button>
          </fieldset>
          <fieldset disabled={busy || !fresh || target.status !== 'enabled'}>
            <legend>预计动作：仅使用刚定位的唯一候选</legend>
            <label>
              输入内容
              <input
                aria-label="视觉输入内容"
                value={text}
                maxLength={500}
                onChange={(e) => setText(e.target.value)}
              />
            </label>
            <button
              disabled={!text}
              onClick={() => void action('type_text', { ...identity, text })}
            >
              输入并验证
            </button>
            <label>
              验证控件 ID
              <input
                aria-label="验证控件 ID"
                value={verificationId}
                maxLength={120}
                onChange={(e) => setVerificationId(e.target.value)}
              />
            </label>
            <label>
              预期文字
              <input
                aria-label="预期文字"
                value={expected}
                maxLength={500}
                onChange={(e) => setExpected(e.target.value)}
              />
            </label>
            <button
              disabled={!verificationId || !expected}
              onClick={() => void action('click', { ...identity, verification })}
            >
              点击并验证
            </button>
            <button onClick={() => void action('scroll', { ...identity, direction: 'down' })}>
              向下滚动并验证
            </button>
          </fieldset>
          <p role="status">
            {busy
              ? '正在观察或验证…'
              : result
                ? (statusText[result.status] || result.status) +
                  (result.code ? ' · ' + result.code : '')
                : '尚未执行操作'}
            {result?.duration_ms !== undefined ? ` · ${result.duration_ms} ms` : ''}
          </p>
          {candidates.length > 0 && (
            <p>
              候选 {candidates.length} 个：
              {candidates.map((c) => (
                <span key={c.candidate_id}>
                  {c.source} · 匹配 {c.match_quality} ·{' '}
                  {c.confidence === null ? 'OCR 无置信分数' : '语义标识'}；
                </span>
              ))}
              {!fresh ? '定位已过期，请重新定位。' : ''}
            </p>
          )}
          {frame && <ObservationView frame={frame} candidates={candidates} />}
          <details>
            <summary>恢复记录</summary>
            <p>
              失效元素需重新定位；焦点、DPI 或显示器变化时停止。UNKNOWN / NEEDS_REVIEW
              不重复点击。重启后重新选择窗口；完整恢复记录见 Trace 与 Recovery。
            </p>
            <pre>{JSON.stringify(target.last_result, null, 2)}</pre>
          </details>
        </>
      )}
      <details>
        <summary>视觉模型与浏览器</summary>
        <button
          disabled={busy || !target?.preview_enabled}
          onClick={() =>
            void run(async () =>
              setVisionResult(
                JSON.stringify(
                  await window.desktopAPI!.computerVision({
                    ...owner,
                    target_id: target!.target_id,
                  }),
                  null,
                  2,
                ),
              ),
            )
          }
        >
          使用已配置视觉模型分析一次（另行确认）
        </button>
        {visionResult && <pre>{visionResult}</pre>}
        <p>
          此界面使用免费本地 UIA / Windows
          OCR。云端视觉默认关闭，需另外确认具体模型与截图范围；模型名称不能证明视觉能力。内置浏览器继续使用原网页模块的
          DOM / Accessibility 工具，不控制外部浏览器。显示器截图仅含所选窗口，其他区域保持黑色。
        </p>
      </details>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
function ObservationView({ frame, candidates }: { frame: Observation; candidates: Candidate[] }) {
  const image = frame.image;
  return (
    <>
      <p>
        UIA {frame.elements.length} 项 · OCR {frame.ocr?.lines.length || 0} 项 · 窗口 DPI{' '}
        {frame.geometry.window_dpi} · 显示器 DPI {frame.geometry.monitor_dpi} ·{' '}
        {frame.geometry.monitors.length} 个显示器 · {Math.round(frame.duration_ms)} ms
      </p>
      {frame.capture_error && <p role="alert">截图受限：{frame.capture_error}</p>}
      {frame.ocr_error && <p role="alert">OCR：{frame.ocr_error}</p>}
      {image && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(image.data_uri) && (
        <div className="visual-image">
          <img src={image.data_uri} alt="仅所选窗口的短暂脱敏预览" />
          <svg viewBox={`0 0 ${image.width} ${image.height}`} aria-label="元素与 OCR 位置">
            {[
              ...frame.elements.filter((e) => e.visible).map((e) => e.bounds),
              ...(frame.ocr?.lines.map((e) => e.bounds) || []),
              ...candidates.map((c) => c.bounding_box),
            ].map((box, i) => (
              <rect
                key={i}
                x={(box[0] - image.origin[0]) * image.scale}
                y={(box[1] - image.origin[1]) * image.scale}
                width={box[2] * image.scale}
                height={box[3] * image.scale}
              />
            ))}
          </svg>
        </div>
      )}
      <details>
        <summary>UIA 控件树（本机内容，不发给模型）</summary>
        <table>
          <thead>
            <tr>
              <th>类型</th>
              <th>标识</th>
              <th>名称</th>
              <th>可用／可见</th>
            </tr>
          </thead>
          <tbody>
            {frame.elements.map((e) => (
              <tr key={e.identity}>
                <td>{e.type}</td>
                <td>{e.automation_id}</td>
                <td>{e.name}</td>
                <td>
                  {String(e.enabled)} / {String(e.visible)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
      <details>
        <summary>OCR 文本与物理坐标</summary>
        {frame.ocr?.lines.map((line, i) => (
          <p key={i}>
            {line.text} · [{line.bounds.map(Math.round).join(', ')}] · {line.language} · 无置信分数
          </p>
        ))}
      </details>
    </>
  );
}
