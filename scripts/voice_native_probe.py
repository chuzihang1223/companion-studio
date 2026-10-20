"""Public synthetic speech only: installed local ASR/TTS, never microphone/cloud."""

import argparse
import asyncio
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("zh-CN", "你好，今天过得怎么样。"),
    ("en-US", "Hello, how are you today."),
    ("zh-CN", "请检查 Python 项目的 README 文件。"),
    ("zh-CN", "现在先休息一下，我们等会儿再继续。"),
    ("en-US", "Please open the project folder."),
]


def memory(pid):
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "peak",
                "working",
                "paged_peak",
                "paged",
                "nonpaged_peak",
                "nonpaged",
                "pagefile",
                "pagefile_peak",
            )
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    process = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
    if not process:
        return None
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        query = ctypes.WinDLL("psapi").GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        return counters.working if query(process, ctypes.byref(counters), counters.cb) else None
    finally:
        kernel.CloseHandle(process)


def normalized(text):
    return re.sub(r"[\s，。！？,.!?]", "", text).casefold()


async def run(output):
    from app.services.realtime_voice.audio import wav_bytes
    from app.services.realtime_voice.windows import WindowsSpeech
    from app.services.evaluation.metrics import percentile

    active, rows, observed = [], [], []
    finished = asyncio.Event()

    async def sample():
        while not finished.is_set():
            values = [memory(p.pid) for provider in tuple(active) for p in tuple(provider.children)]
            observed.extend(v for v in values if v is not None)
            await asyncio.sleep(0.025)

    sampling = asyncio.create_task(sample())
    provider = WindowsSpeech()
    active.append(provider)
    begin = time.perf_counter()
    try:
        capabilities = await provider.initialize()
        initialization_ms = (time.perf_counter() - begin) * 1000
        for index, (language, text) in enumerate(CASES):
            native = WindowsSpeech(language=language)
            active.append(native)
            try:
                start = time.perf_counter()
                pcm = await native.synthesize(text)
                tts_ms = (time.perf_counter() - start) * 1000
                start = time.perf_counter()
                transcript = await native.recognize(wav_bytes(pcm))
                asr_ms = (time.perf_counter() - start) * 1000
                duration = len(pcm) / 32000
                name = f"public-synthetic-{index + 1}.wav"
                (output / name).write_bytes(wav_bytes(pcm))
                rows.append(
                    {
                        "language": language,
                        "input": text,
                        "final": transcript,
                        "normalized_exact_match": normalized(text) == normalized(transcript),
                        "tts_ms": tts_ms,
                        "asr_ms": asr_ms,
                        "audio_seconds": duration,
                        "real_time_factor": tts_ms / 1000 / duration,
                        "wav": name,
                    }
                )
            finally:
                await native.close()
                active.remove(native)
    finally:
        await provider.close()
        active.clear()
        finished.set()
        await sampling
    report = {
        "provider": "windows-sapi",
        "input": "public_local_synthetic_wav",
        "actual_asr": True,
        "actual_tts": True,
        "physical_microphone": False,
        "actual_playback": False,
        "cloud_requests": 0,
        "initialization_probe_ms": initialization_ms,
        "installed": capabilities,
        "output": {"sample_rate": 16000, "channels": 1, "format": "pcm_s16le"},
        "memory": {
            "samples": len(observed),
            "observed_max_helper_working_set_bytes": max(observed, default=None),
            "coverage": "owned PowerShell helpers only, 25ms sampling; not full app peak",
        },
        "rows": rows,
        "metrics": {
            key: {
                "n": len(rows),
                "p50": percentile([r[key] for r in rows], 0.5),
                "p95": percentile([r[key] for r in rows], 0.95),
                "max": max(r[key] for r in rows),
            }
            for key in ("tts_ms", "asr_ms", "real_time_factor")
        },
        "limitations": [
            "Installed Windows voices/recognizers vary across computers.",
            "Batch ASR, no partial transcript; synthesized speech accuracy is not real microphone accuracy.",
            "Exact-match result includes mixed-language failures without rewriting the transcript.",
        ],
    }
    (output / "native-results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "rows": len(rows),
                "exact_matches": sum(r["normalized_exact_match"] for r in rows),
                "external_requests": 0,
                "microphone": False,
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.absolute()
    cache = (ROOT / ".cache").resolve()
    if sys.platform != "win32" or not output.resolve().is_relative_to(cache) or output.exists():
        raise ValueError("Use a new directory under this project's .cache on Windows")
    if any(
        p.is_symlink() or p.is_junction()
        for p in [output, *output.parents]
        if p.is_relative_to(cache)
    ):
        raise ValueError("Native probe must not follow filesystem links")
    output.mkdir(parents=True, exist_ok=False)
    os.environ.update(
        COMPANION_DATA_DIR=str(output / "UserData"),
        COMPANION_CLEAN_DISTRIBUTION="1",
        OPENAI_API_KEY="",
        FISH_API_KEY="",
        FISH_REFERENCE_ID="",
        TEMP=str(output),
        TMP=str(output),
    )
    sys.path.insert(0, str(ROOT / "backend"))
    asyncio.run(run(output))


if __name__ == "__main__":
    main()
