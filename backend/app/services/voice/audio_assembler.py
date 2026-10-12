"""Join phrase audio with natural gaps and mild level matching."""

import numpy as np

from .provider import VoiceAudio
from .speech_plan import SpeechSegmentPlan


def join_segments(parts: list[tuple[SpeechSegmentPlan, VoiceAudio]]) -> VoiceAudio:
    if not parts:
        return VoiceAudio(np.zeros(0, dtype=np.float32), 32000)
    rate = parts[0][1].sample_rate
    if any(audio.sample_rate != rate for _, audio in parts):
        raise ValueError("All segments must use the same sample rate")
    # Stay within 1.5 dB of original segment gain; do not flatten dynamics.
    rms_values = [float(np.sqrt(np.mean(np.square(audio.samples)))) for _, audio in parts if len(audio.samples)]
    target_rms = float(np.median(rms_values)) if rms_values else 0.0
    result = []
    for index, (plan, audio) in enumerate(parts):
        if index:
            gap_ms = max(60, min(500, plan.delivery.pause_before_ms + parts[index - 1][0].delivery.pause_after_ms))
            result.append(np.zeros(round(rate * gap_ms / 1000), dtype=np.float32))
        samples = np.asarray(audio.samples, dtype=np.float32).copy()
        if samples.size and target_rms:
            current = float(np.sqrt(np.mean(samples * samples)))
            gain = float(np.clip(target_rms / max(current, 1e-6), 0.84, 1.19))
            samples *= gain
        result.append(samples)
    joined = np.concatenate(result)
    peak = float(np.max(np.abs(joined))) if joined.size else 0
    if peak > 0.96:
        joined *= 0.96 / peak
    return VoiceAudio(joined, rate)
