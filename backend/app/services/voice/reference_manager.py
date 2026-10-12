"""Warm ONNX reference switching for verified recordings of the same voice.

The supported user-supplied infer.py API has a fixed reference path. This adapter changes only its
cached conditioning arrays and leaves the exported model and infer.py intact.
Calls to synthesize must be serialized while a reference is active.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Callable, Iterator


@dataclass(frozen=True)
class ReferenceProfile:
    name: str
    audio: Path
    text: str


class ReferenceManager:
    def __init__(self, engine, model_dir: Path, references_dir: Path, policy, load_audio: Callable):
        self.engine = engine
        self.model_dir = model_dir
        self.references_dir = references_dir
        self.policy = policy
        self.load_audio = load_audio
        self._lock = RLock()
        self._neutral = self._snapshot()
        self._cache = {"neutral": self._neutral}

    def available(self) -> set[str]:
        choices = {"neutral"}
        for emotion in self.policy.emotions:
            wav = self.references_dir / f"{emotion}.wav"
            transcript = self.references_dir / f"{emotion}.txt"
            if emotion != "neutral" and wav.is_file() and transcript.is_file():
                choices.add(emotion)
        return choices

    def select(self, emotion: str) -> str:
        return self.policy.reference_for(emotion, self.available())

    def _snapshot(self) -> dict:
        names = (
            "reference_16k", "reference_32k", "reference_text", "reference_seq",
            "reference_bert", "ssl_content", "speaker_embedding", "_prompt_cache",
        )
        return {name: getattr(self.engine, name) for name in names}

    def _prepare(self, name: str) -> dict:
        if name == "neutral":
            return self._neutral
        if name in self._cache:
            return self._cache[name]
        if name not in self.available():
            raise ValueError(f"Reference profile {name} is incomplete")
        import numpy as np

        wav = self.references_dir / f"{name}.wav"
        text = (self.references_dir / f"{name}.txt").read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"Reference profile {name} has an empty transcript")
        audio16 = self.load_audio(wav, 16000)[None, :]
        audio32 = self.load_audio(wav, 32000)[None, :]
        if not 3 * 16000 <= audio16.shape[1] <= 12 * 16000:
            raise ValueError(f"Reference profile {name} must be 3 to 12 seconds")
        seq, bert = self.engine._phones(text, "zh")
        silence = np.zeros((1, round(32000 * 0.3)), dtype=np.float32)
        ssl = self.engine.hubert.run(None, {"input_values": np.concatenate((audio16, silence), axis=1)})[0]
        speaker = self.engine.speaker.run(None, {"waveform": audio16})[0]
        state = {
            "reference_16k": audio16, "reference_32k": audio32,
            "reference_text": text, "reference_seq": seq, "reference_bert": bert,
            "ssl_content": ssl, "speaker_embedding": speaker,
            "_prompt_cache": {(text, "zh"): (seq, bert)},
        }
        self._cache[name] = state
        return state

    @contextmanager
    def use(self, emotion: str) -> Iterator[str]:
        with self._lock:
            name = self.select(emotion)
            state = self._prepare(name)
            previous = self._snapshot()
            try:
                for key, value in state.items():
                    setattr(self.engine, key, value)
                yield name
            finally:
                for key, value in previous.items():
                    setattr(self.engine, key, value)
