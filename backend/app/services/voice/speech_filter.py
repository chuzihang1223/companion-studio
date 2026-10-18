"""Only character-facing language belongs in TTS, never code or tool output."""

import re


def speakable_text(text: str) -> str:
    text = re.sub(r"\[K:[a-f0-9-]{36}\]", "", text, flags=re.I)
    text = re.sub(r"```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)", "", text)
    text = re.sub(r"`[^`]*`", "", text)
    lines = [
        line
        for line in text.splitlines()
        if not re.match(
            r'\s*(?:Traceback|File "|at \S+\(|PS [A-Z]:|[A-Z]:[\\/]|[{}]\s*"?|[+-]{3} |(?:def|class|import|const|let|function)\s)',
            line,
        )
    ]
    text = "\n".join(lines)
    text = re.sub(r"https?://\S+|[A-Z]:[\\/][^\s，。；]+", "", text)
    text = re.sub(r"\b(?:print|console\.log)\([^\n]*?\)", "打印函数", text)
    return re.sub(r"\s+", " ", text).strip()


class StreamingSpeechFilter:
    """Retain only a possible partial fence, so complete spoken phrases flush promptly."""

    def __init__(self):
        self.pending = ""
        self.fenced = False
        self.code_seen = False
        self.citation = False

    def feed(self, delta: str, final: bool = False) -> str:
        self.pending += delta
        output = []
        while self.pending:
            if self.citation:
                end = self.pending.find("]")
                if end < 0:
                    self.pending = ""
                    break
                self.pending, self.citation = self.pending[end + 1 :], False
                continue
            if not self.fenced and not final and "[k:".startswith(self.pending.casefold()):
                break
            if not self.fenced and self.pending.casefold().startswith("[k:"):
                self.citation, self.pending = True, self.pending[3:]
                continue
            if not final and self.pending in {chr(96), chr(96) * 2, "~", "~~"}:
                break
            if self.pending.startswith(("```", "~~~")):
                self.fenced = not self.fenced
                self.code_seen = True
                self.pending = self.pending[3:]
                continue
            char, self.pending = self.pending[0], self.pending[1:]
            if not self.fenced:
                output.append(char)
        return "".join(output)
