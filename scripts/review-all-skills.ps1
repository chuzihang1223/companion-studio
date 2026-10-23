param(
  [ValidateSet('all','check','source-gui','build','installer','final')][string]$Phase = 'all',
  [string]$EvidenceRoot,
  [string]$BuildRoot,
  [string]$BuildId = 'studio-0.2.0-router.1-review',
  [Parameter(Mandatory=$true)][string]$ProtectedProfile
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Install locked development requirements first' }
if (-not $EvidenceRoot) { $EvidenceRoot = Join-Path $project '.cache\all-skills-review' }
if (-not $BuildRoot) { $BuildRoot = Join-Path $project 'build\all-skills-review' }
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'; $env:PYTHONOPTIMIZE = ''
$env:TEMP = Join-Path $project '.cache\unified-temp'; $env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
& $python (Join-Path $PSScriptRoot 'review-all-skills.py') --phase $Phase --evidence $EvidenceRoot --build-root $BuildRoot --build-id $BuildId --protected-profile $ProtectedProfile
if ($LASTEXITCODE -ne 0) { throw 'Unified QA has a failed command; inspect retained evidence' }
