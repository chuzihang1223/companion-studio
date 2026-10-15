/** 首次实际播放或有限等待后释放文字；后续逐步展示，避免无限等音频。 */
export class FirstSpeechGate {
  private buffer = '';
  private released = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  private visible: number | null = null;
  private audio: number | null = null;
  private stopped = false;
  private draining = false;
  private started = performance.now();
  constructor(
    private id: string,
    voice: boolean,
    private append: (text: string) => void,
  ) {
    if (!voice) this.released = true;
  }
  push(text: string) {
    if (this.stopped) return;
    if (!this.released && this.timer === null)
      this.timer = setTimeout(() => this.release('timeout'), 2000);
    this.buffer += text;
    if (this.released) this.drain();
  }
  setMessageId(id: string) {
    this.id = id;
  }
  audioStarted() {
    if (this.stopped || this.audio !== null) return;
    this.audio = performance.now();
    this.log('first_actual_audio', {
      elapsed_ms: Math.round(this.audio - this.started),
      sync_delta_ms: this.visible === null ? null : Math.round(this.visible - this.audio),
    });
    this.release('audio');
  }
  private release(reason: string) {
    if (this.stopped || this.released) return;
    this.released = true;
    if (this.timer) clearTimeout(this.timer);
    this.log('presentation_start', {
      reason,
      elapsed_ms: Math.round(performance.now() - this.started),
    });
    this.drain();
  }
  private drain() {
    if (this.draining || this.stopped) return;
    this.draining = true;
    const tick = () => {
      if (this.stopped) return;
      const text = this.buffer.slice(0, 8);
      this.buffer = this.buffer.slice(8);
      if (text) {
        this.append(text);
        if (this.visible === null) {
          requestAnimationFrame(() => {
            if (this.stopped || this.visible !== null) return;
            this.visible = performance.now();
            this.log('first_visible_text', {
              elapsed_ms: Math.round(this.visible - this.started),
              sync_delta_ms: this.audio === null ? null : Math.round(this.visible - this.audio),
            });
          });
        }
      }
      if (this.buffer) this.flushTimer = setTimeout(tick, 25);
      else this.draining = false;
    };
    tick();
  }
  /** 完成事件等缓冲文字展示完；停止可解除等待，不改变音频流本身。 */
  async finish() {
    if (!this.buffer) return;
    if (!this.released) {
      await new Promise<void>((resolve) => {
        const poll = () => {
          if (this.released || this.stopped) resolve();
          else setTimeout(poll, 30);
        };
        poll();
      });
    }
    while (this.buffer && !this.stopped) await new Promise((r) => setTimeout(r, 25));
  }
  stop() {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    if (this.flushTimer) clearTimeout(this.flushTimer);
  }
  private log(event: string, detail: Record<string, unknown>) {
    void window.desktopAPI?.logDiagnostic('voice-sync.log', { id: this.id, event, ...detail });
  }
}
