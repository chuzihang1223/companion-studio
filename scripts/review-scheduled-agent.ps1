param(
  [ValidateSet('all','check','source-gui','build','installer','final')][string]$Phase = 'all',
  [string]$EvidenceRoot,
  [string]$BuildRoot,
  [string]$BuildId = 'studio-0.2.0-scheduler.1-review',
  [Parameter(Mandatory=$true)][string]$ProtectedProfile
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Install locked development requirements first' }
if (-not $EvidenceRoot) { $EvidenceRoot = Join-Path $project '.cache\scheduler-review' }
if (-not $BuildRoot) { $BuildRoot = Join-Path $project 'build\scheduler-review' }
$arguments = @((Join-Path $PSScriptRoot 'review-scheduler-runner.py'),'--phase',$Phase,'--evidence',$EvidenceRoot,'--build-root',$BuildRoot,'--build-id',$BuildId,'--protected-profile',$ProtectedProfile)
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Scheduled Agent QA failed; inspect retained evidence' }
