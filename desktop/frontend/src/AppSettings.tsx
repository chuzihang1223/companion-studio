import SettingsPage from './SettingsPage';
import { useEffect, useState } from 'react';
import AppPanel from './AppPanel';
import ApiSettings from './ApiSettings';
import FishSettingsPanel from './FishSettingsPanel';
import WorkPreferencesPanel from './WorkPreferencesPanel';
import PermissionSettings from './PermissionSettings';
import BackgroundSettings from './BackgroundSettings';
import AutomationDiagnostics from './AutomationDiagnostics';
import RuntimeDiagnostics from './RuntimeDiagnostics';
import type { ExpressionLevel } from './api';
import type { PlaybackDiagnostic } from './AudioPlaybackService';
type Props = {
  onBack: () => void;
  onAvatar: () => void;
  onTestVoice: () => void;
  onClearCache: () => void;
  onRestart: () => void;
  onPlay: (url: string) => Promise<void>;
  general: {
    theme: string;
    setTheme: (v: string) => void;
    startup: string;
    setStartup: (v: string) => void;
  };
  chat: {
    fontSize: number;
    setFontSize: (v: number) => void;
    autoScroll: boolean;
    setAutoScroll: (v: boolean) => void;
  };
  voice: {
    enabled: boolean;
    setEnabled: (v: boolean) => void;
    prefetch: boolean;
    setPrefetch: (v: boolean) => void;
    volume: number;
    setVolume: (v: number) => void;
    expression: ExpressionLevel;
    setExpression: (v: ExpressionLevel) => void;
    speed: number;
    setSpeed: (v: number) => void;
    status: string;
    testStatus: string;
  };
  cache: { path: string; files: number; bytes: number } | null;
  health: string;
  playback: PlaybackDiagnostic | null;
  fileDiagnostic: Record<string, unknown> | null;
};
export default function AppSettings(p: Props) {
  const { general: g, chat: c, voice: v } = p;
  const [build, setBuild] = useState<Awaited<
    ReturnType<NonNullable<Window['desktopAPI']>['appInfo']>
  > | null>(null);
  useEffect(() => {
    let active = true;
    void window.desktopAPI
      ?.appInfo()
      .then((value) => {
        if (active) setBuild(value);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);
  return (
    <SettingsPage
      onBack={p.onBack}
      sections={{
        general: (
          <AppPanel title="启动与主题" icon="settings">
            <label>
              主题
              <select value={g.theme} onChange={(e) => g.setTheme(e.target.value)}>
                <option value="light">浅色</option>
                <option value="dark">深色</option>
              </select>
            </label>
            <label>
              启动时
              <select value={g.startup} onChange={(e) => g.setStartup(e.target.value)}>
                <option value="recent">恢复最近聊天</option>
                <option value="new">打开新聊天</option>
              </select>
            </label>
          </AppPanel>
        ),
        appearance: (
          <>
            <AppPanel title="背景与透明度" icon="eye">
              <BackgroundSettings />
            </AppPanel>
            <AppPanel title="字体与头像" icon="user">
              <label>
                字体大小
                <input
                  aria-label="字体大小"
                  type="range"
                  min="13"
                  max="19"
                  value={c.fontSize}
                  onChange={(e) => c.setFontSize(Number(e.target.value))}
                />
                <span>{c.fontSize}px</span>
              </label>
              <button type="button" onClick={p.onAvatar}>
                更换角色头像
              </button>
            </AppPanel>
          </>
        ),
        chat: (
          <AppPanel title="聊天阅读" icon="conversation">
            <label>
              自动滚动
              <input
                type="checkbox"
                checked={c.autoScroll}
                onChange={(e) => c.setAutoScroll(e.target.checked)}
              />
            </label>
            <p className="setting-note">
              Enter 发送，Shift + Enter 换行。不同会话保留自己的历史；回复风格可在角色档案里调整。
            </p>
          </AppPanel>
        ),
        work: (
          <AppPanel title="Agent 与本地工作" icon="network">
            <WorkPreferencesPanel />
            <p className="setting-note">
              项目目录在工作输入框内选择。每个工作会话保留自己的文件夹和工作区布局。
            </p>
          </AppPanel>
        ),
        permissions: (
          <AppPanel title="操作权限" icon="check">
            <PermissionSettings />
          </AppPanel>
        ),
        api: (
          <AppPanel title="聊天模型与安全密钥" icon="code">
            <ApiSettings />
          </AppPanel>
        ),
        voice: (
          <>
            <AppPanel title="Fish Audio 与角色声音" icon="wave">
              <FishSettingsPanel onPlay={p.onPlay} />
            </AppPanel>
            <AppPanel title="播放设置" icon="volume">
              <label>
                自动朗读
                <input
                  type="checkbox"
                  checked={v.enabled}
                  onChange={(e) => v.setEnabled(e.target.checked)}
                />
              </label>
              <label>
                后台预生成语音
                <input
                  type="checkbox"
                  checked={v.prefetch}
                  onChange={(e) => v.setPrefetch(e.target.checked)}
                />
              </label>
              <label>
                音量
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={v.volume}
                  onChange={(e) => v.setVolume(Number(e.target.value))}
                />
                <span>{v.volume}%</span>
              </label>
              <label>
                表达强度
                <select
                  value={v.expression}
                  onChange={(e) => v.setExpression(e.target.value as ExpressionLevel)}
                >
                  <option value="restrained">克制</option>
                  <option value="natural">自然</option>
                  <option value="rich">丰富</option>
                </select>
              </label>
              <label>
                语速
                <input
                  type="range"
                  min="0.9"
                  max="1.1"
                  step="0.05"
                  value={v.speed}
                  onChange={(e) => v.setSpeed(Number(e.target.value))}
                />
                <span>{v.speed.toFixed(2)}×</span>
              </label>
              <p className="setting-note">{v.status}；语音不可用时仍可文字聊天。</p>
            </AppPanel>
            <AppPanel title="本地备用与试听" icon="activity">
              <p className="setting-note">
                IndexTTS 在本机独立环境运行。是否启用备用由 Fish
                设置控制；同一句话保持同一音色来源。
              </p>
              <button type="button" onClick={p.onTestVoice}>
                测试语音
              </button>
              <p role="status">{v.testStatus}</p>
            </AppPanel>
          </>
        ),
        voiceInput: (
          <AppPanel title="Windows 语音输入" icon="wave">
            <p>点击输入框中的声波按钮，再按 Win + H 启动系统语音输入。</p>
            <p className="setting-note">
              转写由 Windows 提供，应用接收输入框中的文字。目前不读取或伪造系统录音状态。
            </p>
          </AppPanel>
        ),
        attachments: (
          <AppPanel title="消息附件" icon="folder">
            <p className="setting-note">
              每次最多 4 个文件，每个不超过 8
              MB。你主动附加并发送的内容会发送给当前聊天模型；图片理解需要模型支持。项目文件夹仅定义工作范围，不会自动上传整个目录。
            </p>
          </AppPanel>
        ),
        music: (
          <AppPanel title="音乐来源" icon="music">
            <p>在工作台的媒体模块打开网易云等桌面客户端；底部播放器保留音乐控制。</p>
            <p className="setting-note">
              客户端选择和浏览器媒体开关在「工作模式」设置中。播放状态来自实际播放器。
            </p>
          </AppPanel>
        ),
        data: (
          <AppPanel title="本地记录与语音缓存" icon="package">
            <p className="setting-note">
              语音缓存：{p.cache?.path || '正在读取…'} · {p.cache?.files || 0} 个文件
            </p>
            <button type="button" onClick={p.onClearCache}>
              清理语音缓存
            </button>
            <p className="setting-note">清理语音不会删除聊天、工作会话、记忆或个性化设置。</p>
          </AppPanel>
        ),
        developer: (
          <>
            <AppPanel title="Windows 自动操作" icon="app">
              <AutomationDiagnostics />
              <RuntimeDiagnostics />
            </AppPanel>
            <AppPanel title="语音诊断" icon="activity">
              <p className="setting-note">{p.health || '尚未读取语音状态'}</p>
              <p>播放状态：{p.playback?.playResult || '尚未播放'}</p>
              {p.playback && (
                <details>
                  <summary>查看播放诊断</summary>
                  <pre className="diagnostic-details">
                    {JSON.stringify({ file: p.fileDiagnostic, playback: p.playback }, null, 2)}
                  </pre>
                </details>
              )}
              <button type="button" onClick={p.onRestart}>
                重启语音进程
              </button>
            </AppPanel>
          </>
        ),
        about: (
          <AppPanel title="Companion Studio · 伴舟" icon="user">
            <p>
              版本 {build ? `V${build.version}` : '正在读取…'}
              {build?.build_label ? ` · ${build.build_label}` : ''}
            </p>
            <p className="setting-note">
              构建时间：
              {build?.built_at ? new Date(build.built_at).toLocaleString() : '未生成生产构建'}
            </p>
            <p className="setting-note">
              构建标识：{build?.build_id || '未读取'}
              {build ? ` · ${build.packaged ? 'Windows 安装版' : '开发模式'}` : ''}
            </p>
            <p className="setting-note">
              可导入角色卡或创建自定义角色；也可使用通用助手。聊天、声音与本机工作功能由你自行配置。
            </p>
          </AppPanel>
        ),
      }}
    />
  );
}
