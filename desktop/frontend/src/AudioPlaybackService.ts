import { BufferedAudioQueue } from './BufferedAudioQueue';
export type PlaybackDiagnostic = {
  voiceId: string;
  url: string;
  httpStatus?: number;
  contentType?: string | null;
  contentLength?: string | null;
  mediaError?: number | null;
  readyState?: number;
  networkState?: number;
  playResult?: string;
  detail?: string;
};

type PlaybackListener = (voiceId: string | null, diagnostic: PlaybackDiagnostic | null) => void;

/** 协调当前回复/试听的播放所有权；支持连续 PCM、原生播放与浏览器回退。 */
export class AudioPlaybackService {
  private audio: HTMLAudioElement | null = null;
  private objectUrl: string | null = null;
  private voiceId: string | null = null;
  private sequence = 0;
  private listener: PlaybackListener;
  private ended: () => void;
  private native = false;
  private nativePaused = false;
  private nativeTicker: number | null = null;
  private nativeStart: Promise<void> | null = null;
  private pendingStop: Promise<unknown> = Promise.resolve();
  private pcmContext: AudioContext | null = null;
  private pcmEnd = 0;
  private pcmStarted = false;
  private pcmTimer: number | null = null;
  private pcmStartTimer: number | null = null;
  private pcmFinished = false;
  private pcmUserPaused = false;
  private buffered: BufferedAudioQueue | null = null;

  constructor(listener: PlaybackListener, ended: () => void = () => {}) {
    this.listener = listener;
    this.ended = ended;
  }

  get currentId() {
    return this.voiceId;
  }
  get paused() {
    return this.buffered
      ? this.buffered.paused
      : this.pcmContext
        ? this.pcmContext.state === 'suspended'
        : this.native
          ? this.nativePaused
          : (this.audio?.paused ?? true);
  }

  private report(diagnostic: PlaybackDiagnostic) {
    diagnostic.mediaError = this.audio?.error?.code ?? null;
    diagnostic.readyState = this.audio?.readyState;
    diagnostic.networkState = this.audio?.networkState;
    console.info('Voice playback diagnostic', diagnostic);
    this.listener(this.voiceId, diagnostic);
  }

  stop() {
    this.sequence++;
    if (this.buffered) {
      const queue = this.buffered;
      this.buffered = null;
      this.pendingStop = queue.stop();
    }
    if (this.native)
      this.pendingStop =
        window.desktopAPI
          ?.stopNativeAudio()
          .catch((cause) => console.warn('Native audio stop failed', cause)) ?? Promise.resolve();
    if (this.nativeTicker !== null) window.clearInterval(this.nativeTicker);
    this.nativeTicker = null;
    this.native = false;
    this.nativePaused = false;
    this.nativeStart = null;
    if (this.pcmTimer !== null) window.clearTimeout(this.pcmTimer);
    if (this.pcmStartTimer !== null) window.clearTimeout(this.pcmStartTimer);
    this.pcmTimer = null;
    this.pcmStartTimer = null;
    void this.pcmContext?.close().catch(() => {});
    this.pcmContext = null;
    this.pcmStarted = false;
    this.pcmEnd = 0;
    this.pcmFinished = false;
    this.pcmUserPaused = false;
    if (this.audio) {
      this.audio.onended = null;
      this.audio.onerror = null;
      this.audio.pause();
      this.audio.removeAttribute('src');
      this.audio.load();
      this.audio = null;
    }
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = null;
    this.voiceId = null;
    this.listener(null, null);
  }

  pause() {
    if (this.buffered) {
      this.buffered.pause();
      this.listener(null, null);
      return;
    }
    if (this.pcmContext) {
      this.pcmUserPaused = true;
      if (this.pcmTimer !== null) window.clearTimeout(this.pcmTimer);
      this.pcmTimer = null;
      void this.pcmContext.suspend();
      this.listener(null, null);
      return;
    }
    if (this.native) {
      this.nativePaused = true;
      void window.desktopAPI
        ?.pauseNativeAudio()
        .catch((cause) => console.warn('Native audio pause failed', cause));
      this.listener(null, null);
      return;
    }
    if (!this.audio) return;
    this.audio.pause();
    this.listener(null, null);
  }

  async resume() {
    if (this.buffered) {
      await this.buffered.resume();
      this.listener(this.voiceId, null);
      return;
    }
    if (this.pcmContext && this.voiceId) {
      this.pcmUserPaused = false;
      await this.pcmContext.resume();
      this.report({ voiceId: this.voiceId, url: 'pcm-stream', playResult: 'pcm-resumed' });
      if (this.pcmFinished) this.finishPcm();
      return;
    }
    if (this.native && this.voiceId) {
      await window.desktopAPI?.resumeNativeAudio();
      this.nativePaused = false;
      this.report({ voiceId: this.voiceId, url: '', playResult: 'native-resumed' });
      return;
    }
    if (!this.audio || !this.voiceId) return;
    const diagnostic: PlaybackDiagnostic = { voiceId: this.voiceId, url: this.audio.src };
    try {
      await this.audio.play();
      diagnostic.playResult = 'resumed';
      this.report(diagnostic);
    } catch (cause) {
      diagnostic.playResult = 'rejected';
      diagnostic.detail = String(cause);
      this.report(diagnostic);
      throw cause;
    }
  }

