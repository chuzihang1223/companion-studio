"""Local V8.1 boundary checks. No external LLM, TTS or desktop input."""

import asyncio
import json
import os
import sqlite3
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.migrations import migrate
from app.database.session import Base
from app.models import Conversation
from app.services import agent_service, permission_policy, work_state
from app.services.agent_tools import ToolExecutor
from app.services.character.loader import CharacterLoader
from app.services.desktop.runtime import AgentTaskRuntime
from app.services.trusted_actions import TrustedActionStore
from app.services.workspace_service import search, tree


@pytest.mark.parametrize("mode", ["automatic", "smart", "ask"])
def test_policy_critical_cannot_be_overridden_and_code_always_confirmed(
    tmp_path, monkeypatch, mode
):
    monkeypatch.setattr(permission_policy, "read_preferences", lambda: {"permission_mode": mode})
    store = TrustedActionStore(tmp_path / "grants.json")
    for action in [
        "system.admin",
        "security.disable",
        "app.launch:unregistered",
        "app.close:unregistered",
    ]:
        assert store.decision(action) == "deny"
        assert not store.permitted(action, "call", {"call"}, "task")
    for action in ["code.run:project", "coding.execute:project", "workspace.delete:project"]:
        assert store.decision(action) == "ask"
        assert not store.permitted(action, "call", set())
        assert store.permitted(action, "call", {"call"})
        with pytest.raises(ValueError):
            store.set(action, "task", "task")
    assert store.decision("workspace.read:project") == "allow"
    assert store.decision("app.launch:netease") == ("ask" if mode == "ask" else "allow")


def test_task_grants_cover_same_category_only_and_expire(tmp_path, monkeypatch):
    monkeypatch.setattr(permission_policy, "read_preferences", lambda: {"permission_mode": "ask"})
    store = TrustedActionStore(tmp_path / "grants.json")
    store.set("app.launch:netease", "task", "work-a")
    assert store.allowed("app.launch:vscode", "work-a")
    assert not store.allowed("app.focus:netease", "work-a")
    assert not store.allowed("app.launch:netease", "work-b")
    store.finish_task("work-a")
    assert not store.allowed("app.launch:netease", "work-a")


def test_legacy_grants_preserved_without_overriding_new_modes(tmp_path, monkeypatch):
    path = tmp_path / "trusted-actions.json"
    original = b'{"actions":{"app.launch:netease":"always"}}'
    path.write_bytes(original)
    store = TrustedActionStore(path)
    store.archive_legacy()
    store.archive_legacy()
    assert path.read_bytes() == original
    assert path.with_name("trusted-actions.legacy-v8.json").read_bytes() == original
    monkeypatch.setattr(permission_policy, "read_preferences", lambda: {"permission_mode": "ask"})
    assert not store.allowed("app.launch:netease")


def test_workspace_operations_guard_paths_and_preserve_backups(tmp_path):
    root = tmp_path / "project"
    executor = ToolExecutor(root)
    call = lambda name, **args: executor.execute({"name": "workspace." + name, "arguments": args})
    call("create", path="src/note.txt", content="Hello scene")
    assert call("read", path="src/note.txt")["content"] == "Hello scene"
    assert call("search", query="scene")["matches"][0]["path"].endswith("note.txt")
    assert "src" in [row["name"] for row in call("list", path=".")["items"]]
    assert "+Changed" in call("write", path="src/note.txt", content="Changed")["diff"]
    assert (root / "src/note.txt.agent-backup").read_text() == "Hello scene"
    call("rename", path="src/note.txt", destination="src/renamed.txt")
    call("delete", path="src/renamed.txt")
    assert not (root / "src/renamed.txt").exists()
    assert (root / "src/renamed.txt.deleted-backup").read_text() == "Changed"
    for path in [
        "../outside",
        "C:relative.txt",
        "C:/outside.txt",
        ".ENV",
        "sub/.aws/token",
        "auth.json",
        "file.txt:secret",
    ]:
        with pytest.raises(ValueError):
            call("read", path=path)


def test_tree_search_are_bounded_and_hide_secrets_and_large_directories(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".ENV").write_text("needle")
    (root / "node_modules").mkdir()
    (root / "node_modules/needle.txt").write_text("needle")
    (root / "normal.txt").write_text("needle")
    assert [r["name"] for r in tree(str(root))["items"]] == ["normal.txt"]
    assert {r["name"] for r in tree(str(root), show_ignored=True)["items"]} == {
        "normal.txt",
        "node_modules",
    }
    assert [r["path"] for r in search(str(root), "needle")["matches"]] == ["normal.txt"]
    for number in range(210):
        (root / f"item-{number}.txt").write_text("small")
    result = tree(str(root))
    assert len(result["items"]) == 200 and result["truncated"]


