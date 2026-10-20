export type VoiceIdentity = {
  voice_session_id: string;
  owner_id: string;
  conversation_id: string;
  character_id: string;
  state: string;
  turn_id: string | null;
  generation_id: string | null;
  audio_stream_id: string | null;
  turn_state: string | null;
  trace_id: string | null;
};
export type VoiceBinding = Pick<VoiceIdentity, 'voice_session_id'> & {
  turn_id: string;
  generation_id: string;
};
export type VoiceOptions = {
  conversation_id: string;
  character_id: string;
  language: 'zh-CN' | 'en-US';
  tts_provider: 'windows-sapi' | 'configured';
  voice_id: string;
  speaking_rate: number;
  remote_tts_consent: boolean;
};
export type VoiceCapabilities = {
  local: {
    status: string;
    native_streaming_asr: boolean;
    recognizers: string[];
    voices: { id: string; language: string }[];
  };
  configured_tts: { provider_id: string; available: boolean; requires_network: boolean };
  hands_free: boolean;
  voice_barge_in: boolean;
};
export type CapturePacket = {
  sequence: number;
  sample_rate: number;
  channels: 1;
  sample_format: 'pcm_s16le';
  frame_duration_ms: number;
  timestamp: number;
  pcm_base64: string;
};
export type VoiceControl = 'begin' | 'frame' | 'finalize' | 'interrupt' | 'event' | 'stop';
export type VoiceView = {
  identity: VoiceIdentity | null;
  capturing: boolean;
  busy: boolean;
  transcript: string;
  error: string;
  duration: number;
};
