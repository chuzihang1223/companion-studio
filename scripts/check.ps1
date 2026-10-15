param([switch]$SkipHistoryAudit)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Install requirements-dev.txt in .venv first' }
$cache = Join-Path $project '.cache\checks'
New-Item -ItemType Directory -Force -Path $cache | Out-Null
$env:PYTHONPYCACHEPREFIX = Join-Path $cache 'pycache'
$env:TEMP = $cache; $env:TMP = $cache
$env:COMPANION_CLEAN_DISTRIBUTION = '1'
$env:OPENAI_API_KEY = ''; $env:FISH_API_KEY = ''; $env:FISH_REFERENCE_ID = ''
$env:OPENAI_BASE_URL = 'http://127.0.0.1:9/v1'; $env:OPENAI_MODEL = ''
Push-Location $project
try {
  & $python -m ruff check backend scripts tests
  if ($LASTEXITCODE -ne 0) { throw 'Python lint failed' }
  & $python -m ruff format --check backend scripts tests
  if ($LASTEXITCODE -ne 0) { throw 'Python format failed' }
  & $python -m compileall -q backend scripts tests
  if ($LASTEXITCODE -ne 0) { throw 'Python syntax failed' }
  foreach ($check in @('typecheck','lint','format:check','build','test:electron')) {
    & npm.cmd run $check --prefix desktop
    if ($LASTEXITCODE -ne 0) { throw ('Desktop check failed: ' + $check) }
  }
  & $python -m pytest -q --basetemp (Join-Path $cache 'pytest-temp') --junitxml (Join-Path $cache 'pytest.xml')
  if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed' }
  & $python scripts/export-public.py --report (Join-Path $cache 'source-audit.json')
  if ($LASTEXITCODE -ne 0) { throw 'Public source audit failed' }
  if (-not $SkipHistoryAudit) {
    & $python scripts/audit-release.py --source $project --report (Join-Path $cache 'history-audit.json')
    if ($LASTEXITCODE -ne 0) { throw 'Git history audit failed' }
  }
} finally { Pop-Location }
'Automated checks passed; interactive Windows acceptance is a separate gate.'
