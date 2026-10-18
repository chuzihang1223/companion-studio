import { useEffect, useState } from 'react';
import type { ChatMessage } from './api';
import Icon from './Icon';
import logo from './assets/companion-logo.svg';
import { ClipboardService } from './ClipboardService';
import MessageContent from './MessageContent';
import MessageSources from './knowledge/KnowledgeSources';

type Props = {
  message: ChatMessage;
  name: string;
  avatarSrc?: string;
  pendingText?: string;
  toolDetails?: Array<{ name: string; status: string; result: Record<string, unknown> }>;
  playing: boolean;
  generating: boolean;
  voiceProgress?: string;
  voiceError?: string;
  disabled: boolean;
  onReply: (message: ChatMessage) => void;
  onRetry: (message: ChatMessage) => void;
  onBranch: (message: ChatMessage) => void;
  onPlay: (message: ChatMessage) => void;
  onRegenerateVoice: (message: ChatMessage) => void;
  onEdit: (message: ChatMessage) => void;
};

export default function MessageItem(props: Props) {
  const { message, name } = props;
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const [more, setMore] = useState(false);
  const [backendUrl, setBackendUrl] = useState('http://127.0.0.1:8000');
  useEffect(() => {
    void window.desktopAPI?.getBackendUrl().then(setBackendUrl);
  }, []);
  const pending = message.id.startsWith('pending-');
  async function copy() {
    try {
      await ClipboardService.writeText(message.content);
      setCopyFailed(false);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopyFailed(true);
      window.setTimeout(() => setCopyFailed(false), 1600);
    }
  }
  const action = (
    title: string,
    icon: Parameters<typeof Icon>[0]['name'],
    onClick: () => void,
    disabled = false,
  ) => (
    <button
      className="icon-action"
      type="button"
      title={title}
      aria-label={title}
      onClick={onClick}
      disabled={disabled}
    >
      <Icon name={icon} />
    </button>
  );
  return (
    <article
      className={`message-row ${message.role}`}
      onContextMenu={(event) => {
        event.preventDefault();
        event.stopPropagation();
        const selectedText = window.getSelection()?.toString() || '';
        void window.desktopAPI
          ?.showContextMenu(message.role, {
            selectedText,
            text: message.content,
            hasSelection: !!selectedText,
            disabled: pending || props.disabled,
            x: event.clientX,
            y: event.clientY,
          })
          .then((action) => {
            if (action === 'reply') props.onReply(message);
            if (action === 'regenerate') props.onRetry(message);
            if (action === 'play') props.onPlay(message);
            if (action === 'branch') props.onBranch(message);
            if (action === 'edit') props.onEdit(message);
          });
      }}
    >
      {message.role === 'assistant' && (
        <img className="message-avatar" src={props.avatarSrc ?? logo} alt="" />
      )}
      <div className="message-body">
        {message.role === 'assistant' && <span className="message-name">{name}</span>}
        <div className="bubble">
          <MessageContent
            text={message.content || (pending ? props.pendingText || `${name}正在想…` : '')}
          />
        </div>
        {message.role === 'assistant' && !pending && !props.disabled && message.conversation_id && (
          <MessageSources
            messageId={message.id}
            owner={{ conversation_id: message.conversation_id, character_id: message.character_id }}
          />
        )}
        {!!message.attachments?.length && (
          <div className="message-attachments">
            {message.attachments.map((item) => (
              <span className="message-attachment" key={item.id}>
                {item.kind === 'image' ? (
                  <img
                    src={`${backendUrl}/api/attachments/${message.id}/${item.id}`}
                    alt={item.name}
                  />
                ) : (
                  '▤'
                )}
                {item.name}
              </span>
            ))}
          </div>
        )}
        {props.toolDetails && (
          <details className="agent-tool-card">
            <summary>已执行 {props.toolDetails.length} 项操作</summary>
            {props.toolDetails.map((item, index) => (
              <div key={`${item.name}-${index}`}>
                <strong>{item.name}</strong> · {item.status}
                <pre>{JSON.stringify(item.result, null, 2)}</pre>
              </div>
            ))}
          </details>
        )}
        {!pending && (
          <div className="message-actions">
            {action(
              copyFailed ? '复制失败，请重试' : copied ? '已复制' : '复制',
              copied ? 'check' : 'copy',
              () => {
                void copy();
              },
            )}
            {message.role === 'assistant' ? (
              <>
                {action('回复', 'reply', () => props.onReply(message))}
                {action('重新生成回复', 'retry', () => props.onRetry(message), props.disabled)}
                {action('从这里创建分支', 'branch', () => props.onBranch(message), props.disabled)}
                {action(
                  props.playing
                    ? '暂停语音'
                    : message.voice_status === 'failed'
                      ? `生成失败 · 点击重试${props.voiceError ? `：${props.voiceError}` : ''}`
                      : props.voiceProgress || '播放语音',
                  props.playing
                    ? 'pause'
                    : message.voice_status === 'failed'
                      ? 'alert'
                      : message.voice_status === 'queued'
                        ? 'clock'
                        : message.voice_status === 'loading' ||
                            message.voice_status === 'synthesizing'
                          ? 'retry'
                          : message.voice_status === 'partial'
                            ? 'wave'
                            : 'volume',
                  () => props.onPlay(message),
                )}
                <div className="more-wrap">
                  {action('更多', 'more', () => setMore(!more))}
                  {more && (
                    <div className="more-menu">
                      <button
                        type="button"
                        onClick={() => {
                          void copy();
                          setMore(false);
                        }}
                      >
                        复制文本
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          props.onRegenerateVoice(message);
                          setMore(false);
                        }}
                      >
                        重新生成语音
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setMore(false);
                          window.alert(new Date(message.created_at).toLocaleString());
                        }}
                      >
                        查看时间
                      </button>
                    </div>
                  )}
                </div>
              </>
            ) : (
              action('编辑并创建分支', 'edit', () => props.onEdit(message), props.disabled)
            )}
            {props.playing && <span className="playing-mark" aria-label="正在播放" />}
            {message.variant_of_message_id && <span className="variant-label">另一种回复</span>}
          </div>
        )}
      </div>
    </article>
  );
}
