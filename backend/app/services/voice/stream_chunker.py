"""Release semantic sentences as soon as LLM text deltas complete them."""

import re


class SpeechStreamChunker:
    def __init__(self, min_chars: int | None = None, max_chars: int = 60):
        self.pending = ""
        self.first = True
        self.min_chars = min_chars
        self.max_chars = max_chars

    def feed(self, delta: str) -> list[str]:
        self.pending += delta
        if self.min_chars is not None:
            return self._semantic()
        ready = []
        while True:
            match = re.search(r"[。！？!?]", self.pending)
            if self.first:
                clause = re.search(r'[，,；;。！？!?]', self.pending[9:30])
                if clause:
                    match = None
                    end = clause.end()+9
                elif len(self.pending) >= 30 and not match:
                    # Prefer a natural comma; a long clause may continue until a boundary.
                    end = 0
                else:
                    end = match.end() if match else 0
                if not end:
                    break
            elif match:
                end = match.end()
            elif len(self.pending) > 90:
                commas = [candidate.end() for candidate in re.finditer(r"[，,；;]", self.pending[:75])]
                end = commas[-1] if commas else 0
            else:
                end = 0
            if not end:
                break
            chunk = self.pending[:end].strip()
            self.pending = self.pending[end:]
            if chunk:
                ready.append(chunk)
                self.first = False
        return ready

    def _semantic(self) -> list[str]:
        ready = []
        while len(self.pending) >= self.min_chars:
            window = self.pending[:self.max_chars]
            ends = [m.end() for m in re.finditer(r'[。！？!?](?:[”」])?|…{2,}', window)
                    if m.end() >= self.min_chars]
            if ends:
                end = ends[-1]
            elif len(self.pending) > self.max_chars:
                weak = [m.end() for m in re.finditer(r'[，,；;：:\s]', window)
                        if m.end() >= self.min_chars]
                end = weak[-1] if weak else self.max_chars
            else:
                break
            chunk, self.pending = self.pending[:end].strip(), self.pending[end:]
            if chunk:
                ready.append(chunk)
        return ready

    def flush(self) -> list[str]:
        remainder = self.pending.strip()
        self.pending = ""
        return [remainder] if remainder else []
