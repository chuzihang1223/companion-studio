"""Normalize local JSON and Tavern V2/V3 cards to our character format."""

import json
from uuid import uuid4

from app.services.character.card_png import MAX_CARD_JSON, PNG_SIGNATURE, read_png_card
from app.services.character.loader import Character, CharacterProfile


def card_text(data: dict, name: str, limit: int = 16000) -> str:
    value = data.get(name, "")
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"角色卡的 {name} 必须为不超过 {limit} 字符的文本")
    return value.strip()


def parse_card(raw: bytes, filename: str) -> tuple[Character, bytes | None]:
    png = raw.startswith(PNG_SIGNATURE)
    if png:
        payload = read_png_card(raw)
    else:
        if not filename.casefold().endswith(".json") or len(raw) > MAX_CARD_JSON:
            raise ValueError("请选择不超过 256 KB 的 JSON，或不超过 10 MB 的 PNG 角色卡")
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise ValueError("角色卡不是有效的 UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("角色卡必须包含 JSON 对象")
    identifier = "import_" + uuid4().hex[:20]
    if isinstance(payload.get("profile"), dict):
        # A card's ID cannot overwrite another imported card or the default assistant.
        safe = {**payload, "id": identifier, "avatar_url": "", "card_format": "native_json"}
        character = Character.model_validate(safe)
        if not character.name.strip():
            raise ValueError("角色卡缺少角色名字")
        character.name = character.name.strip()
    else:
        spec = payload.get("spec")
        if spec not in {None, "chara_card_v2", "chara_card_v3"}:
            raise ValueError("尚不支持此角色卡规格；请使用本应用 JSON 或 Tavern V2/V3")
        data = payload.get("data") if spec else payload
        if not isinstance(data, dict):
            raise ValueError("角色卡 data 字段无效")
        name = card_text(data, "name", 100)
        if not name:
            raise ValueError("角色卡缺少角色名字")
        description = card_text(data, "description")
        personality = card_text(data, "personality")
        system = card_text(data, "system_prompt", 32000)
        scenario = card_text(data, "scenario")
        examples = card_text(data, "mes_example", 24000)
        instructions = card_text(data, "post_history_instructions", 8000)
        first = card_text(data, "first_mes", 8000).replace("{{char}}", name)
        warnings = []
        if data.get("character_book"):
            warnings.append("已导入基础设定；此版本不执行角色卡内的世界书触发规则。")
        if data.get("extensions") or data.get("assets"):
            warnings.append("角色卡的扩展脚本、外部图片或资源未执行、未下载。")
        character = Character(
            id=identifier,
            name=name,
            source="用户导入",
            card_format=spec or "tavern_json",
            welcome_message=first,
            system_prompt=system or "依据用户导入的角色资料参与对话。",
            profile=CharacterProfile(
                identity=description, personality=[personality] if personality else []
            ),
            scenario=scenario,
            style_examples=examples,
            post_history_instructions=instructions,
            import_warnings=warnings,
        )
    return character, raw if png else None