def test_symlinks_cannot_escape_or_alias_secret_files(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (root / ".env").write_text("private")
    try:
        (root / "escape.txt").symlink_to(outside)
        (root / "alias.txt").symlink_to(root / ".env")
        (root / "loop").symlink_to(root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Windows account cannot create symlinks: {exc.winerror}")
    executor = ToolExecutor(root)
    for name in ["escape.txt", "alias.txt"]:
        with pytest.raises(ValueError):
            executor._path(name)
    assert not search(str(root), "private")["matches"]


@pytest.mark.skipif(os.name != "nt", reason="Windows junction boundary")
def test_windows_junction_cannot_escape_selected_workspace(tmp_path):
    root, outside = tmp_path / "project", tmp_path / "sibling"
    root.mkdir()
    outside.mkdir()
    (outside / "private.txt").write_text("must not be read")
    junction = root / "linked"
    env = {**os.environ, "V81_TEST_JUNCTION": str(junction), "V81_TEST_TARGET": str(outside)}
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference='Stop'; $null=New-Item -ItemType Junction -Path $env:V81_TEST_JUNCTION -Target $env:V81_TEST_TARGET",
        ],
        capture_output=True,
        timeout=15,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    with pytest.raises(ValueError):
        ToolExecutor(root)._path("linked/private.txt")
    assert tree(str(root))["items"] == []
    assert search(str(root), "must not")["matches"] == []


@pytest.fixture
def workspace_api(tmp_path, monkeypatch):
    from app.api import agent, work
    from app.core.config import settings

    engine = create_engine(
        f"sqlite:///{(tmp_path / 'work.db').as_posix()}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(work_state, "SessionLocal", factory)
    monkeypatch.setattr(agent_service, "SessionLocal", factory)
    from app.services.coding import documents

    monkeypatch.setattr(documents, "SessionLocal", factory)
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", "isolated-test-token")
    monkeypatch.setattr(permission_policy, "read_preferences", lambda: {"permission_mode": "smart"})
    app = FastAPI()
    app.include_router(work.router)
    app.include_router(agent.router)
    app.state.work_state = work_state.WorkState()
    app.state.agent_service = agent_service.AgentService()
    app.state.agent_service.executor = ToolExecutor(tmp_path / "agent")
    loader = CharacterLoader(settings.project_root / "characters")
    loader.load()
    app.state.characters = loader
    with factory() as db:
        db.add_all(
            [
                Conversation(
                    id=key,
                    character_id="assistant",
                    conversation_type="work",
                    preferred_mode="work",
                )
                for key in ["a", "b"]
            ]
        )
        db.commit()
    with TestClient(app) as client:
        yield app, client, factory
    engine.dispose()


def test_workspace_private_persistent_independent_and_locked_during_task(workspace_api, tmp_path):
    app, client, factory = workspace_api
    roots = [tmp_path / "folder-a", tmp_path / "folder-b"]
    for root in roots:
        root.mkdir()
        (root / "note.txt").write_text(root.name)
    headers = {"x-companion-control-token": "isolated-test-token"}
    assert (
        client.post(
            "/api/work/workspace", json={"conversation_id": "a", "root": str(roots[0])}
        ).status_code
        == 403
    )
    ids = []
    for key, root in zip(["a", "b"], roots):
        reply = client.post(
            "/api/work/workspace", json={"conversation_id": key, "root": str(root)}, headers=headers
        )
        assert reply.status_code == 200, reply.text
        ids.append(reply.json()["id"])
    assert work_state.WorkState().for_conversation("a")["workspace_root"] == str(roots[0])
    assert work_state.WorkState().for_conversation("b")["workspace_root"] == str(roots[1])
    path = f"/api/work/{ids[0]}/files"
    assert client.get(path).status_code == 403
    assert (
        client.get(path, headers=headers, params={"path": "note.txt", "read": True}).json()[
            "content"
        ]
        == "folder-a"
    )
    assert (
        client.get(
            path, headers=headers, params={"path": "../folder-b/note.txt", "read": True}
        ).status_code
        == 422
    )
    app.state.work_state.emit(ids[0], "started", {}, status="executing")
    reply = client.post(
        "/api/work/workspace", json={"conversation_id": "a", "root": str(roots[1])}, headers=headers
    )
    assert reply.status_code == 409
    assert app.state.work_state.snapshot(ids[0])["workspace_root"] == str(roots[0])
    app.state.work_state.stop(ids[0])
    assert (
        client.post(
            "/api/work/workspace",
            json={"conversation_id": "a", "root": str(roots[1])},
            headers=headers,
        ).status_code
        == 200
    )


def test_agent_inherits_saved_workspace_and_rejects_unsaved_root(workspace_api, tmp_path):
    app, client, factory = workspace_api
    root = tmp_path / "selected"
    root.mkdir()
    (root / "note.txt").write_text("selected content")

    class FakeLLM:
        async def plan_tools(self, prompt, tools):
            assert str(root) in prompt[0]["content"]
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "read1",
                        "type": "function",
                        "function": {"name": "workspace_read", "arguments": '{"path":"note.txt"}'},
                    }
                ],
            }

        async def chat(self, prompt):
            assert any(
                "selected content" in str(row.get("content"))
                for row in prompt
                if row["role"] == "tool"
            )
            return "文件读到了。"

    app.state.llm_service = FakeLLM()
    headers = {"x-companion-control-token": "isolated-test-token"}
    assert (
        client.post(
            "/api/work/workspace", json={"conversation_id": "a", "root": str(root)}, headers=headers
        ).status_code
        == 200
    )
    request = {
        "character_id": "assistant",
        "conversation_id": "a",
        "message": "读取项目文件 note.txt",
    }
    plan = client.post("/api/agent/plan", json=request)
    assert plan.status_code == 200, plan.text
    result = client.post(
        "/api/agent/execute", json={"plan_id": plan.json()["plan_id"], "voice_enabled": False}
    )
    assert result.status_code == 200, result.text
    assert client.get("/api/work/by-conversation/a").json()["workspace_root"] == str(root)
    assert (
        client.post(
            "/api/agent/plan", json={**request, "workspace_folder": str(tmp_path)}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/agent/plan",
            json={**request, "conversation_id": None, "workspace_folder": str(root)},
        ).status_code
        == 422
    )


