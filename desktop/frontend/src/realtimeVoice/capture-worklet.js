/* global AudioWorkletProcessor, registerProcessor, sampleRate */
/** 100ms mono PCM packets. Audio callbacks never render React or access IPC. */
class StudioVoiceCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.packet = new Int16Array(Math.round(sampleRate / 10));
    this.position = 0;
  }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (const value of input) {
      this.packet[this.position++] = Math.round(Math.max(-1, Math.min(1, value)) * 32767);
      if (this.position === this.packet.length) {
        this.port.postMessage(this.packet.buffer, [this.packet.buffer]);
        this.packet = new Int16Array(Math.round(sampleRate / 10));
        this.position = 0;
      }
    }
    return true;
  }
}
registerProcessor('studio-voice-capture', StudioVoiceCapture);
