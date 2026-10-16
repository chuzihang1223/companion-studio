"""Public exports retain required upstream evidence and omit generated builds."""

from pathlib import Path
import hashlib
import json
import runpy

ROOT = Path(__file__).resolve().parents[1]
POLICY = runpy.run_path(str(ROOT / "scripts/export-public.py"))


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
