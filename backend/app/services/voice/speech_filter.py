"""Only character-facing language belongs in TTS, never code or tool output."""

import re


class StreamingCredentialFilter:
    """Withhold partial sensitive prefixes and their entire cross-chunk values.

    A bounded prefix automaton prevents a semantic length cut from revealing a
    credential's tail. This is an allowlisted format filter, not proof that any
    arbitrary private sentence has been made safe to speak.
    """

    prefixes = (
        "sk-",
        "sk_",
        "ghp_",
        "gho_",
        "ghu_",
        "ghs_",
        "ghr_",
        "github_pat_",
        "bearer ",
        "akia",
    ) + tuple(
        label + space + separator
        for label in (
            "api_key",
            "api-key",
            "api key",
            "access_token",
            "access token",
            "password",
            "密钥",
            "密码",
        )
        for separator in (":", "=", "：")
        for space in ("", " ", "  ")
    )

    def __init__(self):
        self.pending = ""
        self.redacting = False
        self.value_started = False
        self.quote = None
        self.escaped = False

    def feed(self, delta: str, final=False):
        result = []
        for character in delta:
            if self.redacting:
                if self.quote:
                    if self.escaped:
                        self.escaped = False
                    elif character == "\\":
                        self.escaped = True
                    elif character == self.quote:
                        self.quote = None
                        self.redacting = False
                    continue
                if character.isspace() and not self.value_started:
                    continue
                if character in "\"'" and not self.value_started:
                    self.quote = character
                    self.value_started = True
                    continue
                if character.isspace() or character in "，。！？；、()[]{}<>\"'":
                    self.redacting = False
                    result.append(character)
                else:
                    self.value_started = True
                continue
            if character.isspace():
                character = " "
                if self.pending.endswith(" "):
                    continue
            self.pending += character
            if self.pending.casefold() in self.prefixes:
                self.pending = ""
                self.redacting, self.value_started = True, False
                result.append("[敏感凭据已隐藏]")
                continue
            while self.pending and not any(
                prefix.startswith(self.pending.casefold()) for prefix in self.prefixes
            ):
                result.append(self.pending[0])
                self.pending = self.pending[1:]
        if final:
            if not self.redacting:
                result.append(self.pending)
            self.pending = ""
        return "".join(result)


def speakable_text(text: str) -> str:
    # Redact before semantic chunks reach any TTS provider, including local ones.
    # Known key formats and explicitly labelled credentials are never speech.
    text = re.sub(
        r"(?i)(?:sk[-_][A-Za-z0-9_-]+|gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|"
        r"Bearer\s+\S+|(?:api[_ -]?key|password|access[_ -]?token|密钥|密码)\s*[:=：]\s*[^\s，。；]+)",
        "[敏感凭据已隐藏]",
        text,
    )
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
        self.inline = 0
        self.url = False
        self.json_depth = 0
        self.json_quote = False
        self.json_escape = False

    def feed(self, delta: str, final: bool = False) -> str:
        self.pending += delta
        output = []
        while self.pending:
            if self.inline:
                marker = "`" * self.inline
                if not final and marker.startswith(self.pending):
                    break
                if self.pending.startswith(marker):
                    self.pending = self.pending[len(marker) :]
                    self.inline = 0
                else:
                    self.pending = self.pending[1:]
                continue
            if self.url:
                char, self.pending = self.pending[0], self.pending[1:]
                if char.isspace() or char in "，。！？；、":
                    self.url = False
                    output.append(char)
                continue
            if self.json_depth:
                char, self.pending = self.pending[0], self.pending[1:]
                if self.json_escape:
                    self.json_escape = False
                elif self.json_quote and char == "\\":
                    self.json_escape = True
                elif char == '"':
                    self.json_quote = not self.json_quote
                elif not self.json_quote:
                    if char in "{[":
                        self.json_depth += 1
                    elif char in "}]":
                        self.json_depth -= 1
                continue
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
            if not self.fenced:
                if not final and any(
                    prefix.startswith(self.pending.casefold()) for prefix in ("http://", "https://")
                ):
                    break
                if self.pending.casefold().startswith(("http://", "https://")):
                    self.url = True
                    continue
                if self.pending.startswith("`"):
                    self.inline, self.code_seen = (2 if self.pending.startswith("``") else 1), True
                    self.pending = self.pending[self.inline :]
                    continue
                if self.pending.startswith(("{", "[")):
                    self.json_depth = 1
                    self.pending = self.pending[1:]
                    continue
            char, self.pending = self.pending[0], self.pending[1:]
            if not self.fenced:
                output.append(char)
        return "".join(output)
