"""Isolated clean-profile and local character-card acceptance checks."""

import asyncio
import base64
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import zlib

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.character import router
from app.services.character.card_png import PNG_SIGNATURE, MAX_CARD_JSON
from app.services.character.cards import parse_card
from app.services.character.loader import (
    Character,
    CharacterLoader,
    CharacterProfile,
    neutral_assistant,
)
from app.services.character.prompt_builder import CharacterPromptBuilder
from app.services.character.self_reference import SelfReferenceResolver
from app.services.llm.openai_provider import LLMConfigurationError, OpenAIProvider

TOKEN = "isolated-character-test-control"
HEADERS = {"X-Companion-Control-Token": TOKEN}


def tavern(spec="chara_card_v2"):
    return {
        "spec": spec,
        "spec_version": "2.0" if spec.endswith("v2") else "3.0",
        "data": {
            "name": "星野",
            "description": "{{char}} 是用户定义的冷静航海员。",
            "personality": "冷静，有主见",
            "scenario": "{{user}} 来到港口。",
            "first_mes": "你好，我是 {{char}}。",
            "mes_example": "{{user}}: 风大吗？\n{{char}}: 风很平稳。",
            "system_prompt": "保持航海员的角色设定。",
            "post_history_instructions": "回答简洁，不自称恋人。",
            "character_book": {"entries": [{"content": "世界书不会自动执行"}]},
            "extensions": {"script": "must_not_execute()"},
            "assets": [{"uri": "https://invalid.test/avatar"}],
        },
    }


