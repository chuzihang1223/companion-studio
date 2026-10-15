"""Incrementally strip a PCM16 WAV header, including streaming sentinel lengths."""

import struct


class PcmStreamDecoder:
    def __init__(self):
        self.buffer = bytearray()
        self.started = False
        self.rate = 44100
        self.channels = 1
        self.remaining: int | None = None

    def feed(self, chunk: bytes) -> bytes:
        self.buffer.extend(chunk)
        if not self.started:
            if len(self.buffer) < 12:
                return b""
            if self.buffer[:4] != b"RIFF" or self.buffer[8:12] != b"WAVE":
                raise ValueError("Fish 流式音频不是 WAV")
            pos = 12
            while len(self.buffer) >= pos + 8:
                kind, size = (
                    bytes(self.buffer[pos : pos + 4]),
                    struct.unpack_from("<I", self.buffer, pos + 4)[0],
                )
                if kind == b"data":
                    self.remaining = size if size not in {0, 0xFFFFFFFF} else None
                    del self.buffer[: pos + 8]
                    self.started = True
                    break
                if size > 65536:
                    raise ValueError("WAV header too large")
                if len(self.buffer) < pos + 8 + size:
                    return b""
                if kind == b"fmt ":
                    fmt, self.channels, self.rate = struct.unpack_from("<HHI", self.buffer, pos + 8)
                    bits = struct.unpack_from("<H", self.buffer, pos + 22)[0]
                    if fmt != 1 or bits != 16 or self.channels not in {1, 2}:
                        raise ValueError("流式播放只接受 PCM16 WAV")
                pos += 8 + size + (size % 2)
            if not self.started:
                return b""
        length = (
            len(self.buffer) if self.remaining is None else min(len(self.buffer), self.remaining)
        )
        length -= length % (2 * self.channels)
        raw = bytes(self.buffer[:length])
        del self.buffer[:length]
        if self.remaining is not None:
            self.remaining -= length
        return raw
