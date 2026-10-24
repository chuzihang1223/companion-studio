param(
  [ValidateSet('all','check','source-gui','stress','build','installer','final','report')][string]$Phase='all',
  [Parameter(Mandatory=$true)][string]$EvidenceRoot,
  [Parameter(Mandatory=$true)][string]$BuildRoot,
  [string]$BuildId='studio-0.3.0-rc.2-hardening01',
  [Parameter(Mandatory=$true)][string]$ProtectedProfile
)
$ErrorActionPreference='Stop'
$project=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$env:PYTHONUTF8='1';$env:PYTHONIOENCODING='utf-8';$env:PYTHONOPTIMIZE=''
$env:TEMP=Join-Path $project '.cache\final-hardening-temp';$env:TMP=$env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$env:npm_config_cache=Join-Path $env:TEMP 'npm'
$env:PIP_CACHE_DIR=Join-Path $env:TEMP 'pip'
$env:PYTHONPYCACHEPREFIX=Join-Path $env:TEMP 'pycache'
& (Join-Path $project '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'final-release-hardening.py') --phase $Phase --evidence $EvidenceRoot --build-root $BuildRoot --build-id $BuildId --protected-profile $ProtectedProfile
if($LASTEXITCODE -ne 0){throw 'Final hardening has incomplete or failed evidence; inspect UNIFIED_TEST_RESULTS.json'}