def test_runtime_denies_unknown_executable_even_with_approved_id(tmp_path, monkeypatch):
    from app.services.desktop import runtime as runtime_module

    monkeypatch.setattr(
        permission_policy, "read_preferences", lambda: {"permission_mode": "automatic"}
    )
    monkeypatch.setattr(
        runtime_module, "trusted_actions", TrustedActionStore(tmp_path / "grants.json")
    )
    state = SimpleNamespace(is_stopped=lambda session: False, emit=lambda *a, **k: None)
    runtime = AgentTaskRuntime(SimpleNamespace(state=SimpleNamespace(work_state=state)))
    context = SimpleNamespace(
        task_id=str(uuid4()),
        resume_verified=None,
        work_session_id="s",
        user_text="打开未知应用",
        executor=ToolExecutor(tmp_path / "workspace"),
        calls=[
            {
                "id": "c",
                "name": "app.launch",
                "arguments": {"app": "unregistered"},
                "permission_required": False,
            }
        ],
    )

    async def forbidden(*args):
        pytest.fail("A denied operation reached the desktop bridge")

    runtime.execute_call = forbidden
    observations, details, verified = asyncio.run(runtime.run(context, {"c"}))
    assert not verified and details[0]["status"] == "failed"


def test_v8_workspace_migration_additive_and_idempotent(tmp_path):
    target = tmp_path / "old.db"
    with sqlite3.connect(target) as db:
        db.execute(
            "CREATE TABLE work_sessions(id TEXT PRIMARY KEY,conversation_id TEXT,goal TEXT,status TEXT,plan_json TEXT,created_at TEXT,updated_at TEXT)"
        )
        db.execute(
            "INSERT INTO work_sessions VALUES (?,NULL,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            (
                "old-work",
                "keep original goal",
                "done",
                json.dumps({"workspace": str(tmp_path / "selected")}),
            ),
        )
    engine = create_engine(f"sqlite:///{target.as_posix()}")
    migrate(engine)
    migrate(engine)
    with sqlite3.connect(target) as db:
        row = db.execute(
            "SELECT goal,workspace_root FROM work_sessions WHERE id=?", ("old-work",)
        ).fetchone()
        assert row == ("keep original goal", str(tmp_path / "selected"))
        assert db.execute("SELECT COUNT(*) FROM work_sessions").fetchone()[0] == 1
    engine.dispose()
