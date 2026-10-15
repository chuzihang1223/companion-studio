import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import {
  addWorkTab,
  clearVoiceCache,
  createBranch,
  createConversation,
  deleteConversation,
  streamAgent,
  generateVoice,
  listBranches,
  listConversations,
  loadAppearance,
  loadCharacter,
  loadConversation,
  loadVoiceStatus,
  loadWorkForConversation,
  loadWorkSession,
  planAgent,
  renameConversation,
  retryMessage,
  routeAgent,
  stopWorkSession,
  streamMessage,
  voiceAudioUrl,
  voiceAudioDiagnostic,
  voiceCacheInfo,
  voiceInfo,
  voiceJobInfo,
  voiceHealth,
  workEventsUrl,
  testVoice,
  restartVoiceWorker,
  type AgentCall,
  type AgentResult,
  type AttachmentInput,
  type CharacterInfo,
  type ChatMessage,
  type Conversation,
  type ExpressionLevel,
  type VoiceJob,
  type WorkSession,
} from './api';
import { AudioPlaybackService, type PlaybackDiagnostic } from './AudioPlaybackService';
import Icon from './Icon';
import { ConversationNavigator } from './ConversationNavigator';
import { ConversationRuntimeStore } from './ConversationRuntimeStore';
import { ConversationSubmissionService, type Submission } from './ConversationSubmissionService';
import ConversationSidebar from './ConversationSidebar';
import { installNativeContextMenus } from './NativeContextMenu';
import { FirstSpeechGate } from './FirstSpeechGate';
import { VoicePresentationService } from './VoicePresentationService';
import { selectApplication, type AgentPlan } from './api';
import WorkbenchActionButton from './WorkbenchActionButton';
import MessageItem from './MessageItem';
import MusicCenter from './MediaCenterPage';
import Workbench from './Workbench';
import AppSettings from './AppSettings';
import CharacterPage from './CharacterPage';
import FirstRunGuide from './FirstRunGuide';
import VoiceInputButton from './VoiceInputButton';
import ComposerActionButton from './ComposerActionButton';
import FolderChip from './FolderChip';
import { usePresence, PresenceIndicator } from './PresenceService';
import { usePageNavigation } from './usePageNavigation';
import FishSettingsPanel from './FishSettingsPanel';
import avatar from './assets/companion-logo.svg';
import './style.css';
import { useAppTheme } from './AppThemeProvider';
import WindowControls from './WindowControls';
import './theme-v81.css';
import './theme-v82.css';
import './theme-v83.css';
import './theme-v84.css';

const errorText = (cause: unknown) => (cause instanceof Error ? cause.message : '操作失败');
const saved = (key: string, fallback: string) => localStorage.getItem(key) ?? fallback;
function blobUrl(base64: string) {
  const binary = atob(base64);
  return URL.createObjectURL(
    new Blob([Uint8Array.from(binary, (char) => char.charCodeAt(0))], { type: 'audio/wav' }),
  );
}

