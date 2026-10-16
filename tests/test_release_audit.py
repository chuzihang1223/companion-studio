"""合成公开项目/历史回归，不使用账号凭据、个人资料或真实模型请求。"""

import base64
import hashlib
import importlib.util
import json
import marshal
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_audit", ROOT / "scripts/audit-release.py")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
POLICY = AUDIT.load_export_policy(ROOT)


def credential():
    # 分开构造，防止把合成的完整假密钥写入公开源码或历史。
    return "sk-" + "Z2a7B4m8C9d0E1f3" * 2


def private_path():
    return "/".join(["C:", "Users", "synthetic-owner", "secret.txt"])


def write_manifest(root, content=b"test license"):
    target = root / "licenses/component/LICENSE"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    manifest = {
        "files": [
            {
                "path": "licenses/component/LICENSE",
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ]
    }
    (root / "licenses/source-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def git(root, *args):
    # 只操作 tmp_path 中由本测试新建的仓库，不触碰维护仓库。
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)
    return result.stdout


def test_scan_redacts_values_and_only_allows_explicit_test_placeholders():
    audit = AUDIT.Audit(POLICY)
    fake = "sk-test-" + "0" * 32
    audit.text(fake, "history", "backend/tests/example.py")
    audit.text('api_key="isolated-test-key"', "history", "backend/tests/example.py")
    audit.text('password="not-a-real-password"', "history", "backend/tests/example.py")
    assert not audit.findings
    placeholder = AUDIT.Audit(POLICY)
    placeholder.text('password="not-a-real-password"', "source", "backend/app/example.py")
    placeholder.text('password="' + "M8uN4aV9" * 4 + '"', "history", "backend/tests/example.py")
    assert len(placeholder.findings) == 2
    assert all(row["kind"] == "credential-assignment" for row in placeholder.findings)
    audit.text(fake, "history", "backend/app/example.py")
    audit.text(credential(), "history", "backend/tests/not_a_placeholder.py")
    audit.text(private_path(), "history", "backend/app/private.py")
    report = json.dumps(audit.findings)
    assert credential() not in report and fake not in report
    assert "synthetic-owner" not in report
    assert {row["kind"] for row in audit.findings} == {
        "credential-pattern",
        "private-path-or-identity",
    }


def test_unprefixed_assignment_and_sensitive_report_path_are_redacted():
    audit = AUDIT.Audit(POLICY)
    value = "M8uN4aV9" * 4
    audit.text('api_key="' + value + '"', "source", private_path())
    assert audit.findings[0]["kind"] == "credential-assignment"
    report = json.dumps(audit.findings)
    assert value not in report and "synthetic-owner" not in report
    assert audit.findings[0]["file"].startswith("redacted-path-")


def test_all_ref_history_detects_removed_secret_and_does_not_flag_binary_icons(tmp_path):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Synthetic Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    (tmp_path / "README.md").write_text("safe main", encoding="utf-8")
    (tmp_path / "icon.ico").write_bytes(b"\0\0\1\0\xff\xfe")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "safe base")
    git(tmp_path, "switch", "-c", "retired-feature")
    secret = tmp_path / "removed.txt"
    secret.write_text(credential() + "\n" + private_path(), encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "synthetic old blob")
    secret.unlink()
    git(tmp_path, "add", "-u")
    git(tmp_path, "commit", "-m", "remove synthetic blob")
    git(tmp_path, "switch", "main")
    audit = AUDIT.Audit(POLICY)
    AUDIT.history_audit(tmp_path, audit)
    assert audit.stats["history_commits"] == 3
    assert audit.stats["history_binary_blobs"] == 1
    assert any(
        row["file"] == "removed.txt" and row["kind"] == "credential-pattern"
        for row in audit.findings
    )
    assert any(row["kind"] == "private-path-or-identity" for row in audit.findings)
    assert not any(row["file"] == "icon.ico" for row in audit.findings)
    assert credential() not in json.dumps(audit.findings)
    assert "synthetic-owner" not in json.dumps(audit.findings)


def test_license_manifest_rejects_missing_tampered_and_unlisted_files(tmp_path):
    write_manifest(tmp_path)
    audit = AUDIT.Audit(POLICY)
    AUDIT.license_integrity(tmp_path, audit, "source")
    assert not audit.findings
    (tmp_path / "licenses/component/LICENSE").write_bytes(b"tampered")
    AUDIT.license_integrity(tmp_path, audit, "source")
    assert any(row["kind"] == "third-party-source-hash-mismatch" for row in audit.findings)
    (tmp_path / "licenses/component/LICENSE").unlink()
    (tmp_path / "licenses/private.db").write_bytes(b"private")
    AUDIT.license_integrity(tmp_path, audit, "source")
    assert any(row["kind"] == "required-third-party-file-missing" for row in audit.findings)
    assert any(row["kind"] == "unlisted-third-party-file" for row in audit.findings)


def test_vendor_paths_are_not_treated_as_personal_but_keys_still_scanned():
    audit = AUDIT.Audit(POLICY)
    audit.text(private_path(), "source", "licenses/upstream/README", vendor=True)
    assert not audit.findings
    audit.text(credential(), "source", "licenses/upstream/README", vendor=True)
    assert audit.findings[0]["kind"] == "credential-pattern"