def png_card(payload, kind=b"tEXt", key=b"chara"):
    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    text = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode())
    if kind == b"zTXt":
        data = key + b"\0\0" + zlib.compress(text)
    elif kind == b"iTXt":
        data = key + b"\0\1\0\0\0" + zlib.compress(text)
    else:
        data = key + b"\0" + text
    return (
        PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(kind, data)
        + chunk(b"IDAT", zlib.compress(b"\0\0\0\0\0"))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def clean_loader(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_CLEAN_DISTRIBUTION", "1")
    bundled = Path(__file__).resolve().parents[2] / "characters"
    loader = CharacterLoader(bundled, tmp_path / "characters")
    loader.load()
    return loader


@pytest.fixture
def client(clean_loader, monkeypatch):
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", TOKEN)
    app = FastAPI()
    app.state.characters = clean_loader
    app.include_router(router)
    with TestClient(app) as result:
        yield result


def test_clean_boot_ignores_bundled_old_personas_and_has_no_avatar(clean_loader):
    assert set(clean_loader.characters) == {"assistant"}
    assert clean_loader.active_id == "assistant"
    assistant = clean_loader.get("assistant")
    assert assistant.profile == CharacterProfile()
    assert assistant.avatar_url == "" and assistant.welcome_message == ""


def test_clean_boot_works_without_bundled_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_CLEAN_DISTRIBUTION", "1")
    loader = CharacterLoader(tmp_path / "absent", tmp_path / "user")
    loader.load()
    assert loader.get("assistant") == neutral_assistant()


def test_default_prompt_has_no_predefined_persona_or_relationship(clean_loader):
    prompt = CharacterPromptBuilder().build(clean_loader.get("assistant"), [], "你好，我想你了")
    system = prompt[0]["content"]
    assert clean_loader.get("assistant").profile.identity == ""
    assert clean_loader.get("assistant").profile.relationship == {}
    assert "ROMANTIC_AFFECTION" not in system and "RELATIONSHIP_STATE" not in system
    assert "DIRECT_AFFECTION_RESPONSE" not in system
    assert prompt[-1]["content"] == "你好，我想你了"


@pytest.mark.parametrize("spec", ["chara_card_v2", "chara_card_v3"])
def test_json_cards_preserve_user_context_and_do_not_fetch_assets(spec):
    character, avatar = parse_card(
        json.dumps(tavern(spec), ensure_ascii=False).encode(), "role.json"
    )
    assert character.name == "星野" and character.id.startswith("import_") and avatar is None
    assert character.card_format == spec and character.welcome_message == "你好，我是 星野。"
    assert len(character.import_warnings) == 2
    prompt = CharacterPromptBuilder().build(
        character,
        [{"role": "user", "content": "原记录"}],
        "风向如何？",
        personalization={"user_name": "小舟"},
    )
    assert "星野 是用户定义的冷静航海员" in prompt[0]["content"]
    assert "小舟 来到港口" in prompt[0]["content"] and "CARD_STYLE_EXAMPLES" in prompt[0]["content"]
    assert (
        "must_not_execute" not in prompt[0]["content"]
        and "invalid.test" not in prompt[0]["content"]
    )
    assert "不强制亲密" in prompt[0]["content"] and prompt[1]["content"] == "原记录"


@pytest.mark.parametrize(
    "kind,key,spec",
    [
        (b"tEXt", b"chara", "chara_card_v2"),
        (b"zTXt", b"chara", "chara_card_v2"),
        (b"iTXt", b"ccv3", "chara_card_v3"),
    ],
)
def test_png_v2_v3_text_formats(kind, key, spec):
    raw = png_card(tavern(spec), kind, key)
    character, avatar = parse_card(raw, "character.png")
    assert character.card_format == spec and avatar == raw


def test_native_card_cannot_overwrite_assistant():
    raw = neutral_assistant().model_dump_json().encode()
    character, avatar = parse_card(raw, "assistant.json")
    assert character.id.startswith("import_") and character.id != "assistant" and avatar is None
    assert character.avatar_url == ""


@pytest.mark.parametrize(
    "raw,name",
    [
        (b"invalid", "bad.json"),
        (b"[]", "bad.json"),
        (b"{}", "bad.json"),
        (b"{}", "script.py"),
        (b"x" * (MAX_CARD_JSON + 1), "large.json"),
        (PNG_SIGNATURE + b"broken", "broken.png"),
    ],
    ids=[
        "invalid-json",
        "array-json",
        "missing-name",
        "script-extension",
        "oversized-json",
        "broken-png",
    ],
)
def test_invalid_cards_fail_without_writes(raw, name):
    with pytest.raises(ValueError):
        parse_card(raw, name)


def test_png_crc_and_compression_bomb_are_rejected():
    corrupt = bytearray(png_card(tavern()))
    corrupt[29] ^= 1
    with pytest.raises(ValueError, match="校验"):
        parse_card(bytes(corrupt), "bad.png")
    from app.services.character.card_png import limited_decompress

    with pytest.raises(ValueError, match="过大"):
        limited_decompress(zlib.compress(b"a" * (MAX_CARD_JSON * 3)))
    with pytest.raises(ValueError, match="损坏"):
        limited_decompress(b"not-zlib")


def test_custom_edit_switch_and_restart_are_persistent(client, clean_loader):
    assert client.get("/api/character").json()["id"] == "assistant"
    assert client.post("/api/characters/custom", json={"name": "星野"}).status_code == 403
    response = client.post(
        "/api/characters/custom",
        headers=HEADERS,
        json={
            "name": "星野",
            "identity": "冷静的航海员",
            "personality": ["冷静"],
            "speech_style": ["简洁"],
            "relationship": {"user_role": "同行者"},
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    identifier = created["id"]
    assert client.get("/api/characters").json()["active_id"] == identifier
    assert (
        client.get(f"/api/characters/{identifier}").json()["profile"]["identity"] == "冷静的航海员"
    )
    assert (
        client.patch(
            f"/api/characters/{identifier}",
            headers=HEADERS,
            json={"name": "星野改", "system_prompt": ""},
        ).status_code
        == 200
    )
    assert (
        client.put("/api/characters/active", headers=HEADERS, json={"id": "assistant"}).status_code
        == 200
    )
    assert (
        client.patch(
            "/api/characters/assistant", headers=HEADERS, json={"name": "破坏默认角色"}
        ).status_code
        == 409
    )
    assert (
        client.put("/api/characters/active", headers=HEADERS, json={"id": "../bad"}).status_code
        == 422
    )
    restarted = CharacterLoader(clean_loader.directory, clean_loader.user_directory)
    restarted.load()
    assert restarted.active_id == "assistant" and restarted.get(identifier).name == "星野改"


def test_api_png_import_avatar_and_restart(client, clean_loader):
    raw = png_card(tavern("chara_card_v3"), key=b"ccv3")
    response = client.post(
        "/api/characters/import",
        headers=HEADERS,
        json={"filename": "角色.png", "data_base64": base64.b64encode(raw).decode()},
    )
    assert response.status_code == 200, response.text
    character = response.json()
    assert character["avatar_url"].endswith("/avatar")
    avatar = client.get(character["avatar_url"])
    assert avatar.content == raw and avatar.headers["content-type"] == "image/png"
    restarted = CharacterLoader(clean_loader.directory, clean_loader.user_directory)
    restarted.load()
    assert restarted.active_id == character["id"]
    assert restarted.get(character["id"]).avatar_url == character["avatar_url"]
    # Importing the same file twice creates independent IDs instead of losing edits.
    another = client.post(
        "/api/characters/import",
        headers=HEADERS,
        json={"filename": "角色.png", "data_base64": base64.b64encode(raw).decode()},
    ).json()
    assert another["id"] != character["id"]


def test_invalid_api_payloads_leave_default_unchanged(client):
    assert (
        client.post("/api/characters/custom", headers=HEADERS, json={"name": "  "}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/characters/import",
            headers=HEADERS,
            json={"filename": "x.json", "data_base64": "not valid base64!"},
        ).status_code
        == 422
    )
    assert client.get("/api/characters").json()["active_id"] == "assistant"
    assert len(client.get("/api/characters").json()["characters"]) == 1


def test_legacy_import_endpoint_cannot_overwrite_default_in_clean_build(client):
    response = client.post(
        "/api/character/import", headers=HEADERS, json={"config": neutral_assistant().model_dump()}
    )
    assert response.status_code == 200 and response.json()["id"].startswith("import_")
    assert client.get("/api/characters/assistant").json()["name"] == "通用助手"


def test_unconfigured_model_fails_locally_with_clear_instruction():
    provider = OpenAIProvider("", "", "https://api.invalid.test/v1")
    with pytest.raises(LLMConfigurationError, match="设置"):
        provider._validate()


def test_software_identity_is_independent_of_user_character():
    text = SelfReferenceResolver().context("这个软件技术上是怎么实现的？")
    assert "Companion Studio" in text and "Electron" in text
    assert "没有已知的具体个人开发者" in text


def test_partial_personalization_save_does_not_introduce_romance(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.database.session import Base
    from app.services.character.personalization import read_settings, save_settings

    monkeypatch.setenv("COMPANION_CLEAN_DISTRIBUTION", "1")
    engine = create_engine(f"sqlite:///{(tmp_path / 'profile.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        saved = save_settings(db, "assistant", {"user_name": "小舟"})
        assert saved["intimacy"] == "natural" and saved["relationship"] == ""
        assert read_settings(db, "assistant")["intimacy"] == "natural"
    engine.dispose()


def test_blank_environment_model_can_initialize_without_any_network(monkeypatch):
    from app.services.llm import configuration, service

    empty = SimpleNamespace(
        llm_provider="openai", openai_base_url="", openai_api_key="", openai_model=""
    )
    monkeypatch.setattr(configuration, "settings", empty)
    monkeypatch.setattr(service, "settings", empty)
    llm = service.create_llm_service()
    assert llm.config.model == "unconfigured" and llm.provider.model == ""
    with pytest.raises(LLMConfigurationError, match="设置"):
        asyncio.run(llm.chat([{"role": "user", "content": "你好"}]))
