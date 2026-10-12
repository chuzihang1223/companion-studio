"""IndexTTS 2.5 adapter: one speaker reference, contextual emotion per phrase."""

from pathlib import Path
import json
import os
import sys
import time
from threading import RLock

import numpy as np

from .provider import VoiceAudio, VoiceProvider
from .prosody_planner import EngineControl


def emotion_vector(emotion: str) -> list[float]:
    # IndexTTS order: happy, angry, sad, afraid, disgusted, melancholic,
    # surprised, calm. Keep blends moderate to preserve the same speaker.
    vectors = {
        "happy": [0.65, 0, 0, 0, 0, 0, 0.22, 0.12],
        "excited": [0.38, 0, 0, 0, 0, 0, 0.46, 0],
        "playful": [0.32, 0, 0, 0, 0, 0, 0.23, 0.22],
        "concerned": [0, 0, 0.16, 0.30, 0, 0.18, 0, 0.26],
        "gentle": [0, 0, 0.08, 0, 0, 0.15, 0, 0.56],
        "serious": [0, 0, 0, 0.10, 0, 0, 0, 0.48],
        "protective": [0, 0.10, 0, 0.25, 0, 0, 0, 0.30],
        "annoyed": [0, 0.32, 0, 0, 0.08, 0, 0, 0.23],
        "embarrassed": [0.18, 0, 0, 0, 0, 0, 0.12, 0.30],
        "nostalgic": [0.04, 0, 0.06, 0, 0, 0.32, 0, 0.26],
        "curious": [0.08, 0, 0, 0, 0, 0, 0.25, 0.32],
        "neutral": [0, 0, 0, 0, 0, 0, 0, 0.34],
    }
    return vectors.get(emotion, vectors["neutral"])


def blended_emotion_vector(primary: str, secondary: str | None, weight: float) -> list[float]:
    first = emotion_vector(primary)
    if not secondary or weight <= 0:
        return first
    second = emotion_vector(secondary)
    weight = max(0.0, min(weight, 0.3))
    return [round((1 - weight) * a + weight * b, 4) for a, b in zip(first, second)]


class IndexTTSVoiceProvider(VoiceProvider):
    name = "indextts-2.5-expressive"

    def __init__(self, model_dir: Path, references_dir: Path):
        self.model_dir = Path(model_dir)
        reference_root = Path(references_dir)
        profile = reference_root / "profile.json"
        if profile.is_file():
            selection = json.loads(profile.read_text(encoding="utf-8"))["selected_reference"]
            candidate = (reference_root / selection).resolve()
            if not candidate.is_relative_to(reference_root.resolve()):
                raise ValueError("Speaker profile reference must stay inside profile directory")
            self.reference = candidate
        else:
            self.reference = reference_root / "neutral.wav"
        self.engine = None
        self._lock = RLock()

    def warm(self) -> None:
        with self._lock:
            if self.engine is not None:
                return
            if not self.reference.is_file():
                raise FileNotFoundError(f"Speaker reference missing: {self.reference}")
            def stage(name: str) -> None:
                if os.getenv("VOICE_DEBUG_STARTUP") == "1":
                    print(f"VOICE_INDEX_STAGE {name} {time.monotonic():.3f}", file=sys.stderr, flush=True)
            stage("before_torch")
            import torch
            stage("after_torch")
            if not torch.cuda.is_available():
                raise RuntimeError("IndexTTS requires a working CUDA GPU on this installation")
            stage("after_cuda_check")
            from indextts.infer_v2_5 import IndexTTS2
            stage("after_index_import")

            stage("before_model_constructor")
            self.engine = IndexTTS2(
                cfg_path=str(self.model_dir / "config.yaml"),
                model_dir=str(self.model_dir),
                use_bf16=True,
                use_cuda_kernel=False,
                use_qwen_emo=False,
            )
            stage("after_model_constructor")

    def synthesize(self, text: str, control: EngineControl) -> VoiceAudio:
        self.warm()
        with self._lock:
            rate, pcm = self.engine.infer(
                spk_audio_prompt=str(self.reference), text=text,
                output_path=None, lang="ZH",
                emo_vector=blended_emotion_vector(control.emotion, control.secondary_emotion, control.secondary_weight),
                emo_alpha=control.intensity,
                duration_factor=max(0.94, min(1.07, 1.0 / control.speed_factor)),
                use_random=False, verbose=False,
            )
            samples = np.asarray(pcm).reshape(-1)
            if samples.dtype == np.int16:
                samples = samples.astype(np.float32) / 32768.0
            else:
                samples = samples.astype(np.float32)
            if samples.size < rate // 4 or float(np.max(np.abs(samples))) < 0.01:
                raise RuntimeError("Expressive voice engine returned empty or near-silent audio")
            return VoiceAudio(samples, rate)
