"""Public exports retain required upstream evidence and omit generated builds."""

from pathlib import Path
import hashlib
import json
import runpy
from email.message import Message
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
POLICY = runpy.run_path(str(ROOT / "scripts/export-public.py"))
NOTICES = runpy.run_path(str(ROOT / "scripts/collect-third-party.py"))


def test_missing_wheel_project_urls_keep_the_exact_distribution_origin():
    info = Message()
    info["Name"] = "synthetic-package"
    dist = SimpleNamespace(metadata=info, version="1.2.3")
    assert NOTICES["upstream"](dist) == ["https://pypi.org/project/synthetic-package/1.2.3/"]
    info["Project-URL"] = "Source, https://example.invalid/owned-source"
    assert NOTICES["upstream"](dist) == ["https://example.invalid/owned-source"]


def test_multi_package_notices_keep_case_insensitive_targets_distinct():
    paths = ["adodbapi/license.txt", "pythonwin/License.txt", "win32com/License.txt"]
    result = NOTICES["notice_relative_paths"](paths)
    assert set(result.values()) == set(paths)
    assert len({p.casefold() for p in result.values()}) == len(paths)


def test_every_inventory_notice_matches_the_distributed_bytes():
    inventory = json.loads((ROOT / "licenses/inventory.json").read_text(encoding="utf-8"))
    for component in inventory["components"]:
        assert component["upstream"]
        for notice in component["files"]:
            assert (
                hashlib.sha256((ROOT / notice["path"]).read_bytes()).hexdigest() == notice["sha256"]
            ), notice["path"]


def test_public_export_contains_every_manifest_source_with_original_bytes():
    selected = {file.relative_to(ROOT).as_posix(): file for file in POLICY["collect"](ROOT)}
    manifest = json.loads((ROOT / "licenses/source-manifest.json").read_text(encoding="utf-8"))
    for evidence in manifest["files"]:
        assert evidence["path"] in selected, evidence["path"]
        assert (
            hashlib.sha256(selected[evidence["path"]].read_bytes()).hexdigest()
            == evidence["sha256"]
        )


def test_upstream_build_sources_survive_while_generated_builds_are_excluded(tmp_path):
    for name in POLICY["ROOT_FILES"]:
        (tmp_path / name).write_text("fixture", encoding="utf-8")
    for name in POLICY["TREES"]:
        (tmp_path / name).mkdir(parents=True)
    paths = [
        "licenses/source/upstream/build/args/release.gn",
        "desktop/build/generated.txt",
        "backend/cache/runtime.txt",
        "desktop/node_modules/dependency.txt",
    ]
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture", encoding="utf-8")
    selected = {file.relative_to(tmp_path).as_posix() for file in POLICY["collect"](tmp_path)}
    assert paths[0] in selected
    assert not set(paths[1:]) & selected