export default function AppV3() {
  const appTheme = useAppTheme();
  const theme = appTheme.config.theme;
  const setTheme = (value: string) =>
    void appTheme.update({ theme: value as 'light' | 'dark' }).catch(() => {});
  const [character, setCharacter] = useState<CharacterInfo | null>(null);
  const characterIdRef = useRef<string | null>(null);
  characterIdRef.current = character?.id || null;
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [branches, setBranches] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // store 跨可见会话切换存在；页面状态只投影当前会话，不拥有后台请求。
  const runtimeRef = useRef(new ConversationRuntimeStore());
  const runtime = runtimeRef.current;
  const [, renderRuntime] = useState(0);
  const [input, rawSetInput] = useState('');
  const draftKey = () =>
    activeIdRef.current ||
    '$new:' + (characterIdRef.current || 'assistant') + ':' + agentModeRef.current;
  function setInput(text: string) {
    rawSetInput(text);
    runtime.draft(draftKey(), text);
  }
  const [attachments, setAttachments] = useState<AttachmentInput[]>([]);
  const [attachmentMenu, setAttachmentMenu] = useState(false);
  const [workFolder, setWorkFolder] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const [workRunning, setWorkRunning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [replyTo, setReplyTo] = useState<ChatMessage | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [generatingId, setGeneratingId] = useState<string | null>(null);
  const [voiceEnabled, setVoiceEnabled] = useState(saved('voice-enabled', 'false') !== 'false');
  const [voicePrefetch, setVoicePrefetch] = useState(saved('voice-prefetch', 'false') !== 'false');
  const [expression, setExpression] = useState<ExpressionLevel>(
    saved('voice-expression', 'natural') as ExpressionLevel,
  );
  const [volume, setVolume] = useState(Number(saved('voice-volume', '80')));
  const [speed, setSpeed] = useState(Number(saved('voice-speed', '1')));
  const [fontSize, setFontSize] = useState(Number(saved('font-size', '15')));
  const [autoScroll, setAutoScroll] = useState(saved('auto-scroll', 'true') !== 'false');
  const [startup, setStartup] = useState(saved('startup', 'recent'));
  const [cacheInfo, setCacheInfo] = useState<{ path: string; files: number; bytes: number } | null>(
    null,
  );
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [musicOpen, setMusicOpen] = useState(false);
  const [agentMode, setAgentMode] = useState<'chat' | 'agent'>(
    saved('agent-mode', 'chat') === 'agent' ? 'agent' : 'chat',
  );
  const { page: panel, setPage: setPanel } = usePageNavigation(
    musicOpen ? 'music' : agentMode === 'agent' ? 'work' : 'chat',
  );
  const presence = usePresence();
  const [agentStatus, setAgentStatus] = useState('');
  const [desktopActionStatus, setDesktopActionStatus] = useState('');
  const [agentDetails, setAgentDetails] = useState<Record<string, AgentResult['tools']>>({});
  const [workSession, setWorkSession] = useState<WorkSession | null>(null);
  const [workMessages, setWorkMessages] = useState<ChatMessage[]>([]);
  const [pendingWork, setPendingWork] = useState<{
    planId: string;
    calls: AgentCall[];
    approved: string[];
    conversationId: string | null;
  } | null>(null);
  const [pendingApps, setPendingApps] = useState<AgentPlan | null>(null);
  const [customAvatar, setCustomAvatar] = useState<string | null>(null);
  const [voiceStatus, setVoiceStatus] = useState('语音尚未配置');
  const [voiceHealthInfo, setVoiceHealthInfo] = useState('');
  const [voiceProgress, setVoiceProgress] = useState<Record<string, string>>({});
  const [voiceErrors, setVoiceErrors] = useState<Record<string, string>>({});
  const [playbackDiagnostic, setPlaybackDiagnostic] = useState<PlaybackDiagnostic | null>(null);
  const [voiceFileDiagnostic, setVoiceFileDiagnostic] = useState<Record<string, unknown> | null>(
    null,
  );
  const [voiceTestStatus, setVoiceTestStatus] = useState('尚未测试');
  const [runningConversation, setRunningConversation] = useState<string | null>(null);
  const activeIdRef = useRef(activeId);
  activeIdRef.current = activeId;
  useEffect(() => {
    void window.desktopAPI?.rendererContext(activeId).catch(() => {});
  }, [activeId]);
  const gateRef = useRef<FirstSpeechGate | null>(null);
  const navigatorRef = useRef(new ConversationNavigator());
  useEffect(installNativeContextMenus, []);
  // 一次注册 store 订阅；draftKey 经 refs 读取当前角色/模式/会话归属。
  useEffect(
    () =>
      runtime.subscribe(() => {
        renderRuntime((value) => value + 1);
        const row = runtime.get(draftKey());
        busyRef.current = row.generating;
        setBusy(row.generating);
        setWorkRunning(runtime.running().length > 0);
        setRunningConversation(runtime.running()[0] || null);
      }),
    [],
  );
  function ownedMessages(id: string, update: (rows: ChatMessage[]) => ChatMessage[]) {
    const rows = runtime.messages(id, update);
    if (activeIdRef.current === id) setMessages(rows);
  }
  const busyRef = useRef(false);
  const workRunningRef = useRef(false);
  const agentModeRef = useRef(agentMode);
  agentModeRef.current = agentMode;
  const endRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const openTokenRef = useRef(0);
  const workStartTokenRef = useRef(0);
  const playbackRef = useRef<AudioPlaybackService | null>(null);
  const queueRef = useRef<Array<{ url: string; before: number; after: number; id: string }>>([]);
  const queueStartingRef = useRef(false);
  const timerRef = useRef<number | null>(null);
  const streamIdRef = useRef<string | null>(null);
  const playbackSeqRef = useRef(0);
  const generatingRef = useRef<string | null>(null);
  const playNextRef = useRef<() => void>(() => {});
  const watchedJobsRef = useRef(new Set<string>());
  const activeJobRef = useRef<string | null>(null);
  const seenSegmentsRef = useRef(new Map<string, number>());
  const currentQueuedAfterRef = useRef(0);
  const avatarSrc = customAvatar ?? avatar;
  // 默认使用按会话排队的提交服务；下方旧串行分支仅保留兼容入口。
  const concurrentRuntime = saved('v85-concurrent-runtime', 'true') === 'true';
  const submitTimesRef = useRef(new Map<string, number>());
  const submissionRef = useRef<ConversationSubmissionService | null>(null);

  if (!playbackRef.current)
    playbackRef.current = new AudioPlaybackService(
      (id, diagnostic) => {
        if (
          diagnostic &&
          ['browser-started', 'pcm-started', 'native-started', 'native-pcm-started'].includes(
            diagnostic.playResult || '',
          )
        ) {
          gateRef.current?.audioStarted();
          if (id) submissionRef.current?.audioStarted(id);
        }
        setPlayingId(id);
        if (diagnostic) {
          setPlaybackDiagnostic(diagnostic);
          if (['native-failed', 'media-error', 'pcm-failed'].includes(diagnostic.playResult || ''))
            setError(`语音播放失败：${diagnostic.detail ?? diagnostic.playResult}`);
        }
      },
      () => {
        timerRef.current = window.setTimeout(
          () => playNextRef.current(),
          currentQueuedAfterRef.current,
        );
      },
    );

  const speechRef = useRef<VoicePresentationService | null>(null);
  const chatAbortRef = useRef<AbortController | null>(null);
  if (!submissionRef.current)
    submissionRef.current = new ConversationSubmissionService(runtime, playbackRef.current, {
      visible: (id) => activeIdRef.current === id && !document.hidden,
      created: (origin, id, mode, characterId) => {
        if (characterIdRef.current === characterId && activeIdRef.current === origin) {
          activeIdRef.current = id;
          setActiveId(id);
          agentModeRef.current = mode;
          setAgentMode(mode);
        }
      },
      messages: (id, rows) => {
        if (activeIdRef.current === id) setMessages(rows);
      },
      work: (id, row) => {
        if (activeIdRef.current === id) setWorkSession(row);
      },
      changed: () => {
        const row = runtime.get(draftKey());
        setError(row.error);
        setPendingWork(row.pending);
        setPendingApps(row.apps);
        setAgentStatus(row.status);
        if (characterIdRef.current) void refreshList(characterIdRef.current).catch(() => {});
      },
    });
  function submission(text: string): Submission {
    return {
      conversationId: activeIdRef.current,
      character: character!,
      text,
      mode: agentModeRef.current,
      folder: workFolder,
      attachments,
      replyTo: replyTo?.id,
      voice: voiceEnabled,
      expression,
      speed,
      volume: volume / 100,
      prefetch: voicePrefetch && voiceEnabled,
      autoWork: false,
    };
  }
  function stopAudio() {
    if (concurrentRuntime) submissionRef.current?.silence();
    speechRef.current?.detach();
    speechRef.current = null;
    // 请求拥有自己的流；导航仅分离播放，后台文字及语音缓存仍继续完成。
    gateRef.current = null;
    playbackSeqRef.current += 1;
    activeJobRef.current = null;
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    playbackRef.current?.stop();
    for (const item of queueRef.current)
      if (item.url.startsWith('blob:')) URL.revokeObjectURL(item.url);
    queueRef.current = [];
    queueStartingRef.current = false;
    streamIdRef.current = null;
    setPlayingId(null);
  }
  playNextRef.current = () => {
    if (playbackRef.current?.currentId || queueStartingRef.current) return;
    const item = queueRef.current.shift();
    if (!item) {
      setPlayingId(null);
      return;
    }
    queueStartingRef.current = true;
    currentQueuedAfterRef.current = item.after;
    timerRef.current = window.setTimeout(() => {
      void playbackRef
        .current!.play(item.id, item.url, volume / 100)
        .catch((cause) => {
          setVoiceErrors((old) => ({ ...old, [item.id]: errorText(cause) }));
          setError(`语音播放失败：${errorText(cause)}`);
          playbackRef.current?.stop();
        })
        .finally(() => {
          queueStartingRef.current = false;
          if (item.url.startsWith('blob:')) URL.revokeObjectURL(item.url);
        });
    }, item.before);
  };
  async function watchJob(jobId: string, messageId: string) {
    if (watchedJobsRef.current.has(jobId)) return;
    watchedJobsRef.current.add(jobId);
    try {
      for (;;) {
        const job = await voiceJobInfo(jobId);
        const state =
          job.status === 'synthesizing' && job.segments_done > 0 ? 'partial' : job.status;
        setMessages((rows) =>
          rows.map((row) => (row.id === messageId ? { ...row, voice_status: state } : row)),
        );
        setVoiceProgress((old) => ({
          ...old,
          [messageId]:
            job.status === 'queued'
              ? '等待生成语音'
              : job.status === 'loading'
                ? '正在准备语音模型'
                : job.status === 'synthesizing'
                  ? `正在生成 ${job.segments_done}/${job.segments_total}`
                  : job.status === 'saving'
                    ? '正在保存语音'
                    : '播放语音',
        }));
        if (activeJobRef.current === jobId) {
          const seen = seenSegmentsRef.current.get(jobId) ?? 0;
          for (const item of job.segments.slice(seen)) {
            await playbackRef.current!.queueWav(
              messageId,
              `${(await window.desktopAPI?.getBackendUrl()) ?? 'http://127.0.0.1:8000'}${item.audio_url}`,
              `${jobId}:${item.index}`,
              volume / 100,
            );
          }
          seenSegmentsRef.current.set(jobId, job.segments.length);
        }
        if (job.status === 'ready' || job.status === 'failed' || job.status === 'cancelled') {
          if (activeJobRef.current === jobId) playbackRef.current?.finishPcm();
          if (job.status === 'failed') {
            setVoiceErrors((old) => ({ ...old, [messageId]: job.error_message || '语音生成失败' }));
            if (activeJobRef.current === jobId)
              setError(job.error_message || '语音生成失败；点击图标重试');
          }
          if (activeJobRef.current === jobId) activeJobRef.current = null;
          break;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 150));
      }
    } catch (cause) {
      setVoiceErrors((old) => ({ ...old, [messageId]: errorText(cause) }));
    } finally {
      watchedJobsRef.current.delete(jobId);
    }
  }
  async function refreshList(id: string, term = '') {
    setConversations(await listConversations(id, term));
  }
  async function openConversation(id: string) {
    setPanel(null);
    const token = ++openTokenRef.current;
    runtime.get(draftKey()).attachments = attachments;
    busyRef.current = false;
    setBusy(false);
    activeIdRef.current = id;
    setActiveId(id);
    const selected = conversations.find((row) => row.id === id);
    if (selected) {
      const mode = selected.conversation_type === 'chat' ? 'chat' : 'agent';
      agentModeRef.current = mode;
      setAgentMode(mode);
    }
    setPendingWork(null);
    setPendingApps(null);
    setWorkSession(null);
    setWorkMessages([]);
    stopAudio();
    setLoading(true);
    setError(runtime.get(id).error);
    setReplyTo(null);
    rawSetInput(runtime.get(id).draft);
    setAttachments(runtime.get(id).attachments);
    setGeneratingId(null);
    setVoiceProgress({});
    setVoiceErrors({});
    setBranches([]);
    try {
      const { messages: rows, branches: nearby, work, mode } = await navigatorRef.current.open(id);
      if (token !== openTokenRef.current) return;
      setActiveId(id);
      setMessages(
        runtime.get(id).generating && runtime.get(id).messages ? runtime.get(id).messages! : rows,
      );
      if (!runtime.get(id).generating) runtime.get(id).messages = rows;
      setBusy(runtime.get(id).generating);
      busyRef.current = runtime.get(id).generating;
      runtime.patch(id, { completed: false });
      setBranches(nearby);
      setWorkMessages(rows);
      setMusicOpen(false);
      setWorkFolder(work?.workspace_root || null);
      agentModeRef.current = mode;
      setAgentMode(mode);
      setWorkSession(work);
      if (concurrentRuntime) {
        setPendingWork(runtime.get(id).pending);
        setPendingApps(runtime.get(id).apps);
      }
      if (work?.pending_plan && !runtime.get(id).generating) {
        if (
          !work.pending_plan.calls.length &&
          work.pending_plan.local_resolution?.candidates?.length
        ) {
          const plan = {
            ...work.pending_plan,
            workspace: work.workspace_root || '',
            conversation_id: id,
            work_session_id: work.id,
          };
          runtime.get(id).apps = plan;
          setPendingApps(plan);
        } else {
          const pending = {
            planId: work.pending_plan.plan_id,
            calls: work.pending_plan.calls,
            approved: [],
            conversationId: id,
          };
          runtime.get(id).pending = pending;
          setPendingWork(pending);
        }
      }
      runtime.get(id).work_session_id = work?.id || runtime.get(id).work_session_id;
      if (work?.tool_results?.length) {
        const last = [...rows].reverse().find((row) => row.role === 'assistant');
        if (last) setAgentDetails((old) => ({ ...old, [last.id]: work.tool_results! }));
      }
      for (const row of rows)
        if (
          row.role === 'assistant' &&
          ['queued', 'loading', 'synthesizing', 'saving'].includes(row.voice_status || '')
        ) {
          void voiceInfo(row.id)
            .then((info) => {
              if (info.job_id) void watchJob(info.job_id, row.id);
            })
            .catch(() => {});
        }
    } catch (cause) {
      if (token === openTokenRef.current) setError(`加载聊天失败：${errorText(cause)}`);
    } finally {
      if (token === openTokenRef.current) {
        setLoading(false);
        window.setTimeout(() => composerRef.current?.focus(), 0);
      }
    }
  }
  useEffect(() => {
    if (panel === 'settings') {
      void voiceCacheInfo()
        .then(setCacheInfo)
        .catch(() => setCacheInfo(null));
      void voiceHealth()
        .then((data) =>
          setVoiceHealthInfo(
            `${data.engine} · ${data.status} · ${data.device ?? '设备未知'} · 排队 ${data.queue_length} · 预热 ${data.warmup_ms ?? 0}ms${data.last_error ? ` · ${data.last_error}` : ''}`,
          ),
        )
        .catch((cause) => setVoiceHealthInfo(errorText(cause)));
    }
  }, [panel]);
  useEffect(() => {
    let active = true;
    void loadCharacter()
      .then(async (profile) => {
        if (!active) return;
        setCharacter(profile);
        const base = (await window.desktopAPI?.getBackendUrl()) ?? 'http://127.0.0.1:8000';
        const appearance = profile.avatar_url ? null : await loadAppearance().catch(() => null);
        if (!active) return;
        setCustomAvatar(
          profile.avatar_url
            ? base + profile.avatar_url
            : appearance?.avatar
              ? base + appearance.avatar
              : null,
        );
        const items = await listConversations(profile.id);
        if (!active) return;
        setConversations(items);
        if (startup === 'new') {
          setActiveId(null);
          setMessages([]);
          setLoading(false);
        } else if (items.length) await openConversation(items[0].id);
        else setLoading(false);
      })
      .catch((cause) => {
        if (active) {
          setError(`启动失败：${errorText(cause)}`);
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (!workSession?.id) return;
    let live = true;
    let stream: EventSource | null = null;
    void workEventsUrl(workSession.id).then((url) => {
      if (!live) return;
      stream = new EventSource(url);
      for (const type of [
        'workflow_updated',
        'permission_requested',
        'permission_resolved',
        'tool_started',
        'tool_completed',
        'workspace_opened',
        'workspace_updated',
        'app_launch_requested',
        'app_launched',
        'work_completed',
        'work_stopped',
        'plan_updated',
        'step_started',
        'verification',
        'goal_verification',
        'retrying',
        'replanning',
        'task_pause',
        'task_resume',
        'task_takeover',
        'task_cancel',
        'task_completed',
        'task_failed',
        'user_interruption',
        'workspace_changed',
        'trace_updated',
      ]) {
        stream.addEventListener(type, () => {
          void loadWorkSession(workSession.id)
            .then((row) => {
              if (live && activeIdRef.current === row.conversation_id) setWorkSession(row);
            })
            .catch(() => {});
        });
      }
    });
    return () => {
      live = false;
      stream?.close();
    };
  }, [workSession?.id]);
  useEffect(() => {
    if (autoScroll) endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, busy, autoScroll]);
  useEffect(() => {
    const check = () => {
      void loadVoiceStatus()
        .then(({ status, engine }) =>
          setVoiceStatus(
            status === 'online'
              ? `${engine.startsWith('fish') ? 'Fish Audio' : 'IndexTTS'} 已就绪`
              : status === 'preparing'
                ? '语音尚未配置'
                : '语音暂不可用',
          ),
        )
        .catch(() => setVoiceStatus('语音暂不可用'));
    };
    check();
    const timer = window.setInterval(check, 6000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    localStorage.setItem('voice-enabled', String(voiceEnabled));
    localStorage.setItem('voice-prefetch', String(voicePrefetch));
    localStorage.setItem('voice-expression', expression);
    localStorage.setItem('voice-volume', String(volume));
    localStorage.setItem('voice-speed', String(speed));
    localStorage.setItem('font-size', String(fontSize));
    localStorage.setItem('auto-scroll', String(autoScroll));
    localStorage.setItem('startup', startup);
    localStorage.setItem('agent-mode', agentMode);
    document.documentElement.style.setProperty('--message-font-size', `${fontSize}px`);
    playbackRef.current?.setVolume(volume / 100);
  }, [
    voiceEnabled,
    voicePrefetch,
    expression,
    volume,
    speed,
    theme,
    fontSize,
    autoScroll,
    startup,
    agentMode,
  ]);
  useEffect(() => {
    appTheme.mode(agentMode);
  }, [agentMode]);
  useEffect(() => {
    void window.desktopAPI
      ?.watchWorkFiles(agentMode === 'agent' ? workSession?.id || null : null)
      .catch(() => {});
    return () => {
      void window.desktopAPI?.watchWorkFiles(null);
    };
  }, [agentMode, workSession?.id, workSession?.workspace_root]);

  async function clearCache() {
    if (!window.confirm('清理已生成的语音缓存？聊天记录会保留，之后点播放可重新生成。')) return;
    stopAudio();
    try {
      await clearVoiceCache();
      setCacheInfo(await voiceCacheInfo());
      setMessages((rows) =>
        rows.map((row) => (row.role === 'assistant' ? { ...row, voice_status: 'missing' } : row)),
      );
    } catch (cause) {
      setError(`清理语音失败：${errorText(cause)}`);
    }
  }

  async function newChat() {
    if (!character) return;
    runtime.get(draftKey()).attachments = attachments;
    setPanel(null);
    setMusicOpen(false);
    ++openTokenRef.current;
    busyRef.current = false;
    setBusy(false);
    activeIdRef.current = null;
    stopAudio();
    setActiveId(null);
    setMessages([]);
    setBranches([]);
    setReplyTo(null);
    setInput('');
    setAttachments([]);
    setAttachmentMenu(false);
    setWorkFolder(null);
    setWorkSession(null);
    setPendingWork(null);
    setWorkMessages([]);
    setGeneratingId(null);
    setError('');
    setLoading(false);
    window.setTimeout(() => composerRef.current?.focus(), 0);
  }
  function switchMode(mode: 'chat' | 'agent') {
    setPanel(null);
    setMusicOpen(false);
    agentModeRef.current = mode;
    setAgentMode(mode);
    if (activeId) {
      const current = conversations.find((row) => row.id === activeId);
      if (!current || (current.conversation_type === 'chat') !== (mode === 'chat')) void newChat();
    }
  }
  async function openDesktopMusic() {
    setDesktopActionStatus('正在检查网易云音乐客户端…');
    try {
      if (!window.desktopAPI) throw new Error('请在桌面程序中使用');
      const result = await window.desktopAPI.launchKnownApp('netease');
      setDesktopActionStatus(
        result.status === 'cancelled'
          ? '已取消打开网易云音乐。'
          : !result.installed
            ? '未找到网易云音乐桌面客户端。'
            : '已向 Windows 请求打开网易云音乐，请查看桌面窗口。',
      );
    } catch (cause) {
      setDesktopActionStatus(`打开网易云音乐失败：${errorText(cause)}`);
    }
  }
  async function finishWork(planId: string, approvedIds: string[], conversationId: string | null) {
    if (activeIdRef.current === conversationId) {
      setPendingWork(null);
      setAgentStatus('正在执行任务并整理结果…');
    }
    const showWorkConversation = () =>
      agentModeRef.current === 'agent' && activeIdRef.current === conversationId;
    let replyId = '',
      result: AgentResult | null = null;
    const gate = new FirstSpeechGate(planId, voiceEnabled, (text) => {
      if (showWorkConversation())
        setMessages((rows) =>
          rows.map((row) => (row.id === replyId ? { ...row, content: row.content + text } : row)),
        );
    });
    const speech = new VoicePresentationService(playbackRef.current!, volume / 100, (detail) => {
      if (showWorkConversation()) setError(detail);
    });
    if (showWorkConversation()) {
      gateRef.current = gate;
      speechRef.current = speech;
    }
    for await (const item of streamAgent(planId, approvedIds, voiceEnabled, expression, speed)) {
      if (item.event === 'completed') result = item.data as unknown as AgentResult;
      if (item.event === 'error') throw Error(String(item.data.detail));
      if (!showWorkConversation()) {
        if (item.event === 'assistant_started' && item.data.session_id)
          void window.desktopAPI?.cancelVoiceStream(String(item.data.session_id));
        continue;
      }
      if (await speech.accept(item)) continue;
      if (item.event === 'assistant_started') {
        replyId = String(item.data.message_id);
        gate.setMessageId(replyId);
        setMessages((rows) => [
          ...rows,
          {
            id: replyId,
            role: 'assistant',
            character_id: character!.id,
            content: '',
            created_at: new Date().toISOString(),
            voice_status: voiceEnabled ? 'generating' : 'none',
          },
        ]);
      } else if (item.event === 'text_delta') gate.push(String(item.data.text));
      else if (item.event === 'voice_saved')
        setMessages((rows) =>
          rows.map((row) => (row.id === replyId ? { ...row, voice_status: 'ready' } : row)),
        );
    }
    if (!result) throw Error('任务没有返回最终结果');
    await gate.finish();
    const displayedTools = [] as AgentResult['tools'];
    for (const item of result.tools) {
      if (item.result.action === 'open_url' && typeof item.result.url === 'string') {
        try {
          if (!window.desktopAPI) throw new Error('请在桌面程序中打开网页');
          if (showWorkConversation())
            window.dispatchEvent(
              new CustomEvent('companion-work-browser', { detail: { url: item.result.url } }),
            );
          else if (result.work_session_id)
            await addWorkTab(result.work_session_id, 'web', '待打开网页', { url: item.result.url });
          displayedTools.push({
            ...item,
            status: 'queued',
            result: { ...item.result, opened: 'requested' },
          });
        } catch (cause) {
          displayedTools.push({ ...item, status: 'failed', result: { error: errorText(cause) } });
        }
      } else if (item.result.action === 'launch_app' && typeof item.result.app === 'string') {
        try {
          if (!window.desktopAPI) throw new Error('请在桌面程序中启动应用');
          const launched = await window.desktopAPI.launchKnownApp(
            item.result.app,
            result.work_session_id,
          );
          setDesktopActionStatus(
            launched.status === 'cancelled'
              ? `已取消打开${launched.name}。`
              : !launched.installed
                ? `未找到${launched.name}桌面客户端。`
                : `已向 Windows 请求打开${launched.name}，请查看桌面窗口。`,
          );
          displayedTools.push({
            ...item,
            status:
              launched.status === 'running'
                ? 'done'
                : launched.status === 'launch_requested'
                  ? 'queued'
                  : 'failed',
            result: { ...item.result, ...launched },
          });
          if (result.work_session_id)
            await addWorkTab(result.work_session_id, 'app', launched.name, {
              app: launched.id,
              action: 'launch',
              status: launched.status,
              pid: launched.pid,
            }).catch(() => {});
        } catch (cause) {
          setDesktopActionStatus(`启动应用失败：${errorText(cause)}`);
          displayedTools.push({ ...item, status: 'failed', result: { error: errorText(cause) } });
        }
      } else if (
        ['query_app', 'focus_app', 'close_app'].includes(String(item.result.action)) &&
        typeof item.result.app === 'string'
      ) {
        try {
          if (!window.desktopAPI) throw new Error('请在桌面程序中管理应用');
          const action = item.result.action;
          const state =
            action === 'focus_app'
              ? await window.desktopAPI.focusKnownApp(item.result.app)
              : action === 'close_app'
                ? await window.desktopAPI.closeKnownApp(item.result.app)
                : await window.desktopAPI.knownAppStatus(item.result.app);
          const succeeded =
            action === 'focus_app'
              ? Boolean('focused' in state && state.focused)
              : action === 'close_app'
                ? Boolean('closed' in state && state.closed)
                : true;
          setDesktopActionStatus(
            action === 'query_app'
              ? `${state.name}：${state.status === 'running' ? '正在运行' : state.installed ? '已安装，尚未打开' : '未安装'}。`
              : action === 'focus_app'
                ? `${state.name}：${succeeded ? '已请求切换到应用窗口' : '未找到可切换的窗口'}。`
                : `${state.name}：${succeeded ? '已请求关闭应用' : '未关闭应用'}。`,
          );
          displayedTools.push({
            ...item,
            status: succeeded ? 'done' : 'failed',
            result: { ...item.result, ...state },
          });
          if (result.work_session_id)
            await addWorkTab(result.work_session_id, 'app', state.name, {
              app: state.id,
              action,
              status: state.status,
              pid: state.pid,
            }).catch(() => {});
        } catch (cause) {
          displayedTools.push({ ...item, status: 'failed', result: { error: errorText(cause) } });
        }
      } else {
        displayedTools.push(item);
        if (
          item.result.action === 'local_music_search' ||
          item.result.action === 'local_music_pause'
        ) {
          window.dispatchEvent(new CustomEvent('companion-music-action', { detail: item.result }));
        }
        if (!result.managed_by_runtime && result.work_session_id && item.result.output)
          await addWorkTab(result.work_session_id, 'terminal', item.name, {
            output: String(item.result.output).slice(0, 16000),
            exit_code: item.result.exit_code,
          }).catch(() => {});
        else if (
          !result.managed_by_runtime &&
          result.work_session_id &&
          item.result.path &&
          ['code.create_file', 'code.edit_file', 'filesystem.write'].includes(item.name)
        ) {
          await addWorkTab(result.work_session_id, 'code', String(item.result.path), {
            summary: `${item.name} · ${String(item.result.bytes || 0)} bytes`,
            diff: String(item.result.diff || '').slice(0, 16000),
          }).catch(() => {});
        }
      }
    }
    setAgentDetails((old) => ({ ...old, [result.message_id]: displayedTools }));
    if (voiceEnabled && result.voice_job?.job_id && showWorkConversation()) {
      activeJobRef.current = result.voice_job.job_id;
      void watchJob(result.voice_job.job_id, result.message_id);
    }
    if (result.voice_error) setError(`语音生成失败：${result.voice_error}`);
    if (result.work_session_id && showWorkConversation()) {
      const restored = await loadWorkSession(result.work_session_id);
      if (showWorkConversation()) setWorkSession(restored);
    }
    await refreshList(character!.id, search);
    if (showWorkConversation()) {
      const restored = await listBranches(result.conversation_id);
      if (showWorkConversation()) setBranches(restored);
    }
    if (showWorkConversation()) setAgentStatus('');
    return conversationId;
  }
  async function executePendingWork() {
    if (concurrentRuntime && pendingWork?.conversationId) {
      submissionRef.current?.approve(pendingWork.planId, pendingWork.approved, {
        ...submission(''),
        conversationId: pendingWork.conversationId,
      });
      return;
    }
    if (!pendingWork || busyRef.current || workRunningRef.current) return;
    workStartTokenRef.current = openTokenRef.current;
    workRunningRef.current = true;
    setWorkRunning(true);
    setRunningConversation(pendingWork.conversationId);
    setError('');
    try {
      await finishWork(pendingWork.planId, pendingWork.approved, pendingWork.conversationId);
    } catch (cause) {
      setError(`工作任务失败：${errorText(cause)}`);
      setAgentStatus('');
    } finally {
      workRunningRef.current = false;
      setWorkRunning(false);
      setRunningConversation(null);
    }
  }
  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (concurrentRuntime) {
      const text = input.trim() || (attachments.length ? '请看看这些附件。' : '');
      if (!text || !character) return;
      const key = draftKey() + ':' + text,
        now = Date.now();
      if (now - (submitTimesRef.current.get(key) || 0) < 250) return;
      submitTimesRef.current.set(key, now);
      setTimeout(() => submitTimesRef.current.delete(key), 500);
      const request = submission(text);
      runtime.get(draftKey()).attachments = [];
      setInput('');
      setAttachments([]);
      setReplyTo(null);
      if (
        request.conversationId &&
        agentMode === 'agent' &&
        /^(停止|停下|取消任务)[。！!]?$/u.test(text)
      ) {
        await submissionRef.current?.stop(request.conversationId);
        return;
      }
      const prefs = await window.desktopAPI?.workPreferences();
      request.autoWork = !!prefs?.auto_work_mode;
      await submissionRef.current!.submit(request).catch((cause) => setError(errorText(cause)));
      return;
    }
    const text = input.trim() || (attachments.length ? '请看看这些附件。' : '');
    if (
      !text ||
      busyRef.current ||
      !character ||
      (agentMode === 'agent' && workRunningRef.current && runningConversation === activeId)
    )
      return;
    const sentAttachments = attachments;
    busyRef.current = true;
    setBusy(true);
    setPendingApps(null);
    setError('');
    setDesktopActionStatus('');
    setInput('');
    setAttachments([]);
    setAttachmentMenu(false);
    const target = replyTo;
    setReplyTo(null);
    stopAudio();
    const userDraft = `pending-user-${Date.now()}`;
    const assistantDraft = `pending-assistant-${Date.now()}`;
    setMessages((rows) => [
      ...rows,
      {
        id: userDraft,
        character_id: character.id,
        role: 'user',
        content: text,
        created_at: new Date().toISOString(),
      },
      {
        id: assistantDraft,
        character_id: character.id,
        role: 'assistant',
        content: '',
        created_at: new Date().toISOString(),
        voice_status: voiceEnabled ? 'queued' : 'none',
      },
    ]);
    let conversationId = activeId;
    const sendToken = openTokenRef.current;
    const updateMessages = (update: (rows: ChatMessage[]) => ChatMessage[]) => {
      if (sendToken === openTokenRef.current) setMessages(update);
    };
    let replyRowId = assistantDraft;
    const speech = new VoicePresentationService(playbackRef.current!, volume / 100, (detail) =>
      setError(detail),
    );
    speechRef.current = speech;
    const gate = new FirstSpeechGate(assistantDraft, voiceEnabled, (text) =>
      updateMessages((rows) =>
        rows.map((row) => (row.id === replyRowId ? { ...row, content: row.content + text } : row)),
      ),
    );
    gateRef.current = gate;
    try {
      const prefs = await window.desktopAPI?.workPreferences();
      const routed = await routeAgent(
        text,
        agentMode === 'chat' && prefs?.auto_work_mode ? 'auto' : agentMode,
      );
      if (routed === 'agent' && agentMode === 'chat') {
        const row = await createConversation(character.id, 'work');
        conversationId = row.id;
        if (sendToken === openTokenRef.current) {
          activeIdRef.current = row.id;
          setActiveId(row.id);
          agentModeRef.current = 'agent';
          setAgentMode('agent');
          setWorkSession(null);
          setPendingWork(null);
        }
      }
      if (!conversationId) {
        const row = await createConversation(character.id, routed === 'agent' ? 'work' : 'chat');
        conversationId = row.id;
        if (sendToken === openTokenRef.current) {
          activeIdRef.current = row.id;
          setActiveId(row.id);
        }
      }
      if (routed === 'agent') {
        setWorkSession(null);
        setAgentStatus('正在规划任务…');
        const plan = await planAgent(
          character.id,
          conversationId,
          text,
          sentAttachments,
          workFolder,
        );
        if (plan.work_session_id && sendToken === openTokenRef.current)
          setWorkSession(await loadWorkSession(plan.work_session_id));
        if (sendToken !== openTokenRef.current) {
          await refreshList(character.id, search);
          return;
        }
        setMessages(await loadConversation(conversationId!));
        await refreshList(character.id, search);
        if (!plan.calls.length && plan.local_resolution?.candidates?.length) {
          setPendingApps(plan);
          setAgentStatus('请选择要操作的应用');
          return;
        }
        if (plan.calls.some((call) => call.requires_confirmation)) {
          setPendingWork({ planId: plan.plan_id, calls: plan.calls, approved: [], conversationId });
          setAgentStatus('等待操作授权');
        } else {
          workRunningRef.current = true;
          setWorkRunning(true);
          setRunningConversation(conversationId);
          try {
            await finishWork(plan.plan_id, [], conversationId);
          } finally {
            workRunningRef.current = false;
            setWorkRunning(false);
            setRunningConversation(null);
          }
        }
        return;
      }
      if (voiceEnabled) streamIdRef.current = assistantDraft;
      const streamAbort = new AbortController();
      chatAbortRef.current = streamAbort;
      for await (const item of streamMessage(
        character.id,
        text,
        expression,
        voiceEnabled,
        conversationId,
        target?.id,
        speed,
        voicePrefetch && voiceEnabled,
        sentAttachments,
        streamAbort.signal,
      )) {
        if (sendToken !== openTokenRef.current) continue;
        if (await speech.accept(item)) continue;
        if (item.event === 'user_saved' && typeof item.data.message_id === 'string')
          setMessages((rows) =>
            rows.map((row) =>
              row.id === userDraft
                ? { ...row, id: item.data.message_id as string, reply_to_message_id: target?.id }
                : row,
            ),
          );
        else if (item.event === 'assistant_started' && typeof item.data.message_id === 'string') {
          replyRowId = item.data.message_id;
          gate.setMessageId(replyRowId);
          setMessages((rows) =>
            rows.map((row) => (row.id === assistantDraft ? { ...row, id: replyRowId } : row)),
          );
          if (streamIdRef.current === assistantDraft) streamIdRef.current = replyRowId;
        } else if (item.event === 'text_delta' && typeof item.data.text === 'string')
          gate.push(item.data.text);
        else if (item.event === 'completed' && typeof item.data.message_id === 'string') {
          const id = item.data.message_id;
          replyRowId = id;
          setMessages((rows) =>
            rows.map((row) => (row.id === assistantDraft ? { ...row, id } : row)),
          );
          if (streamIdRef.current === assistantDraft) streamIdRef.current = id;
        } else if (item.event === 'audio_pcm' && streamIdRef.current) {
          try {
            await playbackRef.current!.feedPcm(
              streamIdRef.current,
              String(item.data.pcm_base64),
              Number(item.data.sample_rate),
              Number(item.data.channels),
              volume / 100,
              String(item.data.segment_id || streamIdRef.current),
            );
          } catch (cause) {
            setError(`语音播放失败：${errorText(cause)}`);
          }
        } else if (item.event === 'audio_segment_end') {
          await playbackRef.current?.endPcmSegment(String(item.data.segment_id));
        } else if (item.event === 'audio_end') {
          playbackRef.current?.finishPcm();
        } else if (item.event === 'voice_error') {
          setError(String(item.data.detail));
        } else if (item.event === 'audio_aborted') {
          playbackRef.current?.stop();
          setError(String(item.data.detail));
        } else if (
          (item.event === 'audio_chunk' || item.event === 'audio_complete') &&
          streamIdRef.current
        ) {
          if (item.event === 'audio_complete') {
            if (playbackRef.current!.finishPcm()) continue;
            // An interrupted Web Audio context must release the queue before
            // the complete WAV can use the Windows playback fallback.
            playbackRef.current!.stop();
          }
          const url =
            typeof item.data.audio_url === 'string'
              ? `${(await window.desktopAPI?.getBackendUrl()) ?? 'http://127.0.0.1:8000'}${item.data.audio_url}`
              : typeof item.data.audio_base64 === 'string'
                ? blobUrl(item.data.audio_base64)
                : '';
          if (!url) continue;
          queueRef.current.push({
            url,
            before: Number(item.data.pause_before_ms ?? 0),
            after: Number(item.data.pause_after_ms ?? 80),
            id: streamIdRef.current,
          });
          playNextRef.current();
        } else if (item.event === 'voice_job' && typeof item.data.job_id === 'string') {
          const job = item.data as unknown as VoiceJob;
          if (voiceEnabled) activeJobRef.current = job.job_id;
          if (item.data.early_audio_sent) seenSegmentsRef.current.set(job.job_id, 1);
          setMessages((rows) =>
            rows.map((row) =>
              row.id === job.message_id ? { ...row, voice_status: job.status } : row,
            ),
          );
          void watchJob(job.job_id, job.message_id);
        } else if (item.event === 'voice_saved' && typeof item.data.message_id === 'string')
          setMessages((rows) =>
            rows.map((row) =>
              row.id === item.data.message_id ? { ...row, voice_status: 'ready' } : row,
            ),
          );
        else if (item.event === 'error') throw new Error(String(item.data.detail ?? '回复失败'));
      }
      await gate.finish();
      await refreshList(character.id, search);
      if (sendToken !== openTokenRef.current) return;
      await loadConversation(conversationId!).then(setMessages);
      await listBranches(conversationId!).then(setBranches);
    } catch (cause) {
      if (sendToken !== openTokenRef.current) return;
      gate.stop();
      setError(`发送失败：${errorText(cause)}`);
      setInput(text);
      setAttachments(sentAttachments);
      if (conversationId)
        await loadConversation(conversationId)
          .then(setMessages)
          .catch(() => {});
      else setMessages([]);
    } finally {
      if (sendToken === openTokenRef.current) {
        chatAbortRef.current = null;
        busyRef.current = false;
        setBusy(false);
        setAgentStatus('');
        streamIdRef.current = null;
      }
    }
  }
  async function playVoice(message: ChatMessage, regenerate = false) {
    if (playingId === message.id && playbackRef.current?.currentId === message.id) {
      playbackRef.current.pause();
      return;
    }
    if (
      playbackRef.current?.paused &&
      playbackRef.current.currentId === message.id &&
      !regenerate
    ) {
      void playbackRef.current
        .resume()
        .catch((cause) => setError(`继续播放失败：${errorText(cause)}`));
      return;
    }
    stopAudio();
    const requestSeq = playbackSeqRef.current;
    try {
      const info = await voiceInfo(message.id);
      if (requestSeq !== playbackSeqRef.current) return;
      if (regenerate || info.status !== 'ready') {
        setGeneratingId(message.id);
        const job = await generateVoice(message.id, expression, regenerate, speed);
        if (requestSeq !== playbackSeqRef.current) return;
        if (job.status !== 'ready') {
          activeJobRef.current = job.job_id;
          setMessages((rows) =>
            rows.map((row) => (row.id === message.id ? { ...row, voice_status: job.status } : row)),
          );
          void watchJob(job.job_id, message.id);
          return;
        }
      }
      const url = await voiceAudioUrl(message.id);
      if (requestSeq !== playbackSeqRef.current) return;
      void voiceAudioDiagnostic(message.id)
        .then(setVoiceFileDiagnostic)
        .catch((cause) => setVoiceFileDiagnostic({ error: errorText(cause) }));
      await playbackRef.current!.play(message.id, url, volume / 100);
    } catch (cause) {
      setVoiceErrors((old) => ({ ...old, [message.id]: errorText(cause) }));
      setError(`语音播放失败：${errorText(cause)}`);
      playbackRef.current?.stop();
    } finally {
      if (generatingRef.current === message.id) generatingRef.current = null;
      setGeneratingId(null);
    }
  }
  async function branchAt(message: ChatMessage, edit = false) {
    if (!activeId || busyRef.current || !character) return;
    const index = messages.findIndex((row) => row.id === message.id);
    try {
      const row = await createBranch(
        activeId,
        edit ? (index > 0 ? messages[index - 1].id : null) : message.id,
      );
      await refreshList(character.id, search);
      await openConversation(row.id);
      if (edit) setInput(message.content);
    } catch (cause) {
      setError(errorText(cause));
    }
  }
  async function retry(message: ChatMessage) {
    if (busyRef.current || !activeId) return;
    busyRef.current = true;
    setBusy(true);
    setError('');
    try {
      const answer = await retryMessage(message.id);
      setMessages((rows) => [
        ...rows,
        {
          id: answer.message_id,
          character_id: answer.character_id,
          conversation_id: activeId,
          role: 'assistant',
          content: answer.content,
          created_at: answer.created_at,
          variant_of_message_id: message.id,
          voice_status: 'none',
        },
      ]);
    } catch (cause) {
      setError(`重新生成失败：${errorText(cause)}`);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function chatAction(item: Conversation, kind: 'rename' | 'delete' | 'branch') {
    if (busyRef.current) {
      setError('请等当前回复完成后再管理聊天。');
      return;
    }
    try {
      if (kind === 'rename') {
        const title = window.prompt('新的聊天标题', item.title)?.trim();
        if (title) await renameConversation(item.id, title);
      } else if (kind === 'delete') {
        if (!window.confirm(`删除会话「${item.title}」？关联的工作任务将停止。`)) return;
        await deleteConversation(item.id);
        const remaining = await listConversations(character!.id, '');
        setConversations(remaining);
        if (activeId === item.id) {
          ++openTokenRef.current;
          stopAudio();
          setActiveId(null);
          setMessages([]);
          setBranches([]);
          setReplyTo(null);
          setInput('');
          setLoading(false);
          setGeneratingId(null);
          setWorkSession(null);
          setWorkMessages([]);
          setPendingWork(null);
          if (remaining.length) await openConversation(remaining[0].id);
          else {
            agentModeRef.current = 'chat';
            setAgentMode('chat');
            window.setTimeout(() => composerRef.current?.focus(), 0);
          }
        }
        return;
      } else {
        const branch = await createBranch(item.id, null);
        await openConversation(branch.id);
      }
      await refreshList(character!.id, search);
    } catch (cause) {
      setError(errorText(cause));
    }
  }
  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void submit();
    }
  }
  async function addFiles(files: FileList | null) {
    if (!files) return;
    setAttachmentMenu(false);
    for (const file of Array.from(files)) {
      if (file.size > 8 * 1024 * 1024) {
        setError(`${file.name} 超过 8 MB`);
        continue;
      }
      if (attachments.length + Array.from(files).length > 4) {
        setError('每次最多添加 4 个附件');
        break;
      }
      try {
        const data = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result));
          reader.onerror = () => reject(new Error('读取文件失败'));
          reader.readAsDataURL(file);
        });
        const kind = file.type.startsWith('image/') ? 'image' : 'file';
        setAttachments((items) => [
          ...items,
          {
            name: file.name,
            data_base64: data.split(',')[1],
            preview: kind === 'image' ? data : undefined,
            kind,
            size: file.size,
          },
        ]);
      } catch (cause) {
        setError(`添加附件失败：${errorText(cause)}`);
      }
    }
  }
  async function selectWorkFolder() {
    setAttachmentMenu(false);
    try {
      if (
        workSession &&
        !['idle', 'done', 'completed', 'failed', 'stopped', 'cancelled'].includes(
          workSession.status,
        )
      ) {
        setError('请先停止当前任务，再更换工作文件夹并重新加载上下文。');
        return;
      }
      let conversationId = activeId;
      if (!conversationId) {
        const row = await createConversation(character!.id, 'work');
        conversationId = row.id;
        activeIdRef.current = row.id;
        setActiveId(row.id);
        await refreshList(character!.id);
      }
      const folder = await window.desktopAPI?.selectWorkFolder(conversationId);
      if (folder) {
        setWorkFolder(folder);
        setWorkSession(await loadWorkForConversation(conversationId));
      }
    } catch (cause) {
      setError(`选择工作文件夹失败：${errorText(cause)}`);
    }
  }
  async function chooseApplication(appId: string) {
    if (concurrentRuntime && pendingApps) {
      await submissionRef.current
        ?.select(pendingApps.plan_id, appId, submission(''))
        .catch((cause) => setError(errorText(cause)));
      return;
    }
    if (!pendingApps || busyRef.current || workRunningRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError('');
    try {
      const plan = await selectApplication(pendingApps.plan_id, appId);
      setPendingApps(null);
      if (plan.work_session_id) setWorkSession(await loadWorkSession(plan.work_session_id));
      if (plan.calls.some((call) => call.requires_confirmation)) {
        setPendingWork({
          planId: plan.plan_id,
          calls: plan.calls,
          approved: [],
          conversationId: plan.conversation_id,
        });
        setAgentStatus('等待操作授权');
      } else {
        workRunningRef.current = true;
        setWorkRunning(true);
        setRunningConversation(plan.conversation_id);
        try {
          await finishWork(plan.plan_id, [], plan.conversation_id);
        } finally {
          workRunningRef.current = false;
          setWorkRunning(false);
          setRunningConversation(null);
        }
      }
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function selectAvatar() {
    try {
      const url = await window.desktopAPI?.selectAppearance('avatar');
      if (url) setCustomAvatar(url);
    } catch (cause) {
      setError(`选择头像失败：${errorText(cause)}`);
    }
  }
  async function useCharacter(profile: import('./api').CharacterCard) {
    const changing = character?.id !== profile.id;
    const base = (await window.desktopAPI?.getBackendUrl()) ?? 'http://127.0.0.1:8000';
    setCharacter({ ...profile, identity: profile.profile.identity });
    setCustomAvatar(profile.avatar_url ? base + profile.avatar_url : null);
    characterIdRef.current = profile.id;
    if (changing) {
      await newChat();
      rawSetInput('');
      setSearch('');
      setPendingApps(null);
      setPanel('character');
    }
    await refreshList(profile.id);
  }
  async function testVoiceSample() {
    const started = performance.now();
    setVoiceTestStatus('正在生成测试语音…');
    try {
      const url = await testVoice();
      await playbackRef.current!.play('voice-test', url, volume / 100);
      setVoiceTestStatus(`生成成功（${Math.round(performance.now() - started)} ms），播放已启动`);
    } catch (cause) {
      setVoiceTestStatus(`测试失败：${errorText(cause)}`);
    }
  }
  const renderMessage = (message: ChatMessage) => (
    <MessageItem
      key={message.id}
      message={message}
      name={character?.name ?? '通用助手'}
      avatarSrc={avatarSrc}
      pendingText={agentStatus}
      toolDetails={agentMode === 'agent' ? undefined : agentDetails[message.id]}
      playing={playingId === message.id}
      generating={generatingId === message.id || (message.voice_status === 'generating' && busy)}
      voiceProgress={voiceProgress[message.id]}
      voiceError={voiceErrors[message.id]}
      disabled={busy}
      onReply={setReplyTo}
      onRetry={(row) => void retry(row)}
      onBranch={(row) => void branchAt(row)}
      onPlay={(row) => void playVoice(row)}
      onRegenerateVoice={(row) => void playVoice(row, true)}
      onEdit={(row) => void branchAt(row, true)}
    />
  );
  const active = conversations.find((row) => row.id === activeId);
  const branchIndex = branches.findIndex((row) => row.id === activeId);
  return (
    <div
      className={`app-shell ${agentMode === 'agent' ? 'work-active' : ''} ${sidebarOpen ? '' : 'sidebar-collapsed'}`}
    >
      <aside className="sidebar">
        <div className="sidebar-top">
          <button
            type="button"
            className="sidebar-avatar-button"
            aria-label="打开角色管理"
            title="管理角色"
            onClick={() => {
              stopAudio();
              setPanel('character');
            }}
          >
            <img className="sidebar-avatar" src={avatar} alt="Companion Studio 图标" />
          </button>
          <div className="sidebar-identity">
            <strong>Companion Studio</strong>
            <span>{character?.name || '通用助手'}</span>
            <PresenceIndicator presence={presence} />
          </div>
        </div>
        <nav className="primary-nav" aria-label="主导航">
          <button
            className={!panel && !musicOpen && agentMode === 'chat' ? 'active' : ''}
            onClick={() => switchMode('chat')}
          >
            对话
          </button>
          <button
            className={!panel && !musicOpen && agentMode === 'agent' ? 'active' : ''}
            onClick={() => switchMode('agent')}
          >
            工作
          </button>
        </nav>
        <button className="new-chat" type="button" onClick={() => void newChat()}>
          <Icon name="plus" />
          <span>新聊天</span>
        </button>
        <div className="search-box">
          <Icon name="search" />
          <input
            aria-label="搜索聊天"
            placeholder="搜索聊天"
            value={search}
            onChange={(event) => {
              const term = event.target.value;
              setSearch(term);
              if (character)
                void refreshList(character.id, term).catch((cause) => setError(errorText(cause)));
            }}
          />
        </div>
        <ConversationSidebar
          rows={conversations}
          active={activeId}
          running={concurrentRuntime ? runtime.running() : runningConversation}
          completed={Object.fromEntries(
            conversations
              .filter((row) => row.id !== activeId && runtime.get(row.id).completed)
              .map((row) => [row.id, runtime.get(row.id).error ? '失败' : '已完成']),
          )}
          onOpen={(id) => void openConversation(id)}
          onManage={(row, action) => void chatAction(row, action)}
        />
        <div className="sidebar-bottom">
          <button
            type="button"
            onClick={() => {
              stopAudio();
              setPanel('character');
            }}
          >
            <img src={avatarSrc} alt="" />
            <span>角色管理</span>
          </button>
          <button type="button" onClick={() => setPanel('settings')}>
            <Icon name="settings" />
            <span>设置</span>
          </button>
        </div>
      </aside>
      <main
        className={`chat-panel ${agentMode === 'agent' ? 'work-mode' : ''} ${!loading && messages.length === 0 ? 'is-empty' : ''}`}
      >
        <header className="chat-header">
          <div className="chat-title">
            <button
              className="icon-action sidebar-toggle"
              title="切换侧栏"
              aria-label="切换侧栏"
              onClick={() => setSidebarOpen(!sidebarOpen)}
            >
              <Icon name="panel" />
            </button>
            <div>
              <strong>
                {panel === 'settings'
                  ? '设置'
                  : panel === 'character'
                    ? '角色管理'
                    : musicOpen
                      ? '音乐中心'
                      : agentMode === 'agent'
                        ? '工作台'
                        : (active?.title ?? '通用助手')}
              </strong>
              <span>{agentMode === 'agent' ? '任务、应用和文件协作' : '陪伴聊天'}</span>
            </div>
          </div>
          <div className="mode-switch" role="group" aria-label="使用模式">
            <button
              type="button"
              className={agentMode === 'chat' ? 'active' : ''}
              onClick={() => switchMode('chat')}
            >
              聊天模式
            </button>
            <button
              type="button"
              className={agentMode === 'agent' ? 'active' : ''}
              onClick={() => switchMode('agent')}
            >
              工作模式
            </button>
          </div>
          {agentMode === 'chat' && workRunning && (
            <span className="work-background-status" role="status">
              工作任务仍在进行
            </span>
          )}
          {branches.length > 1 && (
            <div className="branch-switcher">
              <span>
                分支 {branchIndex + 1}/{branches.length}
              </span>
              <select
                aria-label="切换分支"
                value={activeId ?? ''}
                onChange={(event) => void openConversation(event.target.value)}
              >
                {branches.map((branch, index) => (
                  <option value={branch.id} key={branch.id}>
                    分支 {index + 1}
                  </option>
                ))}
              </select>
            </div>
          )}
          <WindowControls />
        </header>
        {desktopActionStatus && (
          <div className="desktop-action-status" role="status">
            <span>{desktopActionStatus}</span>
            <button
              type="button"
              onClick={() => setDesktopActionStatus('')}
              aria-label="关闭应用状态"
            >
              ×
            </button>
          </div>
        )}
        <div
          className={musicOpen || panel ? 'mode-content mode-hidden' : 'mode-content'}
          aria-hidden={!!(musicOpen || panel)}
        >
          {agentMode === 'agent' ? (
            <Workbench
              key={activeId || 'new-work'}
              conversationId={activeId}
              visible={!musicOpen && !panel}
              session={workSession}
              pending={
                pendingWork ? { calls: pendingWork.calls, approved: pendingWork.approved } : null
              }
              messages={messages}
              renderMessage={renderMessage}
              onApproval={(id, allow) =>
                setPendingWork((current) => {
                  const next = current
                    ? {
                        ...current,
                        approved: allow
                          ? [...current.approved, id]
                          : current.approved.filter((item) => item !== id),
                      }
                    : null;
                  if (concurrentRuntime) runtime.get(draftKey()).pending = next;
                  return next;
                })
              }
              onExecute={() => void executePendingWork()}
              onStop={() => {
                if (concurrentRuntime && activeId)
                  void submissionRef.current
                    ?.stop(activeId)
                    .catch((cause) => setError(errorText(cause)));
                else if (workSession)
                  void stopWorkSession(workSession.id)
                    .then(() => loadWorkSession(workSession.id))
                    .then(setWorkSession)
                    .catch((cause) => setError(errorText(cause)));
                setPendingWork(null);
              }}
              onError={setError}
              onStatus={setDesktopActionStatus}
            />
          ) : (
            <section className="conversation" aria-label="聊天记录" aria-live="polite">
              <div className="conversation-inner">
                {loading && <p className="hint">正在加载聊天记录…</p>}
                {!loading && messages.length === 0 && (
                  <div className="welcome">
                    <img src={avatarSrc} alt="" />
                    <h2>
                      {character?.id === 'assistant'
                        ? '欢迎使用伴舟'
                        : `和${character?.name || '助手'}开始对话`}
                    </h2>
                    <p>
                      {character?.welcome_message || '输入消息开始对话，也可以在工作模式完成任务。'}
                    </p>
                    <div className="character-actions welcome-actions">
                      <button type="button" onClick={() => setPanel('character')}>
                        导入角色卡或自定义角色
                      </button>
                      <button type="button" onClick={() => setPanel('settings')}>
                        配置模型 API
                      </button>
                    </div>
                  </div>
                )}
                {messages.map(renderMessage)}
                <div ref={endRef} />
              </div>
            </section>
          )}
          {concurrentRuntime && runtime.get(draftKey()).queued > 0 && (
            <p className="hint" role="status">
              本会话有 {runtime.get(draftKey()).queued} 条消息等待回复；可继续输入或切换会话。
            </p>
          )}
          <form className="composer" onSubmit={(event) => void submit(event)}>
            {pendingApps && pendingApps.conversation_id === activeId && (
              <div className="app-candidates" role="group" aria-label="候选应用">
                <span>{pendingApps.local_resolution?.error || '请选择要操作的应用'}</span>
                {pendingApps.local_resolution?.candidates?.map((app) => (
                  <WorkbenchActionButton
                    key={app.app_id}
                    disabled={busy || (!concurrentRuntime && workRunning)}
                    onClick={() => void chooseApplication(app.app_id)}
                  >
                    {app.name}
                  </WorkbenchActionButton>
                ))}
              </div>
            )}
            {error && (
              <p className="error" role="alert">
                {error}
                <button type="button" onClick={() => setError('')} aria-label="关闭错误">
                  <Icon name="close" size={14} />
                </button>
              </p>
            )}
            {replyTo && (
              <div className="reply-preview">
                <span>
                  回复 {character?.name || '助手'} · {replyTo.content.slice(0, 56)}
                </span>
                <button type="button" onClick={() => setReplyTo(null)} aria-label="取消回复">
                  <Icon name="close" size={14} />
                </button>
              </div>
            )}

            <div className="composer-box">
              <input
                ref={imageInputRef}
                className="visually-hidden"
                type="file"
                accept="image/png,image/jpeg,image/webp,image/gif"
                multiple
                onChange={(event) => {
                  void addFiles(event.target.files);
                  event.target.value = '';
                }}
              />
              <input
                ref={fileInputRef}
                className="visually-hidden"
                type="file"
                accept=".txt,.md,.pdf,.docx,.csv,.json,.py,.js,.ts,.tsx,.jsx,.html,.css,.xml,.yaml,.yml,.log,.toml"
                multiple
                onChange={(event) => {
                  void addFiles(event.target.files);
                  event.target.value = '';
                }}
              />
              {attachments.length > 0 && (
                <div className="attachment-list">
                  {attachments.map((item, index) => (
                    <div className="attachment-chip" key={`${item.name}-${index}`}>
                      {item.preview && <img src={item.preview} alt="" />}
                      <span>
                        {item.name}
                        <small>{Math.ceil((item.size ?? 0) / 1024)} KB</small>
                      </span>
                      <button
                        type="button"
                        aria-label={`移除${item.name}`}
                        onClick={() =>
                          setAttachments((items) => items.filter((_, i) => i !== index))
                        }
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>
              )}

              <textarea
                ref={composerRef}
                aria-label="输入消息"
                value={input}
                maxLength={4000}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={onKeyDown}
                placeholder={agentMode === 'agent' ? '描述需要完成的工作…' : '输入消息…'}
                disabled={!character}
                rows={2}
              />
              <div className="composer-footer">
                <div className="composer-tools">
                  <ComposerActionButton
                    icon="plus"
                    className="composer-add"
                    aria-label="添加附件"
                    title="添加附件"
                    onClick={() => setAttachmentMenu(!attachmentMenu)}
                  />
                  <VoiceInputButton
                    disabled={!character || (!concurrentRuntime && busy)}
                    onActivate={() => {
                      composerRef.current?.focus();
                      setDesktopActionStatus('输入框已就绪，请按 Win + H 使用 Windows 语音输入。');
                    }}
                  />
                  {agentMode === 'agent' && (
                    <FolderChip
                      root={workFolder}
                      disabled={
                        !!workSession &&
                        !['idle', 'completed', 'done', 'failed', 'stopped', 'cancelled'].includes(
                          workSession.status,
                        )
                      }
                      onSelect={() => void selectWorkFolder()}
                      onOpen={() => {
                        if (workSession)
                          void window.desktopAPI
                            ?.openWorkFolder(workSession.id)
                            .catch((e) => setError(String(e)));
                      }}
                      onClear={() => {
                        if (activeId)
                          void window.desktopAPI
                            ?.clearWorkFolder(activeId)
                            .then((row) => {
                              setWorkFolder(null);
                              setWorkSession(row);
                            })
                            .catch((e) => setError(String(e)));
                      }}
                      onError={setError}
                    />
                  )}
                  {attachmentMenu && (
                    <div className="attachment-menu">
                      <button type="button" onClick={() => imageInputRef.current?.click()}>
                        添加图片
                      </button>
                      <button type="button" onClick={() => fileInputRef.current?.click()}>
                        添加文件
                      </button>
                      {agentMode === 'agent' && (
                        <button type="button" onClick={() => void selectWorkFolder()}>
                          选择项目文件夹
                        </button>
                      )}
                    </div>
                  )}
                </div>
                <span className="composer-hint">所选附件会随消息发送给当前模型</span>
                <button
                  type="submit"
                  aria-label="发送"
                  disabled={
                    !character ||
                    (!concurrentRuntime &&
                      (busy ||
                        (agentMode === 'agent' &&
                          workRunning &&
                          runningConversation === activeId))) ||
                    (!input.trim() && !attachments.length)
                  }
                >
                  <Icon name="send" size={18} />
                </button>
              </div>
            </div>
          </form>
        </div>
        <MusicCenter
          expanded={musicOpen && !panel}
          onOpen={() => {
            setPanel(null);
            setMusicOpen(true);
          }}
          onClose={() => setMusicOpen(false)}
        />

        <FirstRunGuide refresh={panel} onConfigure={() => setPanel('settings')} />
        {panel === 'settings' && (
          <AppSettings
            onBack={() => setPanel(null)}
            onAvatar={() => void selectAvatar()}
            onPlay={(url) => playbackRef.current!.play('fish-auth-test', url, volume / 100)}
            general={{ theme, setTheme, startup, setStartup }}
            chat={{ fontSize, setFontSize, autoScroll, setAutoScroll }}
            voice={{
              enabled: voiceEnabled,
              setEnabled: (value) => {
                setVoiceEnabled(value);
                if (!value) stopAudio();
              },
              prefetch: voicePrefetch,
              setPrefetch: setVoicePrefetch,
              volume,
              setVolume,
              expression,
              setExpression,
              speed,
              setSpeed,
              status: voiceStatus,
              testStatus: voiceTestStatus,
            }}
            cache={cacheInfo}
            health={voiceHealthInfo}
            playback={playbackDiagnostic}
            fileDiagnostic={voiceFileDiagnostic}
            onTestVoice={() => void testVoiceSample()}
            onClearCache={() => void clearCache()}
            onRestart={() =>
              void restartVoiceWorker()
                .then(() => setVoiceHealthInfo('语音进程正在重新加载…'))
                .catch((cause) => setVoiceHealthInfo(errorText(cause)))
            }
          />
        )}
        {panel === 'character' && (
          <CharacterPage
            name={character?.name}
            identity={character?.identity}
            id={character?.id || 'assistant'}
            avatar={avatarSrc}
            tagline={character?.tagline}
            presence={presence}
            onBack={() => setPanel(null)}
            onActive={useCharacter}
            onAvatar={() => void selectAvatar()}
            voice={
              <FishSettingsPanel
                onPlay={(url) => playbackRef.current!.play('fish-profile-test', url, volume / 100)}
              />
            }
          />
        )}
      </main>
    </div>
  );
}
