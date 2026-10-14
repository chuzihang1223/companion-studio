param(
  [string]$BuildId,
  [string]$OutputRoot,
  [switch]$SkipTests
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$desktop = Join-Path $project 'desktop'
$version = (Get-Content -LiteralPath (Join-Path $desktop 'package.json') -Raw | ConvertFrom-Json).version
if (-not $BuildId) { $BuildId = 'studio-' + $version + '-' + (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss') }
if ($BuildId -notmatch '^studio-[A-Za-z0-9.-]+$') { throw 'Invalid build ID' }
if (-not $OutputRoot) { $OutputRoot = Join-Path $project 'build' }
$root = [IO.Path]::GetFullPath($OutputRoot).TrimEnd('\')
$build = [IO.Path]::GetFullPath((Join-Path $root $BuildId))
if (-not $build.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $build)) { throw 'A new isolated build directory is required' }
$python = Join-Path $project '.venv\Scripts\python.exe'
$builder = if ($env:COMPANION_ELECTRON_BUILDER) { $env:COMPANION_ELECTRON_BUILDER } else { Join-Path $desktop 'node_modules\.bin\electron-builder.cmd' }
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $builder)) { throw 'Install Python and npm build dependencies first' }
New-Item -ItemType Directory -Force -Path $build | Out-Null
$cache = Join-Path $root 'cache'
$env:TEMP = Join-Path $cache 'temp'; $env:TMP = $env:TEMP
$env:npm_config_cache = Join-Path $cache 'npm'
$env:ELECTRON_CACHE = Join-Path $cache 'electron'
$env:electron_config_cache = $env:ELECTRON_CACHE
$env:ELECTRON_BUILDER_CACHE = Join-Path $cache 'electron-builder'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $cache 'pyinstaller'
$env:PYTHONPYCACHEPREFIX = Join-Path $cache 'pycache'
foreach ($directory in @($env:TEMP,$env:npm_config_cache,$env:ELECTRON_CACHE,$env:ELECTRON_BUILDER_CACHE,$env:PYINSTALLER_CONFIG_DIR,$env:PYTHONPYCACHEPREFIX)) { New-Item -ItemType Directory -Force -Path $directory | Out-Null }
$env:COMPANION_DATA_DIR = Join-Path $build 'test-data'
$env:PYTHONPATH = Join-Path $project 'backend'
$env:COMPANION_CLEAN_DISTRIBUTION = '1'; $env:COMPANION_DEFAULT_CHARACTER = 'assistant'
$env:OPENAI_API_KEY = ''; $env:FISH_API_KEY = ''; $env:FISH_REFERENCE_ID = ''
$env:OPENAI_BASE_URL = 'http://127.0.0.1:9/v1'; $env:OPENAI_MODEL = ''
$env:VOICE_PREFETCH_ENABLED = 'false'
$env:VOICE_MODEL_DIR = Join-Path $build 'no-model'
$env:VOICE_INDEX_MODEL_DIR = $env:VOICE_MODEL_DIR
$env:VOICE_REFERENCES_DIR = Join-Path $build 'no-references'
Push-Location $project
try {
  if (-not $SkipTests) {
    & $python -m ruff check backend scripts tests
    if ($LASTEXITCODE -ne 0) { throw 'Python lint failed' }
    & $python -m ruff format --check backend scripts tests
    if ($LASTEXITCODE -ne 0) { throw 'Python format verification failed' }
    foreach ($check in @('typecheck','lint','format:check','build','test:electron')) {
      & npm.cmd run $check --prefix desktop
      if ($LASTEXITCODE -ne 0) { throw ('Desktop verification failed: ' + $check) }
    }
    & $python -B -m pytest backend/tests tests -q --basetemp (Join-Path $build 'pytest-temp')
    if ($LASTEXITCODE -ne 0) { throw 'Backend regression failed' }
  }
  # 许可证清单在本次构建目录生成，避免构建修改受版本控制的源码。
  $licenseStage = Join-Path $build 'license-stage'
  New-Item -ItemType Directory -Force -Path $licenseStage,(Join-Path $licenseStage 'backend'),(Join-Path $licenseStage 'desktop') | Out-Null
  Copy-Item -LiteralPath (Join-Path $project 'licenses') -Destination $licenseStage -Recurse
  Copy-Item -LiteralPath (Join-Path $project 'backend\requirements.txt') -Destination (Join-Path $licenseStage 'backend')
  Copy-Item -LiteralPath (Join-Path $desktop 'package.json') -Destination (Join-Path $licenseStage 'desktop')
  & $python -B (Join-Path $PSScriptRoot 'collect-third-party.py') --root $licenseStage --node-modules (Join-Path $desktop 'node_modules')
  if ($LASTEXITCODE -ne 0) { throw 'License collection failed' }
} finally { Pop-Location }
$utf8 = New-Object Text.UTF8Encoding($false)
$sourceHashes = [ordered]@{}
foreach ($folder in @('backend\app','desktop\frontend\src','desktop\electron','characters')) {
  foreach ($file in (Get-ChildItem -LiteralPath (Join-Path $project $folder) -Recurse -File | Sort-Object FullName | Where-Object { $_.Name -ne 'build-info.json' -and $_.Extension -in @('.py','.json','.cjs','.ps1','.cs','.ts','.tsx','.css','.svg') })) {
    $sourceHashes[$file.FullName.Substring($project.Length+1).Replace('\','/')] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  }
}
foreach ($relative in @('desktop/package.json','desktop/package-lock.json','backend/requirements.lock','backend/requirements-dev.txt','scripts/build-companion-studio.ps1','desktop/assets/prepare-runtime-access.ps1','desktop/assets/installer.nsh')) {
  $sourceHashes[$relative] = (Get-FileHash -LiteralPath (Join-Path $project $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
}
$sourceHashes['desktop/assets/companion-studio.ico'] = (Get-FileHash -LiteralPath (Join-Path $desktop 'assets\companion-studio.ico') -Algorithm SHA256).Hash.ToLowerInvariant()
$sha = [Security.Cryptography.SHA256]::Create()
try { $fingerprint = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes(($sourceHashes | ConvertTo-Json -Compress))))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
$revision = if (Test-Path -LiteralPath (Join-Path $project '.git')) { (& git -C $project rev-parse HEAD).Trim() } else { 'source-export' }
$info = [ordered]@{source_revision=$revision;version=$version;product_name='Companion Studio';build_label='Companion Studio Public Release';build_id=$BuildId;built_at=(Get-Date).ToUniversalTime().ToString('o');channel='release';app_id='com.companionstudio.desktop';clean_distribution=$true;source_fingerprint=$fingerprint}
# 只覆盖暂存包中的构建信息；开发源码 build-info.json 保持稳定。
$appStage = Join-Path $build 'desktop-app'
New-Item -ItemType Directory -Force -Path $appStage,(Join-Path $appStage 'node_modules') | Out-Null
foreach ($folder in @('electron','assets')) {
  Copy-Item -LiteralPath (Join-Path $desktop $folder) -Destination $appStage -Recurse
}
foreach ($dependency in @('react','react-dom','scheduler')) {
  Copy-Item -LiteralPath (Join-Path $desktop ('node_modules\'+$dependency)) -Destination (Join-Path $appStage 'node_modules') -Recurse
}
$appPackage = Get-Content -LiteralPath (Join-Path $desktop 'package.json') -Raw | ConvertFrom-Json
$appPackage.PSObject.Properties.Remove('devDependencies')
$appPackage.PSObject.Properties.Remove('scripts')
[IO.File]::WriteAllText((Join-Path $appStage 'package.json'),($appPackage|ConvertTo-Json -Depth 20),$utf8)
[IO.File]::WriteAllText((Join-Path $appStage 'electron\build-info.json'),($info|ConvertTo-Json),$utf8)
$frontend = Join-Path $build 'frontend'
Push-Location $desktop
try {
  & node node_modules/typescript/bin/tsc --noEmit -p frontend/tsconfig.json
  if ($LASTEXITCODE -ne 0) { throw 'TypeScript verification failed' }
  & node node_modules/vite/bin/vite.js build --config frontend/vite.config.ts --outDir $frontend
  if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally { Pop-Location }
$characters = Join-Path $build 'characters'
New-Item -ItemType Directory -Path $characters | Out-Null
Copy-Item -LiteralPath (Join-Path $project 'characters\assistant.json') -Destination $characters
$backend = Join-Path $build 'backend'
$arguments = @('-m','PyInstaller','--noconfirm','--clean','--onedir','--noconsole','--hidden-import','_cffi_backend','--name','companion-backend',
  '--paths',(Join-Path $project 'backend'),'--add-data',($characters+';characters'),
  '--add-data',($frontend+';desktop/frontend/dist'),
  '--add-data',((Join-Path $project 'backend\app\services\character\app_identity.json')+';app/services/character'),
  '--add-data',((Join-Path $project 'backend\app\services\voice\voice_presets.json')+';app/services/voice'),
  '--exclude-module','_pytest','--exclude-module','PIL._imagingtk','--exclude-module','tkinter','--exclude-module','_tkinter','--exclude-module','pytest','--exclude-module','pip','--exclude-module','docx','--exclude-module','lxml','--exclude-module','soundfile','--exclude-module','_soundfile_data',
  '--distpath',$backend,'--workpath',(Join-Path $build 'pyinstaller'),'--specpath',$build,(Join-Path $project 'backend\run.py'))
# Restrict the native-library search to the official Python installation and Windows.
# Development tools can otherwise inject unrelated DLLs through PATH.
$savedPath = $env:PATH
$pythonBase = & $python -B -c "import sys; print(sys.base_prefix)"
$env:PATH = @($pythonBase,(Join-Path $pythonBase 'DLLs'),(Join-Path $env:SystemRoot 'System32'),$env:SystemRoot) -join ';'
try { & $python @arguments; $freezeExit = $LASTEXITCODE } finally { $env:PATH = $savedPath }
if ($freezeExit -ne 0) { throw 'Backend packaging failed' }
$env:COMPANION_APP_NAME = 'Companion Studio'
$env:COMPANION_DATA_DIR = Join-Path $build 'health-data'
$env:COMPANION_PARENT_PID = [string]$PID
$probe = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,0)
$probe.Start(); $port = $probe.LocalEndpoint.Port; $probe.Stop()
$env:BACKEND_PORT = [string]$port
$timer = [Diagnostics.Stopwatch]::StartNew()
$process = Start-Process -FilePath (Join-Path $backend 'companion-backend\companion-backend.exe') -WindowStyle Hidden -PassThru
try {
  $healthy = $false
  for ($i=0;$i -lt 120;$i++) {
    $process.Refresh(); if ($process.HasExited) { throw 'Frozen backend exited before health' }
    try { $health = Invoke-RestMethod -Uri ('http://127.0.0.1:'+$port+'/health') -TimeoutSec 1; if ($health.status -eq 'ok') { $healthy=$true; $timer.Stop(); break } } catch {}
    Start-Sleep -Milliseconds 250
  }
  if (-not $healthy) { throw 'Frozen backend health timed out' }
  $library = Invoke-RestMethod -Uri ('http://127.0.0.1:'+$port+'/api/characters')
  if ($library.active_id -ne 'assistant' -or @($library.characters).Count -ne 1 -or $library.characters[0].avatar_url -or $library.characters[0].profile.identity) { throw 'Unexpected bundled persona or avatar' }
} finally { $process.Refresh(); if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit(10000) | Out-Null } }
$healthReport = @{pass=$healthy;startup_seconds=[math]::Round($timer.Elapsed.TotalSeconds,3);only_generic_assistant=$true;no_model_key_required=$true;external_requests=0}
$package = Get-Content -LiteralPath (Join-Path $desktop 'package.json') -Raw | ConvertFrom-Json
$config = $package.build
$config | Add-Member -NotePropertyName electronVersion -NotePropertyValue $package.devDependencies.electron -Force
$config.directories.output = Join-Path $build 'delivery'
$config.directories.buildResources = Join-Path $desktop 'assets'
$config.extraResources[0].from = Join-Path $backend 'companion-backend'
$config.extraResources[1].from = Join-Path $project 'backend\app'
$config.extraResources[1].filter = @('**/*.py','**/*.json','!**/__pycache__/**')
$config.extraResources[2].from = Join-Path $licenseStage 'licenses'
$config.extraResources[3].from = Join-Path $licenseStage 'THIRD_PARTY_NOTICES.md'
$config.extraResources[4].from = Join-Path $project 'LICENSE'
$config.nsis.include = Join-Path $desktop 'assets\installer.nsh'
$config.nsis | Add-Member -NotePropertyName runAfterFinish -NotePropertyValue $false -Force
$config.nsis | Add-Member -NotePropertyName deleteAppDataOnUninstall -NotePropertyValue $false -Force
$config.nsis | Add-Member -NotePropertyName perMachine -NotePropertyValue $false -Force
$config.nsis | Add-Member -NotePropertyName shortcutName -NotePropertyValue 'Companion Studio' -Force
$config.nsis | Add-Member -NotePropertyName license -NotePropertyValue (Join-Path $project 'LICENSE') -Force
$configFile = Join-Path $build 'electron-builder.json'
[IO.File]::WriteAllText($configFile,($config|ConvertTo-Json -Depth 20),$utf8)
Push-Location $desktop
try {
  & $builder --projectDir $appStage --win dir nsis ('--config='+$configFile) ('--config.electronDist='+(Join-Path $desktop 'node_modules\electron\dist'))
  if ($LASTEXITCODE -ne 0) { throw 'Desktop packaging failed' }
} finally { Pop-Location }
$delivery = Join-Path $build 'delivery'; $artifacts = [ordered]@{}
$setupName = 'Companion-Studio-'+$version+'-Setup.exe'
foreach ($relative in @($setupName,'win-unpacked\CompanionStudio.exe','win-unpacked\resources\app.asar','win-unpacked\resources\companion-backend\companion-backend.exe')) {
  $file = Join-Path $delivery $relative
  if (-not (Test-Path -LiteralPath $file)) { throw ('Artifact missing: '+$relative) }
  $artifacts[$relative] = @{bytes=(Get-Item -LiteralPath $file).Length;sha256=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()}
}
$manifest = @{checks_skipped=[bool]$SkipTests;build=$info;source_hashes=$sourceHashes;artifacts=$artifacts;backend_health=$healthReport;profile_imported=$false;external_model_requests=0}
[IO.File]::WriteAllText((Join-Path $delivery 'build-manifest.json'),($manifest|ConvertTo-Json -Depth 8),$utf8)
[IO.File]::WriteAllText((Join-Path $root 'latest.json'),(@{build_root=$build;delivery=$delivery;build_id=$BuildId}|ConvertTo-Json),$utf8)
[IO.File]::WriteAllText((Join-Path $delivery 'SHA256SUMS.txt'),($artifacts[$setupName].sha256+'  '+$setupName+[Environment]::NewLine),$utf8)
& $python -B (Join-Path $PSScriptRoot 'audit-release.py') --source $project --delivery $delivery --report (Join-Path $delivery 'security-audit.json')
if ($LASTEXITCODE -ne 0) { throw 'Release security audit failed' }
'Companion Studio public production build completed.'
