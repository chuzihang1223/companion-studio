import json
import os
import re
from pathlib import Path
from threading import RLock

from pydantic import BaseModel, Field

def is_clean_distribution() -> bool:
    return True  # Public edition never enables a legacy/private profile.


DEFAULT_CHARACTER_ID = "assistant"


class ConversationExample(BaseModel):
    situation: str
    keywords: list[str] = Field(default_factory=list)
    user: str
    assistant: str


class CharacterProfile(BaseModel):
    identity: str = Field("", max_length=16000)
    personality: list[str] = Field(default_factory=list, max_length=100)
    speech_style: list[str] = Field(default_factory=list, max_length=100)
    behavior_rules: list[str] = Field(default_factory=list, max_length=100)
    relationship: dict[str, str] = Field(default_factory=dict, max_length=30)
    lore: list[str] = Field(default_factory=list)
    emotional_behavior: dict[str, str] = Field(default_factory=dict)
    daily_life: list[str] = Field(default_factory=list)
    conversation_rules: list[str] = Field(default_factory=list)
    anti_drift_rules: list[str] = Field(default_factory=list)
    examples: list[ConversationExample] = Field(default_factory=list)


class Character(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=100)
    english_name: str = Field("", max_length=100)
    source: str = Field("", max_length=300)
    tagline: str = Field("", max_length=500)
    welcome_message: str = Field("", max_length=8000)
    profile: CharacterProfile
    system_prompt: str = Field(min_length=1, max_length=32000)
    avatar_url: str = ""
    card_format: str = "native_json"
    scenario: str = Field("", max_length=16000)
    style_examples: str = Field("", max_length=24000)
    post_history_instructions: str = Field("", max_length=8000)
    import_warnings: list[str] = Field(default_factory=list)


def neutral_assistant() -> Character:
    return Character(id="assistant", name="通用助手", card_format="assistant", profile=CharacterProfile(),
                     system_prompt="你是用户配置的通用 AI 助手。直接、清楚地回应当前问题，不假定虚拟角色、世界观或与用户的亲密关系。对于不知道的事实明确说明，不编造经历。")


class CharacterLoader:
    def __init__(self, directory: Path, user_directory: Path | None = None):
        self.directory = directory
        self.user_directory = user_directory
        self.active_id = DEFAULT_CHARACTER_ID
        self.characters: dict[str, Character] = {}
        self._lock = RLock()

    def load(self) -> None:
        loaded: dict[str, Character] = {}
        for path in self.directory.glob("assistant.json" if is_clean_distribution() else "*.json"):
            character = Character.model_validate(json.loads(path.read_text(encoding="utf-8")))
            if character.id != path.stem or character.id in loaded:
                raise ValueError(f"Invalid or duplicate character id in {path.name}")
            loaded[character.id] = character
        if is_clean_distribution():
            loaded.setdefault("assistant", neutral_assistant())
            self.active_id = "assistant"
        if not loaded:
            raise ValueError("No character configuration found")
        if self.user_directory:
            for path in self.user_directory.glob("*.json"):
                if path.name == "active.json":
                    continue
                try:
                    character = Character.model_validate_json(path.read_text(encoding="utf-8"))
                    if is_clean_distribution() and not character.id.startswith(("custom_", "import_")):
                        continue  # A clean profile must not auto-migrate a legacy persona.
                    if character.id == path.stem and character.id not in {"assistant", "active"} and re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", character.id):
                        character.avatar_url = self._avatar_url(character.id)
                        loaded[character.id] = character
                except (ValueError, OSError):
                    continue  # A corrupt imported file must not prevent startup.
            try:
                active = json.loads((self.user_directory / "active.json").read_text(encoding="utf-8"))["id"]
                if active in loaded:
                    self.active_id = active
            except (ValueError, OSError, KeyError, TypeError):
                pass
        self.characters = loaded

    def _avatar_url(self, identifier: str) -> str:
        if self.user_directory and (self.user_directory / "avatars" / f"{identifier}.png").is_file():
            return f"/api/characters/{identifier}/avatar"
        return ""

    def import_character(self, character: Character, avatar: bytes | None = None, *, activate: bool = True) -> None:
        if not self.user_directory or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", character.id) or character.id in {"active", "assistant"}:
            raise ValueError("角色 ID 必须为 1–64 个英文字母、数字、下划线或连字符")
        with self._lock:
            self.user_directory.mkdir(parents=True, exist_ok=True)
            if avatar is not None:
                avatar_path = self.user_directory / "avatars" / f"{character.id}.png"
                avatar_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = avatar_path.with_suffix(".tmp")
                temporary.write_bytes(avatar)
                os.replace(temporary, avatar_path)
            character.avatar_url = self._avatar_url(character.id)
            target = self.user_directory / f"{character.id}.json"
            temporary = target.with_suffix(".tmp")
            temporary.write_text(character.model_dump_json(indent=2), encoding="utf-8")
            os.replace(temporary, target)
            self.characters[character.id] = character
            if activate:
                self.activate(character.id)

    def activate(self, identifier: str) -> Character:
        if identifier not in self.characters:
            raise LookupError("未找到角色")
        if not self.user_directory:
            raise OSError("角色数据目录不可写")
        self.user_directory.mkdir(parents=True, exist_ok=True)
        active_file = self.user_directory / "active.json"
        temporary = active_file.with_suffix(".tmp")
        with self._lock:
            temporary.write_text(json.dumps({"id": identifier}), encoding="utf-8")
            os.replace(temporary, active_file)
            self.active_id = identifier
        return self.characters[identifier]

    def get(self, character_id: str) -> Character | None:
        return self.characters.get(character_id)