def test_asar_actual_reader_finds_secret_and_nonapproved_image(tmp_path):
    if (
        not shutil.which("node")
        or not (ROOT / "desktop/node_modules/@electron/asar/lib/asar.js").is_file()
    ):
        pytest.skip("ASAR development dependency not installed")
    package = tmp_path / "package"
    package.mkdir()
    (package / "electron/nested").mkdir(parents=True)
    (package / "electron/nested/main.cjs").write_text(
        'const key="' + credential() + '";', encoding="utf-8"
    )
    (package / "private.png").write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aE9sAAAAASUVORK5CYII="
        )
    )
    resources = tmp_path / "unpacked/resources"
    resources.mkdir(parents=True)
    script = "require(process.argv[1]).createPackage(process.argv[2],process.argv[3]).catch(()=>process.exit(1))"
    subprocess.run(
        [
            "node",
            "-e",
            script,
            str(ROOT / "desktop/node_modules/@electron/asar/lib/asar.js"),
            str(package),
            str(resources / "app.asar"),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    audit = AUDIT.Audit(POLICY)
    AUDIT.asar_audit(ROOT, resources.parent, audit, AUDIT.approved_images(ROOT, audit))
    assert audit.stats["asar_members"] == 2
    assert {row["kind"] for row in audit.findings} == {"credential-pattern", "unexpected-image"}
    assert credential() not in json.dumps(audit.findings)


def test_frozen_constants_nested_code_are_read_without_execution(monkeypatch):
    from PyInstaller.archive import readers

    code = compile(
        "marker = (" + repr(credential()) + ",)\n"
        "def nested():\n    return " + repr(private_path()) + "\n"
        "raise RuntimeError('must never execute')",
        "app/example.py",
        "exec",
    )

    class Reader:
        toc = {"PYZ.pyz": (0, 0, 0, 0, "z"), "run": (0, 0, 0, 0, "s")}

        def __init__(self, path):
            pass

        def open_embedded_archive(self, name):
            return SimpleNamespace(
                toc={"app.example": None, "vendor.example": None}, extract=lambda module: code
            )

        def extract(self, name):
            return marshal.dumps(compile("pass", "run.py", "exec"))

    monkeypatch.setattr(readers, "CArchiveReader", Reader)
    audit = AUDIT.Audit(POLICY)
    AUDIT.frozen_audit(Path("fixture.exe"), audit)
    assert audit.stats["frozen_app_modules"] == 1
    assert {row["kind"] for row in audit.findings} == {
        "credential-pattern",
        "private-path-or-identity",
    }
    assert credential() not in json.dumps(audit.findings)


def test_delivery_rejects_profile_audio_model_and_private_image(tmp_path, monkeypatch):
    write_manifest(tmp_path)
    unpacked = tmp_path / "delivery"
    shutil.copytree(tmp_path / "licenses", unpacked / "resources/licenses")
    for relative in (
        "UserData/chat.db",
        "resources/reference.wav",
        "resources/model.onnx",
        "resources/private.png",
    ):
        file = unpacked / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"\xff\x00private")
    (unpacked / "PrivateArchive").mkdir()
    official = tmp_path / "desktop/node_modules/electron/dist/icudtl.bin"
    official.parent.mkdir(parents=True)
    official.write_bytes(b"\xff\x00native snapshot")
    shutil.copyfile(official, unpacked / "icudtl.bin")
    # 本测试覆盖物理资源策略；归档读入分别由真实 ASAR 和常量回归验证。
    monkeypatch.setattr(AUDIT, "asar_audit", lambda *args: None)
    monkeypatch.setattr(AUDIT, "frozen_audit", lambda *args: None)
    audit = AUDIT.Audit(POLICY)
    AUDIT.delivery_audit(tmp_path, unpacked, audit)
    kinds = {row["kind"] for row in audit.findings}
    assert {"runtime-profile-path", "excluded-runtime-file", "unexpected-image"} <= kinds
    assert any(row["file"] == "PrivateArchive" for row in audit.findings)
    assert not any(row["file"] == "icudtl.bin" for row in audit.findings)


def test_cli_failure_nonzero_and_error_does_not_echo_input(tmp_path):
    for name in POLICY.ROOT_FILES:
        (tmp_path / name).write_text("", encoding="utf-8")
    for name in POLICY.TREES:
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    for name in getattr(POLICY, "DOC_FILES", set()):
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("", encoding="utf-8")
    write_manifest(tmp_path)
    shutil.copyfile(ROOT / "scripts/export-public.py", tmp_path / "scripts/export-public.py")
    (tmp_path / "backend/unsafe.py").write_text('api_key="' + credential() + '"', encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "scripts/audit-release.py"),
            "--source",
            str(tmp_path),
            "--no-history",
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert not report["pass"]
    assert any(row["kind"] == "credential-pattern" for row in report["findings"])
    assert credential().encode() not in result.stdout + result.stderr
