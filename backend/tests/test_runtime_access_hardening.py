"""Windows runtime ACL compatibility without profile access or inheritance."""

from pathlib import Path
import subprocess


def test_runtime_sandbox_groups_are_read_only_and_profile_is_untouched(tmp_path):
    root = tmp_path / "owned-program"
    root.mkdir()
    (root / "CompanionStudio.exe").write_bytes(b"public test fixture, never executed")
    (root / "resources").mkdir()
    (root / "resources/app.asar").write_bytes(b"public asar fixture")
    profile = root / "UserData"
    profile.mkdir()
    (profile / "sentinel.txt").write_bytes(b"isolated profile sentinel")
    helper = Path(__file__).resolve().parents[2] / "desktop/assets/prepare-runtime-access.ps1"
    script = tmp_path / "verify.ps1"
    script.write_text(
        """param([string]$Root,[string]$Helper)
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.Security')
$profile=Join-Path $Root 'UserData'
$before=(Get-Acl -LiteralPath $profile).Sddl
function RuleSet($path){
  @((Get-Acl -LiteralPath $path).GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | ForEach-Object {
    $_.IdentityReference.Value+':'+[long]$_.FileSystemRights+':'+$_.AccessControlType+':'+$_.InheritanceFlags+':'+$_.PropagationFlags+':'+$_.IsInherited
  } | Sort-Object) -join '|'
}
function Descriptor($path){
  $acl=Get-Acl -LiteralPath $path
  $acl.Owner+'|'+$acl.Group+'|'+$acl.AreAccessRulesProtected+'|'+(RuleSet $path)
}
$rulesBefore=Descriptor $profile
$fileBefore=Descriptor (Join-Path $profile 'sentinel.txt')
& $Helper -ProgramDirectory $Root
& $Helper -ProgramDirectory $Root -CheckOnly
# Windows can normalize only SE_DACL_AUTO_INHERITED on existing children when
# setting a parent's DACL. Compare every effective ACE and owner/protection;
# that metadata flag alone must not be mistaken for a new access grant.
@{before_sddl=$before;after_sddl=(Get-Acl -LiteralPath $profile).Sddl;before_descriptor=$rulesBefore;after_descriptor=(Descriptor $profile)} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Root 'acl-observation.json') -Encoding UTF8
if((Descriptor $profile) -cne $rulesBefore){throw 'Profile access changed'}
if((Descriptor (Join-Path $profile 'sentinel.txt')) -cne $fileBefore){throw 'Profile file access changed'}
foreach($path in @($Root,(Join-Path $Root 'CompanionStudio.exe'),(Join-Path $Root 'resources\\app.asar'))){
  $acl=Get-Acl -LiteralPath $path
  foreach($sid in @('S-1-15-2-1','S-1-15-2-2')){
    $rules=@($acl.GetAccessRules($true,$false,[Security.Principal.SecurityIdentifier]) | Where-Object {$_.IdentityReference.Value -eq $sid})
    if(-not $rules.Count){throw 'Explicit sandbox read entry missing'}
    foreach($rule in $rules){
      if($rule.InheritanceFlags -ne [Security.AccessControl.InheritanceFlags]::None){throw 'Runtime access inherited into profiles'}
      if($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write){throw 'Sandbox acquired write access'}
    }
  }
}
""",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Root",
            str(root),
            "-Helper",
            str(helper),
        ],
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout.decode(errors="replace") + result.stderr.decode(
        errors="replace"
    )
    denied = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            "-ProgramDirectory",
            str(root),
            "-DevelopmentRuntime",
        ],
        capture_output=True,
        timeout=20,
    )
    assert denied.returncode != 0
    assert (profile / "sentinel.txt").read_bytes() == b"isolated profile sentinel"
