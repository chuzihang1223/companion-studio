import base64
import binascii
import json
from uuid import uuid4
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.schemas.character import CharacterPublic
from app.services.character.loader import Character, CharacterProfile, is_clean_distribution
from app.services.character.cards import parse_card
from app.services.character.card_png import MAX_CARD_FILE, MAX_CARD_JSON
from app.api.llm_settings import require_control

router = APIRouter(tags=["character"])


@router.get("/api/character", response_model=CharacterPublic)
def current_character(request: Request) -> CharacterPublic:
    loader = request.app.state.characters
    character = loader.get(loader.active_id)
    if character is None:
        raise HTTPException(status_code=503, detail="Character unavailable")
    return CharacterPublic(
        id=character.id,
        name=character.name,
        english_name=character.english_name,
        tagline=character.tagline,
        welcome_message=character.welcome_message,
        identity=character.profile.identity,
        avatar_url=character.avatar_url,
        card_format=character.card_format,
    )


class CharacterImport(BaseModel):
    config: dict


@router.post("/api/character/import", response_model=CharacterPublic)
def import_character(
    payload: CharacterImport,
    request: Request,
    x_companion_control_token: str | None = Header(default=None),
) -> CharacterPublic:
    require_control(x_companion_control_token)
    if len(json.dumps(payload.config).encode("utf-8")) > 256 * 1024:
        raise HTTPException(413, "角色配置超过 256 KB")
    try:
        if is_clean_distribution():
            character, _ = parse_card(
                json.dumps(payload.config, ensure_ascii=False).encode("utf-8"), "character.json"
            )
        else:
            character = Character.model_validate(payload.config)
        request.app.state.characters.import_character(character)
    except ValueError:
        raise HTTPException(422, "角色配置格式不正确，请检查 ID 和 profile 字段")
    except OSError:
        raise HTTPException(503, "角色配置保存失败")
    return current_character(request)


class CardFilePayload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    data_base64: str = Field(min_length=1, max_length=((MAX_CARD_FILE + 2) // 3) * 4)


class CustomCharacterPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    identity: str = Field("", max_length=16000)
    personality: list[str] = Field(default_factory=list, max_length=100)
    speech_style: list[str] = Field(default_factory=list, max_length=100)
    behavior_rules: list[str] = Field(default_factory=list, max_length=100)
    relationship: dict[str, str] = Field(default_factory=dict, max_length=30)
    system_prompt: str = Field("", max_length=32000)
    welcome_message: str = Field("", max_length=8000)
    tagline: str = Field("", max_length=500)


class CharacterPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    identity: str | None = Field(None, max_length=16000)
    personality: list[str] | None = Field(None, max_length=100)
    speech_style: list[str] | None = Field(None, max_length=100)
    behavior_rules: list[str] | None = Field(None, max_length=100)
    relationship: dict[str, str] | None = Field(None, max_length=30)
    system_prompt: str | None = Field(None, max_length=32000)
    welcome_message: str | None = Field(None, max_length=8000)
    tagline: str | None = Field(None, max_length=500)


class ActiveCharacter(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")


def bounded_config(value: dict) -> None:
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_CARD_JSON:
        raise HTTPException(413, "角色设定超过 256 KB")


def save(
    request: Request, character: Character, avatar: bytes | None = None, *, activate: bool = True
) -> Character:
    try:
        request.app.state.characters.import_character(character, avatar, activate=activate)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "角色配置保存失败，请检查数据目录权限") from exc
    return character


@router.get("/api/characters")
def list_characters(request: Request) -> dict:
    loader = request.app.state.characters
    return {"active_id": loader.active_id, "characters": list(loader.characters.values())}


@router.post("/api/characters/import", response_model=Character)
def import_card(
    payload: CardFilePayload,
    request: Request,
    x_companion_control_token: str | None = Header(default=None),
) -> Character:
    require_control(x_companion_control_token)
    try:
        raw = base64.b64decode(payload.data_base64, validate=True)
        if len(raw) > MAX_CARD_FILE:
            raise HTTPException(413, "角色卡文件超过 10 MB")
        character, avatar = parse_card(raw, payload.filename)
        bounded_config(character.model_dump())
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(422, "角色卡导入失败：" + str(exc)) from exc
    return save(request, character, avatar)


@router.post("/api/characters/custom", response_model=Character, status_code=201)
def create_custom(
    payload: CustomCharacterPayload,
    request: Request,
    x_companion_control_token: str | None = Header(default=None),
) -> Character:
    require_control(x_companion_control_token)
    values = payload.model_dump()
    bounded_config(values)
    if not payload.name.strip():
        raise HTTPException(422, "角色名字不能为空")
    profile_fields = {
        key: values.pop(key)
        for key in ("identity", "personality", "speech_style", "behavior_rules", "relationship")
    }
    values["name"] = payload.name.strip()
    values["system_prompt"] = payload.system_prompt.strip() or "依据用户自定义的角色资料参与对话。"
    return save(
        request,
        Character(
            id="custom_" + uuid4().hex[:20],
            source="用户自定义",
            card_format="custom",
            profile=CharacterProfile(**profile_fields),
            **values,
        ),
    )


@router.put("/api/characters/active", response_model=Character)
def activate_character(
    payload: ActiveCharacter,
    request: Request,
    x_companion_control_token: str | None = Header(default=None),
) -> Character:
    require_control(x_companion_control_token)
    try:
        return request.app.state.characters.activate(payload.id)
    except LookupError as exc:
        raise HTTPException(404, "未找到角色") from exc
    except OSError as exc:
        raise HTTPException(503, "角色选择保存失败") from exc


@router.get("/api/characters/{character_id}", response_model=Character)
def get_character(character_id: str, request: Request) -> Character:
    character = request.app.state.characters.get(character_id)
    if character is None:
        raise HTTPException(404, "未找到角色")
    return character


@router.patch("/api/characters/{character_id}", response_model=Character)
def update_character(
    character_id: str,
    payload: CharacterPatch,
    request: Request,
    x_companion_control_token: str | None = Header(default=None),
) -> Character:
    require_control(x_companion_control_token)
    character = get_character(character_id, request)
    if character_id == "assistant":
        raise HTTPException(409, "通用助手保持空白设定；请创建自己的角色")
    values = payload.model_dump(exclude_none=True)
    bounded_config(values)
    if "name" in values and not values["name"].strip():
        raise HTTPException(422, "角色名字不能为空")
    profile = character.profile.model_dump()
    for key in ("identity", "personality", "speech_style", "behavior_rules", "relationship"):
        if key in values:
            profile[key] = values.pop(key)
    if "system_prompt" in values:
        values["system_prompt"] = (
            values["system_prompt"].strip() or "依据用户自定义的角色资料参与对话。"
        )
    updated = Character.model_validate({**character.model_dump(), **values, "profile": profile})
    bounded_config(updated.model_dump())
    return save(request, updated, activate=request.app.state.characters.active_id == character_id)


@router.get("/api/characters/{character_id}/avatar")
def character_avatar(character_id: str, request: Request):
    character = get_character(character_id, request)
    directory = request.app.state.characters.user_directory
    path = directory / "avatars" / f"{character.id}.png" if directory else None
    if path is None or not path.is_file():
        raise HTTPException(404, "角色没有头像")
    return FileResponse(path, media_type="image/png", headers={"X-Content-Type-Options": "nosniff"})