  private async activateNative(
    voiceId: string,
    url: string,
    volume: number,
    diagnostic: PlaybackDiagnostic,
    sequence: number,
  ) {
    if (!window.desktopAPI || url.startsWith('blob:'))
      throw new Error('此音频无法通过 Windows 播放通道访问');
    await this.pendingStop;
    if (this.audio) {
      this.audio.onerror = null;
      this.audio.pause();
      this.audio.removeAttribute('src');
      this.audio.load();
      this.audio = null;
    }
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = null;
    this.native = true;
    try {
      await window.desktopAPI.playNativeAudio(url, volume);
      if (sequence !== this.sequence) {
        await window.desktopAPI.stopNativeAudio();
        return;
      }
      diagnostic.playResult = 'native-started';
      diagnostic.detail = 'Chromium 音频输出失败，已切换 Windows 音频通道';
      this.report(diagnostic);
      this.nativeTicker = window.setInterval(() => {
        void window.desktopAPI
          ?.nativeAudioStatus()
          .then((status) => {
            if (sequence !== this.sequence) return;
            if (status.status === 'stopped') {
              this.stop();
              this.ended();
            }
          })
          .catch((cause) => console.warn('Native audio status failed', cause));
      }, 500);
    } catch (cause) {
      this.native = false;
      diagnostic.playResult = 'native-failed';
      diagnostic.detail = String(cause);
      this.report(diagnostic);
      throw cause;
    }
  }

  async play(voiceId: string, url: string, volume: number) {
    this.stop();
    const sequence = this.sequence;
    const diagnostic: PlaybackDiagnostic = { voiceId, url };
    this.voiceId = voiceId;
    try {
      await this.pendingStop;
      const response = await fetch(url, { cache: 'no-store' });
      diagnostic.httpStatus = response.status;
      diagnostic.contentType = response.headers.get('content-type');
      diagnostic.contentLength = response.headers.get('content-length');
      if (!response.ok) throw new Error(`音频接口返回 HTTP ${response.status}`);
      if (!diagnostic.contentType?.includes('audio/'))
        throw new Error(`音频类型异常：${diagnostic.contentType}`);
      const blob = await response.blob();
      if (blob.size < 44) throw new Error(`语音文件为空或损坏：${blob.size} 字节`);
      if (sequence !== this.sequence) return;
      this.objectUrl = URL.createObjectURL(blob);
      const audio = new Audio(this.objectUrl);
      this.audio = audio;
      audio.volume = Math.max(0, Math.min(1, volume));
      audio.onplaying = () => {
        if (this.audio === audio) {
          diagnostic.playResult = 'browser-started';
          this.report(diagnostic);
        }
      };
      audio.onended = () => {
        if (this.audio === audio) {
          this.stop();
          this.ended();
        }
      };
      audio.onerror = () => {
        if (this.audio !== audio) return;
        diagnostic.mediaError = audio.error?.code ?? null;
        diagnostic.detail = audio.error?.message || '浏览器无法解码或播放语音';
        if (window.desktopAPI && !url.startsWith('blob:')) {
          this.nativeStart = this.activateNative(voiceId, url, volume, diagnostic, sequence);
          void this.nativeStart.catch(() => {});
        } else {
          diagnostic.playResult = 'media-error';
          this.report(diagnostic);
        }
      };
      await audio.play();
      if (sequence !== this.sequence) return;
      for (let attempt = 0; attempt < 20 && sequence === this.sequence; attempt++) {
        if (this.nativeStart) {
          await this.nativeStart;
          return;
        }
        if (audio.error)
          throw new Error(`浏览器媒体错误 ${audio.error.code}: ${audio.error.message}`);
        if (audio.currentTime > 0.05) {
          diagnostic.playResult = 'browser-started';
          this.report(diagnostic);
          return;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 100));
      }
      if (sequence === this.sequence) {
        this.nativeStart = this.activateNative(voiceId, url, volume, diagnostic, sequence);
        await this.nativeStart;
      }
    } catch (cause) {
      if (sequence !== this.sequence) return;
      if (this.nativeStart) {
        await this.nativeStart;
        return;
      }
      diagnostic.playResult =
        diagnostic.playResult === 'native-failed' ? 'native-failed' : 'rejected';
      diagnostic.detail =
        cause instanceof Error ? `${cause.name}: ${cause.message}` : String(cause);
      this.report(diagnostic);
      throw cause;
    }
  }

  setVolume(value: number) {
    this.buffered?.setVolume(value);
    if (this.audio) this.audio.volume = Math.max(0, Math.min(1, value));
  }

  private startBuffered(id: string, volume: number, initialBufferMs = 300) {
    this.stop();
    this.voiceId = id;
    const sequence = this.sequence;
    this.buffered = new BufferedAudioQueue(
      id,
      volume,
      (playResult, detail) => {
        if (sequence === this.sequence)
          this.report({ voiceId: id, url: 'continuous-pcm', playResult, detail });
      },
      () => {
        if (sequence === this.sequence) {
          this.stop();
          this.ended();
        }
      },
      this.pendingStop,
      initialBufferMs,
    );
    return this.buffered;
  }
  async queueWav(id: string, url: string, segment: string, volume: number) {
    if (this.buffered?.finished) await this.buffered.done;
    const queue = this.buffered || this.startBuffered(id, volume);
    await queue.enqueueWav(url, segment);
  }
  async feedPcm(
    id: string,
    base64: string,
    rate: number,
    channels: number,
    volume: number,
    segment = id,
    initialBufferMs = 300,
  ) {
    if (this.buffered && this.voiceId !== id) this.stop();
    if (this.buffered?.finished) return false;
    const queue = this.buffered || this.startBuffered(id, volume, initialBufferMs);
    await queue.feed(base64, rate, channels, segment);
    return true;
  }
  endPcmSegment(id: string) {
    return this.buffered?.endSegment(id);
  }
  finishPcm() {
    if (!this.buffered) return false;
    this.buffered.finish();
    return true;
  }
}
