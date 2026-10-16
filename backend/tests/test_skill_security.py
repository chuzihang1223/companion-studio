import shutil

import pytest

from app.services.mcp_ext.contracts import MCPError
from app.services.skills.manager import AgentSkillManager, safe_file
from app.services.skills.parser import parse_skill
from test_mcp_connection import host

VALID = "---\nname: project-review\ndescription: Review project quality and search code 代码质量检查\nmetadata:\n  version: '1.0'\n  companion-capabilities: read_project_files\n---\nRead selected project files. Never execute scripts.\n"


@pytest.mark.parametrize(
    "text",
    [
        "name: no-frontmatter",
        VALID.replace("project-review", "Project-review"),
        VALID.replace("project-review", "project--review"),
        VALID.replace("name: project-review", "name: project-review\nname: project-review"),
        VALID.replace("metadata:", "unknown: true\nmetadata:"),
        VALID.replace("metadata:", "license: &a hello\ncompatibility: *a\nmetadata:"),
        VALID.replace("version: '1.0'", "version: 1.0"),
        VALID.replace(
            "description: Review project quality and search code 代码质量检查", "description: ''"
        ),
    ],
)
def test_invalid_frontmatter_is_rejected(text):
    with pytest.raises(MCPError, match="SKILL_INVALID"):
        parse_skill(text, "project-review")


def test_import_enable_progressive_load_dependency_disable_and_update(tmp_path, monkeypatch):
    store = host(tmp_path).store
    skills = AgentSkillManager(store, tmp_path / "installed")
    folder = tmp_path / "project-review"
    folder.mkdir()
    (folder / "SKILL.md").write_text(VALID, encoding="utf-8")
    scripts = folder / "scripts"
    scripts.mkdir()
    (scripts / "never.py").write_text("raise RuntimeError('must not run')", encoding="utf-8")
    preview = skills.preview(folder)
    item = skills.import_folder(folder, preview["content_hash"])
    assert not item["enabled"]
    assert item["source"] == "local-import"
    assert "instructions" not in skills.listing()[0]
    assert skills.select("$project-review", {"workspace.read"})["skills"] == []
    skills.enable(item["id"], True)
    assert skills.select("$project-review", set())["missing"] == ["read_project_files"]
    loaded = []
    original = skills.read_file
    monkeypatch.setattr(
        skills, "read_file", lambda key, path: (loaded.append(path), original(key, path))[1]
    )
    assert skills.select("$project-review", {"workspace.read"})["context"].startswith(
        "Read selected"
    )
    assert loaded == ["SKILL.md"]
    skills.enable(item["id"], False)
    assert not skills.select("$project-review", {"workspace.read"})["skills"]
    (folder / "SKILL.md").write_text(VALID + "Updated.", encoding="utf-8")
    with pytest.raises(MCPError, match="SKILL_CHANGED_SINCE_PREVIEW"):
        skills.import_folder(folder, preview["content_hash"], skill_id=item["id"])
    updated = skills.import_folder(
        folder, skills.preview(folder)["content_hash"], skill_id=item["id"]
    )
    assert updated["content_hash"] != item["content_hash"]
    assert not updated["enabled"]
    with pytest.raises(MCPError, match="SKILL_NAME_CONFLICT"):
        skills.import_folder(folder, skills.preview(folder)["content_hash"])
    skills.delete(item["id"])
    assert folder.is_dir() and not skills.listing()


def test_paths_private_files_and_mutation_are_blocked(tmp_path):
    skills = AgentSkillManager(host(tmp_path).store, tmp_path / "installed")
    folder = tmp_path / "project-review"
    folder.mkdir()
    (folder / "SKILL.md").write_text(VALID, encoding="utf-8")
    (folder / ".env").write_text("synthetic-private-value", encoding="utf-8")
    with pytest.raises(MCPError, match="SKILL_PRIVATE_FILE_DENIED"):
        skills.preview(folder)
    (folder / ".env").unlink()
    item = skills.import_folder(folder, skills.preview(folder)["content_hash"])
    for path in ("../SKILL.md", "C:/SKILL.md", "a\\b", "SKILL.md:stream"):
        with pytest.raises(MCPError):
            skills.read_file(item["id"], path)
    target = skills.root / item["installation_path"] / "SKILL.md"
    target.write_text(VALID + "tampered", encoding="utf-8")
    with pytest.raises(MCPError, match="SKILL_CONTENT_CHANGED"):
        skills.enable(item["id"], True)


def test_self_authored_edit_and_export_content(tmp_path):
    skills = AgentSkillManager(host(tmp_path).store, tmp_path / "installed")
    item = skills.create("project-review", VALID)
    assert item["source"] == "self-authored"
    assert skills.read_file(item["id"], "SKILL.md") == VALID
    edited = skills.create("project-review", VALID + "Edited", skill_id=item["id"])
    assert edited["content_hash"] != item["content_hash"]


def test_import_symlink_rejected_if_windows_supports_links(tmp_path):
    folder = tmp_path / "project-review"
    folder.mkdir()
    (folder / "SKILL.md").write_text(VALID, encoding="utf-8")
    try:
        (folder / "escape").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("Windows account cannot create symlinks")
    with pytest.raises(MCPError, match="SKILL_PATH_DENIED"):
        AgentSkillManager(host(tmp_path).store, tmp_path / "installed").preview(folder)
