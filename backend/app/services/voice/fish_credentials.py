"""In-memory credential bridge target; persistence is Electron safeStorage only."""

from threading import Lock


class FishCredentialService:
    def __init__(self):
        self._key = ""
        self._lock = Lock()

    def update(self, key: str) -> None:
        with self._lock:
            self._key = key.strip()

    def get(self) -> str:
        with self._lock:
            return self._key

    def configured(self) -> bool:
        with self._lock:
            return bool(self._key)
