"""Optional decoder for user supplied legacy MP3 files.

The standard distribution generates WAV and does not bundle libsndfile.
Users of a separate Python environment may install soundfile themselves.
"""
from pathlib import Path


def decode_mp3_to_wav(source: Path, target: Path) -> None:
    try:
        import soundfile
    except ImportError as exc:
        raise OSError("旧 MP3 的本地回放需要自行安装 soundfile；请重新生成 WAV 语音") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with soundfile.SoundFile(source) as reader:
            if reader.channels not in (1, 2) or not 8000 <= reader.samplerate <= 192000:
                raise ValueError("MP3 音频格式不支持")
            if not reader.samplerate // 20 <= reader.frames <= reader.samplerate * 1800:
                raise ValueError("MP3 音频长度无效")
            with soundfile.SoundFile(target, "w", samplerate=reader.samplerate,
                                     channels=reader.channels, subtype="PCM_16", format="WAV") as writer:
                for block in reader.blocks(blocksize=8192, dtype="int16"):
                    writer.write(block)
    except Exception:
        target.unlink(missing_ok=True)
        raise
