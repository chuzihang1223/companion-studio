"""One normalized reply buffer; emotion directions never rewrite its body."""

import unicodedata
from .speech_filter import StreamingSpeechFilter, speakable_text


class CanonicalResponseText:
    def __init__(self):
        self.text = ""
        self.pending = ""
        self.filter = StreamingSpeechFilter()
        self.spoken = ""

    def feed(self, delta, final=False):
        self.pending += str(delta)
        # Retain the last character for CRLF and combining characters split by tokens.
        stable_end = bool(self.pending and self.pending[-1] in "。！？!?；;：:\n")
        boundary = len(self.pending) if final or stable_end else max(0, len(self.pending) - 1)
        if not final:
            while 0 < boundary < len(self.pending) and unicodedata.combining(
                self.pending[boundary]
            ):
                boundary -= 1
            if boundary and self.pending[boundary - 1] == "\r":
                boundary -= 1
        raw, self.pending = self.pending[:boundary], self.pending[boundary:]
        visible = unicodedata.normalize(
            "NFC", raw.replace("\r\n", "\n").replace("\r", "\n")
        ).replace("\x00", "")
        self.text += visible
        speech = self.filter.feed(visible, final=final)
        self.spoken += speech
        return visible, speech


class IncrementalSpeechText:
    """Flush complete short phrases promptly; preserve longer connected utterances."""

    def __init__(self, initial_chars=10, target_chars=120, max_chars=180, initial_limit=30):
        self.pending, self.sent = "", 0
        self.initial_chars, self.target_chars, self.max_chars = (
            initial_chars,
            target_chars,
            max_chars,
        )
        self.initial_limit = initial_limit

    def adapt(self, buffered_audio_seconds: float, produced_per_second: float):
        # A low queue releases the next complete clause; a healthy queue keeps longer arcs.
        if buffered_audio_seconds < 1.2:
            self.target_chars = 24
        elif produced_per_second < 1.1:
            self.target_chars = 150
        else:
            self.target_chars = 120

    def feed(self, text, final=False):
        self.pending += text
        output = []
        while self.pending:
            minimum = self.initial_chars if not self.sent else self.target_chars
            ends = [i + 1 for i, c in enumerate(self.pending) if c in "。！？!?；;\n"]
            limit = self.initial_limit if not self.sent else self.max_chars
            boundary = next((at for at in ends if minimum <= at <= limit), None)
            if boundary is None and not self.sent and len(self.pending) >= self.initial_limit:
                boundary = next(
                    (
                        i + 1
                        for i, c in enumerate(self.pending[: self.initial_limit])
                        if c in "，,；;：:" and i + 1 >= max(10, self.initial_chars)
                    ),
                    None,
                )
            if boundary is None and len(self.pending) >= self.max_chars:
                boundary = max(
                    (i + 1 for i, c in enumerate(self.pending[: self.max_chars]) if c in "，,、 "),
                    default=self.max_chars,
                )
            if boundary is None and final:
                boundary = len(self.pending)
            if boundary is None:
                break
            part, self.pending = self.pending[:boundary], self.pending[boundary:]
            if clean := speakable_text(part):
                output.append(clean)
                self.sent += len(clean)
        return output
