"""Windows 沙箱读取准备只改变明确运行文件，不能扩散到档案或未来文件。"""

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "desktop/assets/prepare-runtime-access.ps1"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows filesystem ACL test")


def ps_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def ps(expression: str):
    expression = (
        "Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security'); "
        "Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility'); "
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); " + expression
    )
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", expression],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=True,
    ).stdout.strip()


def sddl(path: Path) -> str:
    return ps(f"(Get-Acl -LiteralPath {ps_quote(path)}).Sddl")


def prepare(path: Path, *, check_only: bool = False):
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT),
        "-ProgramDirectory",
        str(path),
    ]
    if check_only:
        command.append("-CheckOnly")
    return subprocess.run(command, capture_output=True, text=True)


def fixture_program(tmp_path):
    program = tmp_path / "Program"
    program.mkdir()
    (program / "CompanionStudio.exe").write_bytes(b"synthetic runtime")
    (program / "UserData").mkdir()
    (program / "UserData/api-keys.json").write_text("synthetic private file")
    (program / "unrelated.txt").write_text("unrelated user content")
    return program


def test_explicit_runtime_read_does_not_change_private_files_or_inherit_to_future_files(tmp_path):
    program = fixture_program(tmp_path)
    private = [program / "UserData", program / "UserData/api-keys.json", program / "unrelated.txt"]
    before = {path: sddl(path) for path in private}
    (program / "baseline.txt").write_text("baseline")

    def inherited(path):
        return ps(
            f"@((Get-Acl -LiteralPath {ps_quote(path)}).GetAccessRules($true,$true,"
            "[System.Security.Principal.SecurityIdentifier]) | Where-Object {"
            "$_.IsInherited -and $_.IdentityReference.Value -eq 'S-1-15-2-2'}).Count"
        )

    old_inherited = inherited(program / "baseline.txt")
    result = prepare(program)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["inherited_permission_added"] is False
    assert {path: sddl(path) for path in private} == before
    permissions = ps(
        f"(Get-Acl -LiteralPath {ps_quote(program / 'CompanionStudio.exe')}).GetAccessRules("
        "$true,$false,[System.Security.Principal.SecurityIdentifier]) | Where-Object {"
        "$_.IdentityReference.Value -eq 'S-1-15-2-2'} | ForEach-Object {"
        "@{inheritance=[string]$_.InheritanceFlags;rights=[string]$_.FileSystemRights}} | ConvertTo-Json"
    )
    rule = json.loads(permissions)
    assert rule["inheritance"] == "None"
    assert "ReadAndExecute" in rule["rights"] and "Write" not in rule["rights"]
    (program / "future-private.txt").write_text("future user file")
    assert inherited(program / "future-private.txt") == old_inherited


def test_check_only_keeps_every_acl_unchanged(tmp_path):
    program = fixture_program(tmp_path)
    targets = [program, *(program.rglob("*"))]
    before = {path: sddl(path) for path in targets}
    result = prepare(program, check_only=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["checked"] is True
    assert {path: sddl(path) for path in targets} == before


def test_missing_product_is_rejected_before_permission_changes(tmp_path):
    program = tmp_path / "UserData"
    program.mkdir()
    before = sddl(program)
    result = prepare(program)
    assert result.returncode != 0
    assert sddl(program) == before
