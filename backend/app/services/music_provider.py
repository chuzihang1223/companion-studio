"""Legal music destinations and an explicitly selected local library."""

from abc import ABC, abstractmethod
from urllib.parse import quote

from app.services.music_library import LocalMusicLibrary


class MusicProvider(ABC):
    @abstractmethod
    def search(self, query: str) -> dict:
        ...


class NetEaseMusicProvider(MusicProvider):
    def search(self, query: str) -> dict:
        return {"url": "https://music.163.com/#/search/m/?s=" + quote(query[:160]),
                "playback": "official_website"}


class QQMusicProvider(MusicProvider):
    def search(self, query: str) -> dict:
        return {"url": "https://y.qq.com/n/ryqq/search?w=" + quote(query[:160]),
                "playback": "official_website"}


class LocalMusicProvider(MusicProvider):
    def __init__(self, library: LocalMusicLibrary | None = None):
        self.library = library or LocalMusicLibrary()

    def search(self, query: str) -> dict:
        matches = [row for row in self.library.scan()
                   if query.casefold() in row["title"].casefold()][:10]
        return {"tracks": matches, "playback": "app"}
