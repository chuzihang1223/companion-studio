"""Optional local ONNX engine adapter, run in the model's own venv."""

import importlib.util
import os
from pathlib import Path
from threading import RLock

from .provider import VoiceAudio, VoiceProvider
from .prosody_planner import EngineControl
from .reference_manager import ReferenceManager
from .policy import VoiceExpressionPolicy


class ONNXVoiceProvider(VoiceProvider):
    name = "onnx-tts"

    def __init__(self, model_dir: Path, references_dir: Path):
        self.model_dir = Path(model_dir)
        self.references_dir = Path(references_dir)
        self._lock = RLock()
        self.engine = None
        self.references = None

    def warm(self) -> None:
        with self._lock:
            if self.engine is not None:
                return
            spec = importlib.util.spec_from_file_location("local_onnx_voice_provider", self.model_dir / "infer.py")
            if spec is None or spec.loader is None:
                raise RuntimeError("Could not load ONNX package infer.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            # Local model packages are user supplied. Their adapter may expose
            # a neutral factory, or the user may name the existing engine class.
            providers = module.select_providers("cpu")
            factory = getattr(module, "create_engine", None)
            if callable(factory):
                engine = factory(self.model_dir, providers)
            else:
                engine_class = getattr(module, os.getenv("VOICE_ONNX_ENGINE_CLASS", "OnnxEngine"), None)
                if not callable(engine_class):
                    raise RuntimeError("本地 ONNX 包需提供 create_engine 或配置 VOICE_ONNX_ENGINE_CLASS")
                engine = engine_class(self.model_dir, providers)
            self.references = ReferenceManager(engine, self.model_dir, self.references_dir, VoiceExpressionPolicy(), module.load_audio)
            for emotion in sorted(self.references.available() - {"neutral"}):
                with self.references.use(emotion):
                    pass
            self.engine = engine

    def synthesize(self, text: str, control: EngineControl) -> VoiceAudio:
        import numpy as np  # Installed only in the user's local model environment.
        self.warm()
        with self._lock:
            # Some short phrases produce silence with a strongly styled reference.
            # Retry with the same speaker's neutral reference before reporting failure.
            attempts = ((control.reference, 42), ("neutral", 43), ("neutral", 44))
            for reference, seed in attempts:
                with self.references.use(reference):
                    audio = np.asarray(self.engine.synthesize(
                        text, language="zh", text_split_method="cut0",
                        speed_factor=control.speed_factor, fragment_interval=0.0,
                        temperature=1.0, top_k=15, seed=seed,
                    ), dtype=np.float32)
                if audio.size >= 8000 and float(np.max(np.abs(audio))) >= 0.01:
                    return VoiceAudio(audio, 32000)
            raise RuntimeError("Voice engine returned empty or near-silent audio after retries")
