"""A single Windows waveOut device with queued PCM buffers, including prebuffering."""

import ctypes as c
from ctypes import wintypes as w
import json
import threading
import time
from app.core.file_logging import file_logger


class Format(c.Structure):
    _pack_ = 1
    _fields_ = [
        ("tag", w.WORD),
        ("channels", w.WORD),
        ("rate", w.DWORD),
        ("bytes", w.DWORD),
        ("align", w.WORD),
        ("bits", w.WORD),
        ("extra", w.WORD),
    ]


class Header(c.Structure):
    _fields_ = [
        ("data", c.c_void_p),
        ("length", w.DWORD),
        ("recorded", w.DWORD),
        ("user", c.c_size_t),
        ("flags", w.DWORD),
        ("loops", w.DWORD),
        ("next", c.c_void_p),
        ("reserved", c.c_size_t),
    ]


class Position(c.Structure):
    _fields_ = [("type", w.UINT), ("sample", w.DWORD), ("reserved", w.DWORD)]


class NativePcmOutput:
    def __init__(self):
        self.lock = threading.RLock()
        self.handle = None
        self.state = "stopped"
        self.logger = file_logger("app.voice.native.continuity", "voice-continuity.log")
        self.buffers, self.segments = [], {}
        self.rate, self.channels, self.total, self.last_position = 44100, 1, 0, 0
        self.finished, self.released = False, False
        self.started, self.last_end, self.underrun_at = 0.0, None, None
        self.gaps = []
        self.underrun_gaps = []

    def _check(self, result):
        if result:
            raise OSError(f"Windows waveOut error {result}")

    def _log(self, stage, segment, **values):
        self.logger.info(
            json.dumps(
                {
                    "message_id": self.message_id,
                    "segment_id": segment,
                    "event": stage,
                    "output": "waveOut",
                    "monotonic_ms": round(time.perf_counter() * 1000),
                    **values,
                }
            )
        )

    def start(self, message_id, rate, channels, volume, initial_buffer_ms=300):
        with self.lock:
            self.stop()
            self.message_id, self.rate, self.channels = message_id, rate, channels
            self.buffers, self.segments, self.gaps = [], {}, []
            self.underrun_gaps = []
            self.session_id = None
            self.sequence = -1
            self.total, self.last_position, self.finished, self.released = 0, 0, False, False
            self.started, self.last_end, self.underrun_at = 0.0, None, None
            self.volume = volume
            self.initial_buffer_seconds = max(0.2, min(1.2, initial_buffer_ms / 1000))
            self._log(
                "initial_buffer_gate",
                message_id,
                initial_buffer_ms=round(self.initial_buffer_seconds * 1000),
            )
            self.win = c.WinDLL("winmm")
            self.win.waveOutOpen.argtypes = [
                c.POINTER(c.c_void_p),
                w.UINT,
                c.POINTER(Format),
                c.c_size_t,
                c.c_size_t,
                w.DWORD,
            ]
            for name in ("waveOutPrepareHeader", "waveOutUnprepareHeader", "waveOutWrite"):
                getattr(self.win, name).argtypes = [c.c_void_p, c.POINTER(Header), w.UINT]
            self.win.waveOutGetPosition.argtypes = [c.c_void_p, c.POINTER(Position), w.UINT]
            for name in ("waveOutPause", "waveOutRestart", "waveOutReset", "waveOutClose"):
                getattr(self.win, name).argtypes = [c.c_void_p]
            handle = c.c_void_p()
            fmt = Format(1, channels, rate, rate * channels * 2, channels * 2, 16, 0)
            self._check(self.win.waveOutOpen(c.byref(handle), 0xFFFFFFFF, c.byref(fmt), 0, 0, 0))
            self.handle = handle
            self._check(self.win.waveOutPause(handle))
            self.state = "buffering"
            threading.Thread(target=self._monitor, args=(handle,), daemon=True).start()
            return self.status()

    def feed(self, raw, segment_id, end_segment=False):
        with self.lock:
            if not self.handle or self.finished:
                raise ValueError("音频流已结束")
            self._observe()
            if raw:
                if len(raw) % (self.channels * 2):
                    raise ValueError("PCM frame alignment mismatch")
                if (
                    sum(header.length for header, _data in self.buffers) + len(raw)
                    > 64 * 1024 * 1024
                ):
                    raise ValueError("音频缓冲超过限制")
                # Scale samples without changing the device-wide volume.
                if self.volume < 0.999:
                    from array import array

                    values = array("h")
                    values.frombytes(raw)
                    raw = array("h", (round(x * self.volume) for x in values)).tobytes()
                entry = self.segments.setdefault(
                    segment_id, {"start": self.total, "end": None, "started": False, "ended": False}
                )
                data = c.create_string_buffer(raw)
                header = Header(c.cast(data, c.c_void_p), len(raw), 0, 0, 0, 0, None, 0)
                self._check(
                    self.win.waveOutPrepareHeader(self.handle, c.byref(header), c.sizeof(header))
                )
                self._check(self.win.waveOutWrite(self.handle, c.byref(header), c.sizeof(header)))
                self.buffers.append((header, data))
                self.total += len(raw) // (self.channels * 2)
                if self.underrun_at is not None and self.state == "playing":
                    gap = round((time.perf_counter() - self.underrun_at) * 1000, 3)
                    self.underrun_gaps.append(gap)
                    self._log("audio_buffer_underrun_end", segment_id, gap_ms=gap)
                    self.underrun_at = None
                if not self.released and self.total / self.rate >= self.initial_buffer_seconds:
                    self._release()
            if end_segment and segment_id in self.segments:
                self.segments[segment_id]["end"] = self.total
            return self.status()

    def _release(self):
        self._check(self.win.waveOutRestart(self.handle))
        self.released = True
        self.state = "playing"
        self.started = time.perf_counter()

    def finish(self):
        with self.lock:
            if self.handle:
                self.finished = True
                for entry in self.segments.values():
                    if entry["end"] is None:
                        entry["end"] = self.total
                if not self.released:
                    self._release()
            return self.status()

    def _observe(self):
        if not self.handle:
            return
        pos = Position(2, 0, 0)  # TIME_SAMPLES; counts hardware-consumed frames.
        self._check(self.win.waveOutGetPosition(self.handle, c.byref(pos), c.sizeof(pos)))
        self.last_position = pos.sample
        now = time.perf_counter()
        if (
            self.released
            and self.state == "playing"
            and not self.finished
            and self.total
            and pos.sample >= self.total
            and self.underrun_at is None
        ):
            self.underrun_at = now
            self._log("audio_buffer_underrun_start", self.message_id, buffered_seconds=0)
        for segment, entry in self.segments.items():
            if not entry["started"] and pos.sample > entry["start"]:
                entry["started"] = True
                # Subtract frames already played after the boundary, not polling delay.
                start_ms = (now - (pos.sample - entry["start"]) / self.rate) * 1000
                gap = max(0.0, start_ms - self.last_end) if self.last_end is not None else None
                if gap is not None:
                    self.gaps.append(round(gap, 3))
                self._log(
                    "play_start",
                    segment,
                    play_position_ms=round(start_ms, 3),
                    inter_segment_gap_ms=gap,
                )
            if (
                entry["started"]
                and not entry["ended"]
                and entry["end"] is not None
                and pos.sample >= entry["end"]
            ):
                entry["ended"] = True
                self.last_end = (now - (pos.sample - entry["end"]) / self.rate) * 1000
                self._log(
                    "play_end",
                    segment,
                    play_position_ms=round(self.last_end, 3),
                    segment_duration=(entry["end"] - entry["start"]) / self.rate,
                )
        keep = []
        for header, data in self.buffers:
            if header.flags & 1:
                self._check(
                    self.win.waveOutUnprepareHeader(self.handle, c.byref(header), c.sizeof(header))
                )
            else:
                keep.append((header, data))
        self.buffers = keep

    def _monitor(self, owned):
        while True:
            with self.lock:
                if self.handle is not owned:
                    return
                try:
                    self._observe()
                    if self.finished and not self.buffers:
                        self._log(
                            "stream_end",
                            self.message_id,
                            frames_played=self.last_position,
                            total_frames=self.total,
                            gaps_ms=self.underrun_gaps,
                            inter_segment_gaps_ms=self.gaps,
                        )
                        self._check(self.win.waveOutClose(self.handle))
                        self.handle = None
                        self.state = "stopped"
                        return
                except Exception as exc:
                    self._log("output_error", self.message_id, error=type(exc).__name__)
                    self.stop()
                    return
            time.sleep(0.005)

    def pause(self):
        with self.lock:
            self.underrun_at = None
            if self.handle:
                self._check(self.win.waveOutPause(self.handle))
                self.state = "paused"
            return self.status()

    def resume(self):
        with self.lock:
            if self.handle:
                self._release()
            return self.status()

    def stop(self):
        with self.lock:
            if self.handle:
                self._log(
                    "stream_stopped",
                    self.message_id,
                    frames_played=self.last_position,
                    total_frames=self.total,
                    finished=self.finished,
                )
                self.win.waveOutReset(self.handle)
                for header, _data in self.buffers:
                    self.win.waveOutUnprepareHeader(self.handle, c.byref(header), c.sizeof(header))
                self.win.waveOutClose(self.handle)
            self.handle = None
            self.buffers = []
            self.state = "stopped"
            self.session_id = None

    def status(self):
        with self.lock:
            self._observe()
            return {
                "status": self.state,
                "position": round(self.last_position / self.rate, 3),
                "duration": round(self.total / self.rate, 3),
                "buffered_seconds": round(max(0, self.total - self.last_position) / self.rate, 3),
                "output": "Windows continuous PCM",
                "segments": len(self.segments),
                "gaps_ms": self.underrun_gaps,
            }
