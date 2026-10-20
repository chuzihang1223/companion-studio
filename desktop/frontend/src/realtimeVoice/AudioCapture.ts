// Worklet module loading must use the application's audited same-origin asset;
// a small Vite data-URL asset is rejected by the packaged origin policy.
import workletURL from './capture-worklet.js?url&no-inline';
import type { CapturePacket } from './types';

const encode = (raw: ArrayBuffer) => {
  const bytes = new Uint8Array(raw);
  let text = '';
  for (let n = 0; n < bytes.length; n += 8192)
    text += String.fromCharCode(...bytes.subarray(n, n + 8192));
  return btoa(text);
};

/** Owns only tracks explicitly granted to this capture. Default: no device open. */
export class AudioCapture {
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private revision = 0;
  private chain: Promise<unknown> = Promise.resolve();
  private pending = 0;
  private timer: number | null = null;

  async start(
    deviceId: string,
    frame: (packet: CapturePacket) => Promise<unknown>,
    failure: (message: string) => void,
    limit: () => void,
  ) {
    await this.stop();
    const token = ++this.revision;
    let sequence = 0;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: deviceId ? { exact: deviceId } : undefined,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
    if (token !== this.revision) {
      stream.getTracks().forEach((track) => track.stop());
      throw Error('录音已取消');
    }
    this.stream = stream;
    for (const track of stream.getTracks())
      track.onended = () => {
        if (token === this.revision) failure('麦克风已断开或权限已撤销，请重新选择设备');
      };
    try {
      const context = new AudioContext({ latencyHint: 'interactive' });
      this.context = context;
      if (![16000, 24000, 44100, 48000].includes(context.sampleRate))
        throw Error('设备采样率不受支持');
      await context.audioWorklet.addModule(workletURL);
      if (token !== this.revision) throw Error('录音已取消');
      const node = new AudioWorkletNode(context, 'studio-voice-capture');
      this.node = node;
      const source = context.createMediaStreamSource(stream),
        mute = context.createGain();
      mute.gain.value = 0;
      source.connect(node).connect(mute).connect(context.destination);
      node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (token !== this.revision) return;
        if (this.pending >= 8) {
          void this.stop();
          failure('转写处理跟不上采集，已停止录音。请缩短语句后重试。');
          return;
        }
        const packet: CapturePacket = {
          sequence: sequence++,
          sample_rate: context.sampleRate,
          channels: 1,
          sample_format: 'pcm_s16le',
          frame_duration_ms: (event.data.byteLength / 2 / context.sampleRate) * 1000,
          timestamp: performance.now(),
          pcm_base64: encode(event.data),
        };
        this.pending++;
        this.chain = this.chain
          .then(async () => {
            if (token === this.revision) await frame(packet);
          })
          .catch(() => {
            if (token === this.revision) {
              void this.stop();
              failure('音频传输失败，已停止采集；没有发送识别文本');
            }
          })
          .finally(() => this.pending--);
      };
      await context.resume();
      if (token !== this.revision || context.state !== 'running') throw Error('录音已停止');
      this.timer = window.setTimeout(limit, 29000);
    } catch (cause) {
      // An obsolete permission/addModule continuation must not stop a newer
      // capture's tracks or AudioContext after it has taken ownership.
      if (token === this.revision) await this.stop();
      else stream.getTracks().forEach((track) => track.stop());
      throw cause;
    }
  }

  /** Stop devices immediately; optionally await only already-accepted bounded packets. */
  async stop(drain = false) {
    const token = this.revision;
    if (!drain) this.revision++;
    if (this.timer !== null) window.clearTimeout(this.timer);
    this.timer = null;
    if (this.node) {
      this.node.port.onmessage = null;
      this.node.disconnect();
    }
    this.node = null;
    this.stream?.getTracks().forEach((track) => {
      track.onended = null;
      track.stop();
    });
    this.stream = null;
    const context = this.context;
    this.context = null;
    await context?.close().catch(() => {});
    if (drain) await this.chain;
    if (drain && this.revision === token) this.revision++;
  }
}
