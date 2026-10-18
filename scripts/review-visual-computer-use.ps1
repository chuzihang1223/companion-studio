param(
  [ValidateSet('all','check','source-gui','build','installer','final')][string]$Phase = 'all',
  [string]$EvidenceRoot,
  [string]$BuildRoot,
  [string]$BuildId = 'studio-0.2.0-visual.1-review',
  [string]$ProtectedProfile
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Install locked development requirements first' }
if (-not $EvidenceRoot) { $EvidenceRoot = Join-Path $project '.cache\visual-qa' }
if (-not $BuildRoot) { $BuildRoot = Join-Path $project 'build\visual-qa' }
$arguments = @((Join-Path $PSScriptRoot 'review-visual-runner.py'),'--phase',$Phase,'--evidence',$EvidenceRoot,'--build-root',$BuildRoot,'--build-id',$BuildId)
if ($ProtectedProfile) { $arguments += @('--protected-profile',$ProtectedProfile) }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Visual QA gate failed; inspect retained local evidence' }
