import { AudioCapture } from './AudioCapture';
import type { VoiceBinding, VoiceIdentity, VoiceOptions, VoiceView } from './types';

/** Async identity is fixed before capture/ASR/submit. Navigation invalidates it. */
export class VoiceController {
  view: VoiceView = {
    identity: null,
    capturing: false,
    busy: false,
    transcript: '',
    error: '',
    duration: 0,
  };
  private capture = new AudioCapture();
  private revision = 0;
  private releasedWhileStarting = false;
  private submitted = false;
  private replyId = '';
  private eventSequence = 0;
  constructor(
    private changed: (view: VoiceView) => void,
    private interruptOutput: (conversationId?: string) => Promise<void>,
    private send: (text: string, binding: VoiceBinding, current: () => boolean) => Promise<void>,
    private sdk = window.desktopAPI!,
  ) {}
  private patch(values: Partial<VoiceView>) {
    this.view = { ...this.view, ...values };
    this.changed(this.view);
  }
  private scope(identity = this.view.identity) {
    if (!identity) throw Error('请先开启语音会话');
    return {
      voice_session_id: identity.voice_session_id,
      turn_id: identity.turn_id,
      generation_id: identity.generation_id,
    };
  }
  async start(options: VoiceOptions) {
    const token = ++this.revision;
    this.patch({ busy: true, error: '' });
    try {
      const identity = await this.sdk.realtimeVoiceStart(options);
      if (token !== this.revision) {
        await this.sdk.realtimeVoiceControl('stop', this.scope(identity)).catch(() => {});
        return;
      }
      this.patch({ identity, transcript: '' });
    } catch (cause) {
      if (token === this.revision) this.patch({ error: String(cause) });
    } finally {
      if (token === this.revision) this.patch({ busy: false });
    }
  }
  async record(deviceId: string) {
    if (this.view.busy || this.view.capturing || !this.view.identity) return;
    const token = ++this.revision;
    this.releasedWhileStarting = false;
    this.submitted = false;
    this.patch({ busy: true, error: '', transcript: '', duration: 0 });
    try {
      await this.interruptOutput(this.view.identity?.conversation_id);
      await this.sdk.realtimeVoiceControl('interrupt', this.scope());
      if (token !== this.revision) return;
      const identity = await this.sdk.realtimeVoiceControl('begin', {
        voice_session_id: this.view.identity.voice_session_id,
      });
      if (token !== this.revision) return;
      this.patch({ identity });
      await this.capture.start(
        deviceId,
        async (packet) => {
          if (token !== this.revision) return;
          const next = await this.sdk.realtimeVoiceControl('frame', {
            ...this.scope(identity),
            ...packet,
          });
          if (token === this.revision)
            this.patch({ identity: next, duration: next.duration_ms || 0 });
        },
        (message) => {
          if (token === this.revision) {
            void this.interrupt();
            this.patch({ error: message });
          }
        },
        () => void this.finish(),
      );
      if (token !== this.revision || this.releasedWhileStarting) {
        await this.capture.stop();
        if (token === this.revision) await this.interrupt();
        return;
      }
      this.patch({ capturing: true, busy: false });
    } catch (cause) {
      if (token === this.revision) {
        await this.capture.stop();
        const identity = await this.sdk
          .realtimeVoiceControl('interrupt', this.scope())
          .catch(() => null);
        if (token === this.revision)
          this.patch({
            ...(identity ? { identity } : {}),
            capturing: false,
            busy: false,
            error: String(cause),
          });
      }
    }
  }
  async finish() {
    if (this.view.busy && !this.view.capturing) {
      this.releasedWhileStarting = true;
      return;
    }
    if (!this.view.capturing || !this.view.identity) return;
    const token = this.revision,
      identity = this.view.identity;
    this.patch({ capturing: false, busy: true });
    try {
      await this.capture.stop(true);
      const result = await this.sdk.realtimeVoiceControl('finalize', this.scope(identity));
      if (token === this.revision) this.patch({ identity: result, transcript: result.text || '' });
    } catch (cause) {
      if (token === this.revision) {
        const next = await this.sdk
          .realtimeVoiceControl('interrupt', this.scope(identity))
          .catch(() => null);
        if (token === this.revision)
          this.patch({ ...(next ? { identity: next } : {}), error: String(cause) });
      }
    } finally {
      if (token === this.revision) this.patch({ busy: false });
    }
  }
  async submit(text: string) {
    const identity = this.view.identity,
      token = this.revision;
    if (
      this.submitted ||
      this.view.busy ||
      !identity?.turn_id ||
      !identity.generation_id ||
      !text.trim()
    )
      return;
    this.submitted = true;
    this.patch({ transcript: '' });
    try {
      await this.send(
        text.trim(),
        {
          voice_session_id: identity.voice_session_id,
          turn_id: identity.turn_id,
          generation_id: identity.generation_id,
        },
        () => token === this.revision && this.submitted,
      );
    } catch (cause) {
      if (token === this.revision) this.patch({ error: String(cause) });
    }
  }
  bindReply(id: string, binding: VoiceBinding) {
    if (this.view.identity?.generation_id === binding.generation_id && this.submitted)
      this.replyId = id;
  }
  generationEnded(identity: VoiceIdentity) {
    const original = this.view.identity;
    if (
      this.submitted &&
      original &&
      identity.voice_session_id === original.voice_session_id &&
      identity.turn_id === original.turn_id &&
      identity.generation_id === original.generation_id
    ) {
      ++this.eventSequence;
      this.patch({ identity });
    }
  }
  observe(event: 'playback_started' | 'playback_completed' | 'playback_failed', id: string) {
    if (!this.submitted || !this.view.identity || id !== this.replyId) return;
    const token = this.revision,
      sequence = ++this.eventSequence;
    void this.sdk
      .realtimeVoiceControl('event', { ...this.scope(), event })
      .then((identity) => {
        if (token === this.revision && sequence === this.eventSequence) this.patch({ identity });
      })
      .catch(() => {});
  }
  async interrupt() {
    const token = ++this.revision,
      original = this.view.identity;
    this.submitted = false;
    void this.capture.stop();
    this.patch({ capturing: false, busy: false, transcript: '' });
    await this.interruptOutput(original?.conversation_id);
    if (original) {
      const identity = await this.sdk
        .realtimeVoiceControl('interrupt', this.scope(original))
        .catch(() => null);
      if (identity && token === this.revision) this.patch({ identity });
    }
  }
  async stop() {
    ++this.revision;
    this.submitted = false;
    const identity = this.view.identity;
    this.patch({ identity: null, capturing: false, busy: false, transcript: '', duration: 0 });
    await this.capture.stop();
    await this.interruptOutput(identity?.conversation_id);
    if (identity) await this.sdk.realtimeVoiceControl('stop', this.scope(identity)).catch(() => {});
  }
}
