import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from 'react';
import {
  loadCharacter,
  loadHistory,
  loadVoiceStatus,
  streamMessage,
  type CharacterInfo,
  type ChatMessage,
  type ExpressionLevel,
} from './api';
import logo from './assets/companion-logo.svg';
import './style.css';

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败，请稍后重试。';
}

function voiceLabel(status: unknown, engine: unknown): string {
  if (status === 'preparing') return '正在准备声音…';
  if (status !== 'online') return '语音暂不可用';
  return engine === 'indextts-2.5-expressive' ? '情境语音已就绪' : '基础语音已就绪';
}

function savedExpression(): ExpressionLevel {
  const value = localStorage.getItem('voice-expression');
  return value === 'restrained' || value === 'rich' ? value : 'natural';
}

function wavUrl(base64: string): string {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return URL.createObjectURL(new Blob([bytes.buffer], { type: 'audio/wav' }));
}

export default function App() {
  const [character, setCharacter] = useState<CharacterInfo | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [error, setError] = useState('');
  const [voiceEnabled, setVoiceEnabled] = useState(
    () => localStorage.getItem('voice-enabled') !== 'false',
  );
  const voiceEnabledRef = useRef(voiceEnabled);
  const [expression, setExpression] = useState<ExpressionLevel>(savedExpression);
  const [voiceStatus, setVoiceStatus] = useState('');
  const [debugSpeech, setDebugSpeech] = useState('');
  const busyRef = useRef(false);
  const endRef = useRef<HTMLDivElement>(null);
  const audioQueueRef = useRef<Array<{ url: string; pauseBefore: number; pauseAfter: number }>>([]);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const currentUrlRef = useRef<string | null>(null);
  const audioTimerRef = useRef<number | null>(null);

  const playNext = useCallback(function playNextAudio() {
    if (currentAudioRef.current) return;
    const next = audioQueueRef.current.shift();
    if (!next) return;
    const player = new Audio(next.url);
    currentAudioRef.current = player;
    currentUrlRef.current = next.url;
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      player.onended = null;
      player.onerror = null;
      currentAudioRef.current = null;
      currentUrlRef.current = null;
      URL.revokeObjectURL(next.url);
      audioTimerRef.current = window.setTimeout(playNextAudio, next.pauseAfter);
    };
    player.onended = finish;
    player.onerror = finish;
    const wait = next.pauseBefore;
    if (wait)
      audioTimerRef.current = window.setTimeout(() => {
        void player.play().catch(finish);
      }, wait);
    else void player.play().catch(finish);
  }, []);

  const stopAudio = useCallback(() => {
    if (audioTimerRef.current !== null) window.clearTimeout(audioTimerRef.current);
    audioTimerRef.current = null;
    if (currentAudioRef.current) {
      currentAudioRef.current.onended = null;
      currentAudioRef.current.onerror = null;
      currentAudioRef.current.pause();
      currentAudioRef.current = null;
    }
    if (currentUrlRef.current) URL.revokeObjectURL(currentUrlRef.current);
    currentUrlRef.current = null;
    for (const item of audioQueueRef.current) URL.revokeObjectURL(item.url);
    audioQueueRef.current = [];
  }, []);

  const refresh = useCallback(async (characterId: string) => {
    const history = await loadHistory(characterId);
    setMessages(history);
  }, []);

  useEffect(() => {
    let active = true;
    loadCharacter()
      .then(async (profile) => {
        if (!active) return;
        setCharacter(profile);
        await refresh(profile.id);
      })
      .catch((cause) => {
        if (active) setError(`加载角色或聊天记录失败：${errorText(cause)}`);
      })
      .finally(() => {
        if (active) setLoadingHistory(false);
      });
    return () => {
      active = false;
    };
  }, [refresh]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, busy]);

  useEffect(() => {
    let active = true;
    const check = () => {
      void loadVoiceStatus()
        .then(({ status, engine }) => {
          if (!active) return;
          setVoiceStatus(voiceLabel(status, engine));
        })
        .catch(() => {
          if (active) setVoiceStatus('语音暂不可用');
        });
    };
    check();
    const timer = window.setInterval(check, 3000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => () => stopAudio(), [stopAudio]);

  useEffect(() => {
    localStorage.setItem('voice-expression', expression);
    localStorage.setItem('voice-enabled', String(voiceEnabled));
  }, [expression, voiceEnabled]);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const text = input.trim();
    if (!text || busyRef.current || !character) return;
    stopAudio();
    busyRef.current = true;
    setBusy(true);
    setError('');
    setInput('');
    const draft: ChatMessage = {
      id: `pending-${Date.now()}`,
      character_id: character.id,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    };
    const assistantDraftId = `pending-assistant-${Date.now()}`;
    setMessages((current) => [
      ...current,
      draft,
      {
        id: assistantDraftId,
        character_id: character.id,
        role: 'assistant',
        content: '',
        created_at: new Date().toISOString(),
      },
    ]);
    try {
      for await (const item of streamMessage(character.id, text, expression, voiceEnabled)) {
        if (item.event === 'text_delta' && typeof item.data.text === 'string') {
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantDraftId
                ? { ...message, content: message.content + item.data.text }
                : message,
            ),
          );
        } else if (item.event === 'completed') {
          const replyId = item.data.message_id;
          const content = item.data.content;
          if (typeof replyId === 'string' && typeof content === 'string') {
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantDraftId ? { ...message, id: replyId, content } : message,
              ),
            );
          }
        } else if (
          item.event === 'audio_chunk' &&
          typeof item.data.audio_base64 === 'string' &&
          voiceEnabledRef.current
        ) {
          audioQueueRef.current.push({
            url: wavUrl(item.data.audio_base64),
            pauseBefore:
              typeof item.data.pause_before_ms === 'number' ? item.data.pause_before_ms : 0,
            pauseAfter:
              typeof item.data.pause_after_ms === 'number' ? item.data.pause_after_ms : 100,
          });
          playNext();
        } else if (item.event === 'voice_status') {
          setVoiceStatus(voiceLabel(item.data.status, item.data.engine));
        } else if (item.event === 'speech_plan' && import.meta.env.DEV) {
          setDebugSpeech(
            `${String(item.data.text ?? '')} · ${String(item.data.intent ?? '')} · ${String(item.data.emotion ?? '')} · ${String(item.data.engine ?? '')}`,
          );
        } else if (item.event === 'error') {
          throw new Error(typeof item.data.detail === 'string' ? item.data.detail : '回复生成失败');
        }
      }
      await refresh(character.id).catch(() => {
        /* Keep the reply visible until the next load. */
      });
    } catch (cause) {
      setError(`发送失败：${errorText(cause)}`);
      try {
        await refresh(character.id);
      } catch {
        /* Keep the visible draft if history is unavailable. */
      }
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void submit();
    }
  }

  const displayName = character?.name ?? '通用助手';
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <img className="avatar" src={logo} alt="应用图标" />
        <h1>{displayName}</h1>
        <p>{character?.tagline ?? '由你定义的聊天与工作空间'}</p>
        <span className="sidebar-caption">{character?.english_name ?? 'Companion Studio'}</span>
      </aside>
      <main className="chat-panel">
        <header className="chat-header">
          <strong>{displayName}</strong>
          <span>{voiceEnabled ? voiceStatus || '语音已开启' : '文字聊天'}</span>
        </header>
        <section className="conversation" aria-label="聊天记录" aria-live="polite">
          {loadingHistory && <p className="hint">正在加载聊天记录…</p>}
          {!loadingHistory && character && messages.length === 0 && (
            <p className="hint">{character.welcome_message}</p>
          )}
          {messages.map((message) => (
            <div className={`message-row ${message.role}`} key={message.id}>
              <div className="bubble">
                {message.content ||
                  (message.id.startsWith('pending-assistant') ? `${displayName}正在回复…` : '')}
              </div>
            </div>
          ))}
          <div ref={endRef} />
        </section>
        <form className="composer" onSubmit={(event) => void submit(event)}>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <div className="voice-settings">
            <label>
              <input
                type="checkbox"
                checked={voiceEnabled}
                onChange={(event) => {
                  voiceEnabledRef.current = event.target.checked;
                  setVoiceEnabled(event.target.checked);
                  if (!event.target.checked) stopAudio();
                }}
              />{' '}
              播放语音
            </label>
            <label>
              情感表现{' '}
              <select
                value={expression}
                onChange={(event) => setExpression(event.target.value as ExpressionLevel)}
                disabled={!voiceEnabled}
              >
                <option value="restrained">克制</option>
                <option value="natural">自然</option>
                <option value="rich">丰富</option>
              </select>
            </label>
          </div>
          {import.meta.env.DEV && debugSpeech && <p className="voice-debug">{debugSpeech}</p>}
          <div className="composer-row">
            <textarea
              aria-label="输入消息"
              value={input}
              maxLength={4000}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={onKeyDown}
              placeholder="输入消息，Enter 发送，Shift + Enter 换行"
              disabled={busy || !character}
              rows={2}
            />
            <button type="submit" disabled={busy || !character || !input.trim()}>
              发送
            </button>
          </div>
        </form>
      </main>
    </div>
  );
}
