"""Offline regression gates for the public edition's text and WAV paths."""

import asyncio
from io import BytesIO
from types import SimpleNamespace
import wave
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest

from app.api import fish_settings
from app.services.attachment_service import AttachmentService
from app.services.voice.fish_provider import FishAudioError, FishAudioProvider, FishConfig


def docx(xml: bytes) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return output.getvalue()


def test_docx_extracts_text_tabs_and_paragraphs_without_third_party_parser():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>第一段</w:t><w:tab/><w:t>字段</w:t></w:r></w:p>
      <w:p><w:r><w:t>第二段</w:t><w:br/><w:t>下一行</w:t></w:r></w:p></w:body>
    </w:document>""".encode("utf-8")
    assert AttachmentService._extract_docx(docx(xml)) == "第一段\t字段\n第二段\n下一行"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_docx_rejects_document_types_and_entity_expansion(encoding):
    xml = """<?xml version="1.0"?><!DOCTYPE document [<!ENTITY injected "secret">]>
    <document>&injected;</document>""".encode(encoding)
    with pytest.raises(ValueError, match="外部实体|UTF-8"):
        AttachmentService._extract_docx(docx(xml))


def test_docx_rejects_oversize_decompressed_document():
    archive = docx(b"<document>" + b"x" * (8 * 1024 * 1024) + b"</document>")
    assert len(archive) < 20_000
    with pytest.raises(ValueError, match="超过限制"):
        AttachmentService._extract_docx(archive)


def test_docx_rejects_missing_body_or_invalid_zip():
    with pytest.raises(ValueError, match="损坏或缺少正文"):
        AttachmentService._extract_docx(b"not a zip")
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("other.xml", "<document/>")
    with pytest.raises(ValueError, match="损坏或缺少正文"):
        AttachmentService._extract_docx(output.getvalue())


def wav_audio() -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(24000)
        writer.writeframes(b"\x01\x00" * 4000)
    return output.getvalue()


def test_fish_authorization_endpoint_saves_native_wav(tmp_path, monkeypatch):
    monkeypatch.setattr(fish_settings, "settings", SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(fish_settings, "require_control", lambda _token: None)
    captured = []

    def response(request):
        captured.append(request)
        return httpx.Response(200, content=wav_audio())

    provider = FishAudioProvider(
        FishConfig(api_key="mock-key", reference_id="fixture-voice"), httpx.MockTransport(response)
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(fish_voice=provider)))
    result = asyncio.run(fish_settings.test_fish(request, "fixture-token"))
    assert result["ok"] and provider.authorized
    assert len(captured) == 1
    assert (tmp_path / "voice_test" / "fish_auth_test.wav").read_bytes() == wav_audio()
    assert list((tmp_path / "voice_test").glob("*.mp3")) == []
    served = fish_settings.test_fish_audio()
    assert served.media_type == "audio/wav"
    assert served.path.name == "fish_auth_test.wav"


@pytest.mark.parametrize("content", [b"ID3" + b"x" * 600, b"RIFF", b""])
def test_fish_wav_generation_rejects_nonwav_without_optional_decoder(content):
    provider = FishAudioProvider(
        FishConfig(api_key="mock-key", reference_id="fixture-voice"),
        httpx.MockTransport(lambda _request: httpx.Response(200, content=content)),
    )
    with pytest.raises(FishAudioError, match="音频格式无效"):
        provider.generate_wav("验证声音。")


def test_fish_comparison_serves_wav(tmp_path, monkeypatch):
    monkeypatch.setattr(fish_settings, "settings", SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(fish_settings, "require_control", lambda _token: None)
    provider = FishAudioProvider(
        FishConfig(api_key="mock-key", reference_id="fixture-voice"),
        httpx.MockTransport(lambda _request: httpx.Response(200, content=wav_audio())),
    )
    provider.authorized = True
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(fish_voice=provider)))
    result = asyncio.run(fish_settings.compare_fish(request, "fixture-token"))
    assert result["complete"] and len(result["samples"]) == 4
    assert len(list((tmp_path / "voice_test").glob("*.wav"))) == 4
    assert list((tmp_path / "voice_test").glob("*.mp3")) == []
    for sample in result["samples"]:
        served = fish_settings.compare_fish_audio(sample["name"])
        assert served.media_type == "audio/wav"


def test_public_loader_ignores_unselected_bundled_persona_even_with_legacy_env(
    tmp_path, monkeypatch
):
    from app.services.character.loader import CharacterLoader

    monkeypatch.setenv("COMPANION_CLEAN_DISTRIBUTION", "0")
    monkeypatch.setenv("COMPANION_DEFAULT_CHARACTER", "bundled_fixture")
    (tmp_path / "bundled_fixture.json").write_text(
        '{"invalid": "must never be read"}', encoding="utf-8"
    )
    loader = CharacterLoader(tmp_path)
    loader.load()
    assert set(loader.characters) == {"assistant"} and loader.active_id == "assistant"


@pytest.mark.parametrize("factory_kind", ["factory", "configured_class"])
def test_optional_onnx_adapter_loads_user_supplied_neutral_entrypoint(
    tmp_path, monkeypatch, factory_kind
):
    from app.services.voice import onnx_provider

    engine = object()
    seen = []

    def create_engine(directory, providers):
        seen.append((directory, providers))
        return engine

    model = SimpleNamespace(
        select_providers=lambda device: [device], load_audio=lambda *_args: None
    )
    if factory_kind == "factory":
        model.create_engine = create_engine
    else:
        monkeypatch.setenv("VOICE_ONNX_ENGINE_CLASS", "CustomLocalEngine")
        model.CustomLocalEngine = create_engine
    monkeypatch.setattr(
        onnx_provider.importlib.util,
        "spec_from_file_location",
        lambda *_args: SimpleNamespace(loader=SimpleNamespace(exec_module=lambda _module: None)),
    )
    monkeypatch.setattr(onnx_provider.importlib.util, "module_from_spec", lambda _spec: model)
    monkeypatch.setattr(
        onnx_provider,
        "ReferenceManager",
        lambda *_args: SimpleNamespace(available=lambda: {"neutral"}),
    )
    provider = onnx_provider.ONNXVoiceProvider(tmp_path, tmp_path)
    provider.warm()
    assert provider.engine is engine and seen == [(tmp_path, ["cpu"])]
    provider.warm()
    assert len(seen) == 1
