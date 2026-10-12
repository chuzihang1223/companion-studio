"""User-selected local files; no third-party audio extraction."""

import hashlib
import json
from pathlib import Path

from app.core.config import settings


MIME = {".mp3": "audio/mpeg", ".flac": "audio/flac", ".wav": "audio/wav",
        ".aac": "audio/aac", ".m4a": "audio/mp4", ".ogg": "audio/ogg"}


class LocalMusicLibrary:
    def __init__(self):
        self.settings_file = settings.data_root / "music.json"
        self.folder: Path | None = None
        self.files: dict[str, Path] = {}
        if self.settings_file.is_file():
            try:
                candidate = Path(json.loads(self.settings_file.read_text(encoding="utf-8"))["folder"])
                if candidate.is_dir():
                    self.folder = candidate.resolve()
            except (OSError, KeyError, ValueError):
                pass
        self.scan()

    def choose(self, folder: str) -> list[dict]:
        target = Path(folder).resolve()
        if not target.is_dir():
            raise ValueError("音乐文件夹不存在")
        self.folder = target
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps({"folder": str(target)}, ensure_ascii=False), encoding="utf-8")
        return self.scan()

    def scan(self) -> list[dict]:
        self.files.clear()
        if not self.folder or not self.folder.is_dir():
            return []
        rows = []
        for path in self.folder.rglob("*"):
            if len(rows) >= 2000:
                break
            if path.is_symlink() or path.suffix.lower() not in MIME or not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(self.folder):
                continue
            identifier = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:24]
            self.files[identifier] = resolved
            rows.append({"id": identifier, "title": path.stem, "name": path.name,
                         "format": path.suffix.lower()[1:], "audio_url": f"/api/music/audio/{identifier}"})
        return sorted(rows, key=lambda item: item["title"].casefold())

    def path(self, identifier: str) -> Path | None:
        target = self.files.get(identifier)
        return target if target and target.is_file() and self.folder and target.resolve().is_relative_to(self.folder) else None
