"""Exercise the real installer version guard without running an installer."""

import base64
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell version resource contract")
def test_installer_accepts_review_semver_and_rejects_lookalikes():
    script = r"""
$ErrorActionPreference='Stop'
. $env:TRACE_VERSION_HELPER
$cases=@(
  @('0.1.2','0.1.2','0.1.2',$true),
  @('0.1.2','0.1.2','0.1.2.0',$true),
  @('0.2.0-trace.1','0.2.0-trace.1','0.2.0.0',$true),
  @('0.2.0-trace.1','0.2.0-trace.2','0.2.0.0',$false),
  @('0.2.0-trace.1','0.2.0-trace.1','0.2.01.0',$false),
  @('0.1.2','0.1.20','0.1.20',$false),
  @('0.2.0-trace.1','0.2.0-trace.1','0.1.2',$false)
)
foreach($case in $cases){
  $passed=$true
  try { Assert-InstalledVersion ([pscustomobject]@{FileVersion=$case[1];ProductVersion=$case[2]}) $case[0] }
  catch { $passed=$false }
  if($passed -ne $case[3]){throw 'Version guard fixture failed'}
}
'7 version resource cases passed'
"""
    environment = {
        **os.environ,
        "TRACE_VERSION_HELPER": str(
            Path(__file__).resolve().parents[1] / "scripts" / "installer-version.ps1"
        ),
    }
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            base64.b64encode(script.encode("utf-16-le")).decode("ascii"),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    assert "7 version resource cases passed" in result.stdout
