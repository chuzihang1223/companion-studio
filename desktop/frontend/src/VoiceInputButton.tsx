import ComposerActionButton from './ComposerActionButton';
export type VoiceInputState = 'idle' | 'recording' | 'transcribing' | 'error';
/** Windows dictation remains the input engine; recording state is never guessed. */
export default function VoiceInputButton({
  state = 'idle',
  disabled = false,
  onActivate,
}: {
  state?: VoiceInputState;
  disabled?: boolean;
  onActivate: () => void;
}) {
  const label = {
    idle: '语音输入',
    recording: '正在录音，点击停止',
    transcribing: '正在转写',
    error: '语音输入暂不可用',
  }[state];
  return (
    <ComposerActionButton
      icon={state === 'error' ? 'alert' : state === 'transcribing' ? 'retry' : 'wave'}
      className={`voice-input-button ${state}`}
      title={state === 'idle' ? '使用 Windows 语音输入（Win + H）' : label}
      aria-label={label}
      aria-pressed={state === 'recording'}
      disabled={disabled || state === 'transcribing'}
      onClick={onActivate}
    />
  );
}
