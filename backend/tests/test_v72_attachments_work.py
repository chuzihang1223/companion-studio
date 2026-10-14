import base64
import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pypdf import PdfWriter

from app.services.attachment_service import AttachmentService
from app.services.work_state import WorkState
from app.database.migrations import migrate
from app.database.session import engine
from app.api.fish_settings import generate_v72_scenario


def source(name: str, raw: bytes):
    return SimpleNamespace(name=name, data_base64=base64.b64encode(raw).decode("ascii"))


def test_text_attachment_extracts_bounded_relevant_context(tmp_path):
    service = AttachmentService(tmp_path)
    items = service.prepare(
        [source("notes.md", ("普通说明\n" * 1000 + "关键错误 8080 端口占用\n").encode())],
        vision=False,
    )
    context = service.context(items, "检查端口占用")
    assert "8080" in context
    metadata = service.save(items)
    assert metadata[0]["name"] == "notes.md"
    assert (tmp_path / f"{metadata[0]['id']}.md").is_file()
    assert len(context) <= 12_000


def test_pdf_extraction_and_image_gate(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)
    service = AttachmentService(tmp_path)
    pdf = service.prepare([source("sample.pdf", output.getvalue())], vision=False)[0]
    assert pdf.media_type == "application/pdf" and pdf.kind == "file"
    with pytest.raises(ValueError, match="图片理解"):
        service.prepare([source("picture.png", b"not a picture")], vision=False)


def test_work_session_persists_permission_and_progress():
    migrate(engine)
    state = WorkState()
    id = state.create(
        "检查项目", None, [{"id": "call-1", "name": "code.run", "requires_confirmation": True}]
    )
    snapshot = state.snapshot(id)
    assert snapshot["status"] == "waiting_permission"
    assert snapshot["permissions"][0]["decision"] == "pending"
    assert snapshot["events"][-1]["type"] == "permission_requested"
    state.decide(id, set())
    state.emit(id, "tool_completed", {"name": "code.run", "status": "denied"}, step=2)
    assert state.snapshot(id)["events"][-1]["type"] == "tool_completed"
    assert state.snapshot(id)["permissions"][0]["decision"] == "denied"
    state.add_tab(id, "code", "demo.py", {"diff": "+" * 16_000})
    assert len(state.snapshot(id)["tabs"][0]["data"]["diff"]) == 16_000
    with pytest.raises(ValueError, match="32 KB"):
        state.add_tab(id, "code", "too-large.py", {"diff": "+" * 40_000})


def test_v72_listening_batch_refuses_paid_fish_model(monkeypatch):
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", "local-test-token")
    fish = SimpleNamespace(config=SimpleNamespace(model="s2.1-pro"), ready=True)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(fish_voice=fish)))
    with pytest.raises(HTTPException) as error:
        asyncio.run(generate_v72_scenario(1, request, "local-test-token"))
    assert error.value.status_code == 409
