param(
  [Parameter(Mandatory=$true)][string]$Delivery,
  [string]$TestRoot,
  [ValidateRange(0,65535)][int]$BackendPort = 0,
  [switch]$RunUI,
  [switch]$PreserveInstalledSnapshot,
  [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
if ($PreserveInstalledSnapshot -and -not $RunUI) {
  throw 'PreserveInstalledSnapshot requires RunUI; incomplete installation checks cannot produce a retained program'
}
Set-StrictMode -Version 2.0
# 通过官方自带模块的绝对运行时位置加载，避免从 PS7/其他父进程继承的
# PSModulePath 使 Windows PowerShell 5.1 找不到 Get-FileHash 等命令。
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility') -ErrorAction Stop
if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) {
  throw 'Use 64-bit Windows PowerShell for the Windows x64 installer registry view'
}

# 仅允许公开项目自身 .cache/installer-tests 下的新目录；所有备份与测试
# 档案保留，不把测试清理扩大到正式安装目录或其他产品的注册信息。
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cacheRoot = [IO.Path]::GetFullPath((Join-Path $project '.cache')).TrimEnd('\')
$testsRoot = [IO.Path]::GetFullPath((Join-Path $cacheRoot 'installer-tests')).TrimEnd('\')
$testId = [Guid]::NewGuid().ToString('N')
if (-not $TestRoot) { $TestRoot = Join-Path $testsRoot ('installation-' + $testId) }
$testRootPath = [IO.Path]::GetFullPath($TestRoot).TrimEnd('\')

function Assert-NewTestRoot([string]$Candidate) {
  $full = [IO.Path]::GetFullPath($Candidate).TrimEnd('\')
  if (-not $full.StartsWith($testsRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'TestRoot must be a new child of this project .cache/installer-tests directory'
  }
  if ($full -eq $testsRoot -or $full -eq [IO.Path]::GetPathRoot($full).TrimEnd('\') -or
      $full -eq [Environment]::GetFolderPath('UserProfile').TrimEnd('\') -or
      (Test-Path -LiteralPath $full)) { throw 'Existing/root/user-profile test target is forbidden' }
  # GetFullPath 不能阻止 junction 指向其他档案；检查现有祖先的重解析属性。
  $ancestor = $full
  while ($ancestor) {
    if (Test-Path -LiteralPath $ancestor) {
      if ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'TestRoot must not traverse a filesystem link'
      }
    }
    $parent = [IO.Path]::GetDirectoryName($ancestor)
    if (-not $parent -or $parent -eq $ancestor) { break }
    $ancestor = $parent
  }
}

function Assert-ExactProtectionScope([string[]]$Paths, [string[]]$Expected) {
  # 括号中的每个字符串是一个数组元素；绝不把逗号组合送入 Join-Path。
  # 仅检查 Count 不够：还检查两个不同完整路径和精确允许列表。
  if ($Paths.Count -ne 2 -or @($Paths | ForEach-Object { $_.ToUpperInvariant() } | Select-Object -Unique).Count -ne 2) {
    throw 'Protection scope must contain exactly two unique paths'
  }
  foreach ($path in $Paths) {
    if ($Expected -notcontains $path) { throw 'Unexpected product protection path' }
  }
}

Assert-NewTestRoot $testRootPath
$guid = '5ebbaf46-265d-5aeb-93be-ba7b646371aa'
# 不能扩成 Software、Uninstall 父键，也不能使用产品名称通配符。
[string[]]$registryPaths = @(
  ('HKCU:\Software\' + $guid),
  ('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\' + $guid)
)
[string[]]$expectedRegistryPaths = @(
  ('HKCU:\Software\' + $guid),
  ('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\' + $guid)
)
Assert-ExactProtectionScope $registryPaths $expectedRegistryPaths
function Assert-NoMachineInstallation {
  # 同 GUID 的机器级安装不属于本脚本授权范围，两种注册表视图均只读。
  foreach ($view in @([Microsoft.Win32.RegistryView]::Registry32,[Microsoft.Win32.RegistryView]::Registry64)) {
    $hive = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine,$view)
    try {
      foreach ($path in $registryPaths) {
        $key = $hive.OpenSubKey($path.Substring('HKCU:\'.Length),$false)
        if ($key) { $key.Close(); throw 'A same-GUID machine installation exists; it is outside this test protection scope' }
      }
    } finally { $hive.Close() }
  }
}
Assert-NoMachineInstallation
$desktopDirectory = [Environment]::GetFolderPath('Desktop')
$programsDirectory = [Environment]::GetFolderPath('Programs')
if (-not $desktopDirectory -or -not $programsDirectory) { throw 'Current-user shortcut folders are unavailable' }
[string[]]$shortcutPaths = @(
  (Join-Path $desktopDirectory 'Companion Studio.lnk'),
  (Join-Path $programsDirectory 'Companion Studio.lnk')
)
Assert-ExactProtectionScope $shortcutPaths @(
  (Join-Path $desktopDirectory 'Companion Studio.lnk'),
  (Join-Path $programsDirectory 'Companion Studio.lnk')
)
$deliveryRoot = (Resolve-Path -LiteralPath $Delivery).Path
$manifest = Get-Content -LiteralPath (Join-Path $deliveryRoot 'build-manifest.json') -Raw | ConvertFrom-Json
if ($manifest.build.app_id -ne 'com.companionstudio.desktop' -or
    $manifest.build.product_name -ne 'Companion Studio' -or $manifest.build.clean_distribution -ne $true) {
  throw 'Delivery is not the clean Companion Studio product'
}
$setupName = 'Companion-Studio-' + $manifest.build.version + '-Setup.exe'
$setup = Join-Path $deliveryRoot $setupName
if ((Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.artifacts.$setupName.sha256) {
  throw 'Installer does not match the build manifest'
}
function Test-InstallerStillRunning {
  $name = [IO.Path]::GetFileNameWithoutExtension($setup)
  foreach ($candidate in @(Get-Process -Name $name -ErrorAction SilentlyContinue)) {
    if (-not $candidate.Path) { throw 'Cannot verify the path of a running installer' }
    if ([IO.Path]::GetFullPath($candidate.Path).Equals($setup,[StringComparison]::OrdinalIgnoreCase)) {
      return $true
    }
  }
  return $false
}
if (Test-InstallerStillRunning) { throw 'This installer is already running; do not overlap installation attempts' }
$testProductHome = Join-Path $testRootPath 'Product'
$testFolder = Join-Path $testProductHome 'Release\installed'
$testExe = Join-Path $testFolder 'CompanionStudio.exe'
function Assert-NoOtherStudioProcess {
  # NSIS 在某些 PowerShell 策略下可能退回按 /IM 检查/结束进程。若正式
  # 版同名进程仍在运行，直接拒绝测试；不让安装器替本脚本停止它。
  foreach ($candidate in @(Get-Process -Name CompanionStudio -ErrorAction SilentlyContinue)) {
    if (-not $candidate.Path -or -not [IO.Path]::GetFullPath($candidate.Path).Equals($testExe,[StringComparison]::OrdinalIgnoreCase)) {
      throw 'Another or unverified CompanionStudio process is running; close it yourself before this test'
    }
  }
}
Assert-NoOtherStudioProcess
$uninstaller = Join-Path $testFolder 'Uninstall CompanionStudio.exe'
$smokeHome = Join-Path $testRootPath 'UIProfile'
$backupRoot = Join-Path $testRootPath 'Backup'
$reportPath = Join-Path $testRootPath 'installer-test-report.json'
$snapshotRoot = Join-Path $testRootPath 'InstalledSnapshot'
$snapshotFolder = Join-Path $snapshotRoot 'win-unpacked'
$regExe = (Get-Command reg.exe -ErrorAction Stop).Source
if ($RunUI) { $nodeExe = (Get-Command node -ErrorAction Stop).Source }
foreach ($path in $registryPaths) {
  if (Test-Path -LiteralPath ($path + '-installer-test-' + $testId)) { throw 'Temporary registry backup already exists' }
}
foreach ($path in $shortcutPaths) {
  if (Test-Path -LiteralPath $path) {
    if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
      throw 'Shortcut protection must not follow filesystem links'
    }
  }
}
if ($DryRun) {
  # 只验证输入和保护范围；不创建目录、不导出/修改注册表、不启动程序。
  @{dry_run=$true;validated=$true;registry_paths=$registryPaths;shortcut_paths=$shortcutPaths;
    registry_count=$registryPaths.Count;test_root=$testRootPath;test_product_home=$testProductHome;
    installer=$setup;run_ui=[bool]$RunUI;preserve_installed_snapshot=[bool]$PreserveInstalledSnapshot;
    snapshot_folder=$snapshotFolder} | ConvertTo-Json -Depth 4
  exit 0
}

$utf8 = New-Object Text.UTF8Encoding($false)
$report = [ordered]@{success=$false;test_id=$testId;product='Companion Studio';version=$manifest.build.version;
  build_id=$manifest.build.build_id;test_root=$testRootPath;product_home=$testProductHome;installed=$testFolder;
  ui_requested=[bool]$RunUI;installation_verified=$false;shortcuts_verified=$false;ui_verified=$false;
  original_registry_restored=$false;original_shortcuts_restored=$false;uninstall_verified=$false;
  recovery_deferred=$false;
  installed_snapshot=[ordered]@{requested=[bool]$PreserveInstalledSnapshot;verified=$false;
    program=$snapshotFolder;files=0;excluded_installer_files=@();core_hashes=@{};manifest_sha256=$null;
    inventory_sha256=$null};
  external_model_requests=0;external_voice_requests=0;error=$null;cleanup_errors=@();registry=@();shortcuts=@()}
$registryRows = @()
$shortcutRows = @()
$installationStarted = $false
$recoveryAllowed = $true
$uninstallProcess = $null
$failure = $null
$savedEnv = @{}
foreach ($name in @('TEMP','TMP','COMPANION_TEST_EXE','COMPANION_TEST_HOME','COMPANION_TEST_START_NOTEPAD')) {
  $savedEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

function Save-TestReport {
  $report.registry = $registryRows
  $report.shortcuts = $shortcutRows
  [IO.File]::WriteAllText($reportPath, ($report | ConvertTo-Json -Depth 8), $utf8)
}

function Save-InstalledSnapshot {
  # 快照只来自本次已通过验收的 Program，不复制 Product 的 UserData、
  # Cache 或独立 UIProfile；目标固定在新 TestRoot 内，不能由调用方改址。
  if (-not $RunUI -or -not $report.ui_verified -or -not $report.installation_verified) {
    throw 'Installed program snapshot requires completed installation and UI verification'
  }
  if (Test-Path -LiteralPath $snapshotRoot) { throw 'Installed snapshot destination must be new' }
  function Assert-SnapshotPath([string]$Path, [string]$Boundary) {
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (-not $full.Equals($Boundary,[StringComparison]::OrdinalIgnoreCase) -and
        -not $full.StartsWith($Boundary+'\',[StringComparison]::OrdinalIgnoreCase)) {
      throw 'Installed snapshot path escaped its isolated boundary'
    }
    # 不只检查最终文件：复制前也重新检查其目录链，防止已枚举的父目录
    # 被换成 junction 后读到其他档案。边界本身必须仍是普通目录。
    $current = $full
    while ($current) {
      if (Test-Path -LiteralPath $current) {
        if ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
          throw 'Installed snapshot must not traverse a filesystem link'
        }
      }
      if ($current.Equals($testRootPath,[StringComparison]::OrdinalIgnoreCase)) { break }
      $parent = [IO.Path]::GetDirectoryName($current)
      if (-not $parent -or $parent -eq $current) { throw 'Installed snapshot ancestry is outside its test root' }
      $current = $parent
    }
  }
  Assert-SnapshotPath $testFolder $testRootPath
  $privateDirectories = @('userdata','cache','uiprofile','legacyarchive','privatearchive',
    'voice_profiles','logs','profile','profiles','__pycache__','.pytest_cache','.venv')
  $privateFiles = @('api-keys.json','fish-key.json','fish-settings.json','api-presets.json',
    'auth.json','credentials.json','secrets.json','agent-preferences.json','trusted-actions.json',
    'personalization.json','installation.json')
  $privateSuffixes = @('.db','.sqlite','.sqlite3','.log','.wal','.shm')
  $coreFiles = @('CompanionStudio.exe','resources\app.asar',
    'resources\companion-backend\companion-backend.exe')
  foreach ($relative in $coreFiles) {
    $hash = (Get-FileHash -LiteralPath (Join-Path $testFolder $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $manifest.artifacts.('win-unpacked\'+$relative).sha256) {
      throw ('Installed snapshot source does not match the build manifest: '+$relative)
    }
  }
  # 按层枚举，先拒绝 junction/symlink 再进入目录；不使用递归复制跟随
  # 突然出现的链接。发现疑似运行档案就停止，不能偷偷漏掉后称完整。
  $pending = New-Object 'Collections.Generic.Queue[string]'
  $directories = New-Object 'Collections.Generic.List[string]'
  $files = New-Object 'Collections.Generic.List[object]'
  $pending.Enqueue($testFolder)
  while ($pending.Count) {
    $current = $pending.Dequeue()
    if ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
      throw 'Installed snapshot source contains a filesystem link'
    }
    foreach ($item in @(Get-ChildItem -LiteralPath $current -Force)) {
      if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Installed snapshot source contains a filesystem link'
      }
      $relative = $item.FullName.Substring($testFolder.Length+1)
      $name = $item.Name.ToLowerInvariant()
      # 唯一已确认的安装器附加文件是根目录卸载器；不把未知临时
      # 目录或深层同名文件通过通配符静默排除，仍由后续审计阻断。
      if (-not $item.PSIsContainer -and $relative -eq 'Uninstall CompanionStudio.exe') {
        $report.installed_snapshot.excluded_installer_files += $relative
        continue
      }
      if ($item.PSIsContainer) {
        if ($privateDirectories -contains $name) { throw 'Installed snapshot source contains a runtime/profile directory' }
        $directories.Add($relative)
        $pending.Enqueue($item.FullName)
      } else {
        if ($privateFiles -contains $name -or $name -eq '.env' -or
            ($name.StartsWith('.env.') -and $name -ne '.env.example') -or
            $privateSuffixes -contains $item.Extension.ToLowerInvariant() -or
            $name.EndsWith('.db-wal') -or $name.EndsWith('.db-shm') -or
            $item.Extension -eq '.tmp') {
          throw 'Installed snapshot source contains a private configuration or runtime file'
        }
        $files.Add([pscustomobject]@{source=$item.FullName;relative=$relative;bytes=$item.Length;
          sha256=(Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()})
      }
    }
  }
  New-Item -ItemType Directory -Path $snapshotRoot | Out-Null
  New-Item -ItemType Directory -Path $snapshotFolder | Out-Null
  foreach ($relative in $directories) {
    Assert-SnapshotPath (Join-Path $testFolder $relative) $testFolder
    Assert-SnapshotPath (Join-Path $snapshotFolder $relative) $snapshotFolder
    New-Item -ItemType Directory -Path (Join-Path $snapshotFolder $relative) | Out-Null
  }
  foreach ($file in $files) {
    Assert-SnapshotPath $file.source $testFolder
    $destination = Join-Path $snapshotFolder $file.relative
    Assert-SnapshotPath $destination $snapshotFolder
    Copy-Item -LiteralPath $file.source -Destination $destination
    if ((Get-Item -LiteralPath $destination).Length -ne $file.bytes -or
        (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.sha256) {
      throw ('Installed snapshot file copy mismatch: '+$file.relative)
    }
  }
  foreach ($relative in $coreFiles) {
    $hash = (Get-FileHash -LiteralPath (Join-Path $snapshotFolder $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $manifest.artifacts.('win-unpacked\'+$relative).sha256) {
      throw ('Installed snapshot destination does not match the build manifest: '+$relative)
    }
    $report.installed_snapshot.core_hashes[$relative] = $hash
  }
  $manifestSource = Join-Path $deliveryRoot 'build-manifest.json'
  $manifestDestination = Join-Path $snapshotRoot 'build-manifest.json'
  $manifestHash = (Get-FileHash -LiteralPath $manifestSource -Algorithm SHA256).Hash.ToLowerInvariant()
  Copy-Item -LiteralPath $manifestSource -Destination $manifestDestination
  if ((Get-FileHash -LiteralPath $manifestDestination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifestHash) {
    throw 'Installed snapshot build manifest copy mismatch'
  }
  $report.installed_snapshot.manifest_sha256 = $manifestHash
  # 每个复制文件的摘要仅证明枚举与复制一致；构建清单只覆盖三项核心
  # 文件。完整快照仍须单独执行公开产物审计，不能把此清单称为安全认证。
  $inventoryPath = Join-Path $snapshotRoot 'installed-program-manifest.json'
  $inventory = [ordered]@{product='Companion Studio';version=$manifest.build.version;
    build_id=$manifest.build.build_id;build_manifest_sha256=$manifestHash;
    files=@($files | ForEach-Object {
      [ordered]@{path=$_.relative;bytes=$_.bytes;sha256=$_.sha256}
    })}
  [IO.File]::WriteAllText($inventoryPath,($inventory | ConvertTo-Json -Depth 6),$utf8)
  $report.installed_snapshot.inventory_sha256 = (Get-FileHash -LiteralPath $inventoryPath -Algorithm SHA256).Hash.ToLowerInvariant()
  $report.installed_snapshot.files = $files.Count
  $report.installed_snapshot.verified = $true
  Save-TestReport
}

function Invoke-RegBackup([string]$Path, [string]$File) {
  $native = $Path.Replace('HKCU:\', 'HKCU\')
  $process = Start-Process -FilePath $regExe -ArgumentList @('export',$native,('"'+$File+'"'),'/y') -WindowStyle Hidden -PassThru
  $process.WaitForExit()
  if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $File) -or (Get-Item -LiteralPath $File).Length -eq 0) {
    throw 'Registry export failed; original key was not renamed'
  }
}

function Get-RegistrationOwner([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return $null }
  $values = Get-ItemProperty -LiteralPath $Path
  $property = $values.PSObject.Properties['InstallLocation']
  if ($property -and $property.Value) {
    return [IO.Path]::GetFullPath([string]$property.Value).TrimEnd('\')
  }
  # NSIS 的 Uninstall 键通常只有带引号的 UninstallString；product 键才
  # 记录 InstallLocation。只解析文件名和父目录，不执行注册表命令文本。
  $uninstall = $values.PSObject.Properties['UninstallString']
  if ($uninstall -and [string]$uninstall.Value -match '^"([^"]+)"(?:\s+/currentuser)?$') {
    $path = [IO.Path]::GetFullPath($Matches[1])
    if ([IO.Path]::GetFileName($path) -eq 'Uninstall CompanionStudio.exe') {
      return [IO.Path]::GetDirectoryName($path).TrimEnd('\')
    }
  }
  throw 'Registration has no verifiable installation owner'
}

function Assert-TestRegistration([string]$Path) {
  $owner = Get-RegistrationOwner $Path
  if ($owner -and -not $owner.Equals($testFolder, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Registration does not belong to this isolated test installation'
  }
  return $owner
}

function Stop-OnlyTestExecutable {
  foreach ($process in @(Get-Process -Name CompanionStudio -ErrorAction SilentlyContinue)) {
    if ($process.Path -and [IO.Path]::GetFullPath($process.Path).Equals($testExe, [StringComparison]::OrdinalIgnoreCase)) {
      $process.CloseMainWindow() | Out-Null
      if (-not $process.WaitForExit(10000)) { $process.Kill(); $process.WaitForExit(10000) | Out-Null }
    }
  }
}

function Test-ShortcutTarget([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return $false }
  $shell = New-Object -ComObject WScript.Shell
  $target = $shell.CreateShortcut($Path).TargetPath
  return $target -and [IO.Path]::GetFullPath($target).Equals($testExe, [StringComparison]::OrdinalIgnoreCase)
}

# 根目录不用 -Force：即使预检后有人创建同名目录，也必须失败，不能
# 把已有资料当成空测试目录。创建父目录后再检查一次重解析边界。
New-Item -ItemType Directory -Path $testsRoot -Force | Out-Null
Assert-NewTestRoot $testRootPath
New-Item -ItemType Directory -Path $testRootPath | Out-Null
New-Item -ItemType Directory -Path $backupRoot | Out-Null
try {
  Save-TestReport
  # 先导出完整 .reg，再同父键改名隔离，避免 NSIS 根据旧 InstallLocation
  # 自动卸载已有安装。每项变更及时写报告，异常时能找到保留备份。
  for ($i=0; $i -lt $registryPaths.Count; $i++) {
    $path = $registryPaths[$i]
    $row = [ordered]@{original=$path;saved=($path+'-installer-test-'+$testId);
      backup=(Join-Path $backupRoot ('registry-'+$i+'.reg'));present=(Test-Path -LiteralPath $path);
      backup_hash=$null;renamed=$false;restored=$false}
    $registryRows += $row
    Save-TestReport
    if ($row.present) {
      Invoke-RegBackup $path $row.backup
      $row.backup_hash = (Get-FileHash -LiteralPath $row.backup -Algorithm SHA256).Hash
      Rename-Item -LiteralPath $path -NewName ($guid+'-installer-test-'+$testId)
      $row.renamed = $true
      Save-TestReport
    }
  }
  for ($i=0; $i -lt $shortcutPaths.Count; $i++) {
    $path = $shortcutPaths[$i]
    $row = [ordered]@{original=$path;backup=(Join-Path $backupRoot ('shortcut-'+$i+'.lnk'));
      present=(Test-Path -LiteralPath $path);sha256=$null;moved=$false;restored=$false}
    $shortcutRows += $row
    if ($row.present) {
      $row.sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
      Copy-Item -LiteralPath $path -Destination $row.backup
      if ((Get-FileHash -LiteralPath $row.backup -Algorithm SHA256).Hash -ne $row.sha256) { throw 'Shortcut backup mismatch' }
      Remove-Item -LiteralPath $path
      $row.moved = $true
    }
    Save-TestReport
  }
  foreach ($path in $registryPaths) {
    if (Test-Path -LiteralPath $path) { throw 'Old registry protection did not complete; refusing to install' }
  }
  if (Test-InstallerStillRunning) {
    $recoveryAllowed = $false
    throw 'This installer is already running; keep saved registration and shortcuts protected'
  }
  Assert-NoMachineInstallation
  Assert-NoOtherStudioProcess
  if (-not $BackendPort) {
    $probe = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,0)
    $probe.Start(); $BackendPort=$probe.LocalEndpoint.Port; $probe.Stop()
  }
  $installationStarted = $true
  & (Join-Path $PSScriptRoot 'install-companion-studio.ps1') -Delivery $deliveryRoot -ProductHome $testProductHome -DesktopDirectory $desktopDirectory -BackendPort $BackendPort -CurrentUserOnly
  $report.installation_verified = $true
  foreach ($path in $registryPaths) {
    if (-not (Assert-TestRegistration $path)) { throw 'Installer did not register its isolated destination' }
  }
  foreach ($path in $shortcutPaths) {
    if (-not (Test-ShortcutTarget $path)) { throw 'Installer shortcut does not point to this test executable' }
  }
  $report.shortcuts_verified = $true
  Save-TestReport
  if ($RunUI) {
    $env:COMPANION_TEST_EXE = $testExe
    $env:COMPANION_TEST_HOME = $smokeHome
    $env:COMPANION_TEST_START_NOTEPAD = '0'
    & $nodeExe (Join-Path $project 'tests\ui\installed-studio-smoke.cjs')
    if ($LASTEXITCODE -ne 0) { throw 'Installed UI smoke failed; retain its report and isolated profile' }
    $smoke = Get-Content -LiteralPath (Join-Path $smokeHome 'report.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($smoke.success -ne $true -or $smoke.realModelRequests -ne 0 -or $smoke.realVoiceRequests -ne 0) { throw 'Installed UI smoke did not meet the local-only gate' }
    $report.ui_verified = $true
    Save-TestReport
  }
  if ($PreserveInstalledSnapshot) { Save-InstalledSnapshot }
} catch {
  $failure = $_
  $report.error = $_.Exception.Message
} finally {
  try {
    Stop-OnlyTestExecutable
    if ($installationStarted) {
      try { $installerActive = Test-InstallerStillRunning } catch { $recoveryAllowed = $false; throw }
      if ($installerActive) {
        $recoveryAllowed = $false
        throw 'Installer is still running; defer restoration so it cannot change restored product keys'
      }
      # 所有存活 GUID 键须归属本次目录，才调用本次的卸载器或删除残余键。
      foreach ($path in $registryPaths) { Assert-TestRegistration $path | Out-Null }
      if (Test-Path -LiteralPath $uninstaller) {
        Assert-NoOtherStudioProcess
        # NSIS 的 _?= 必须是最后一个未经引号包装的参数，避免复制到其他
        # 临时位置后提前返回；Windows 不会把这个路径作为 Shell 命令执行。
        $uninstallProcess = Start-Process -FilePath $uninstaller -ArgumentList @('/S','/currentuser',('_?='+$testFolder)) -WindowStyle Hidden -PassThru
        if (-not $uninstallProcess.WaitForExit(120000)) {
          $recoveryAllowed = $false
          throw 'Isolated uninstaller is still running; saved keys and shortcuts must remain protected'
        }
        if ($uninstallProcess.ExitCode -ne 0) { throw ('Isolated uninstaller failed: '+$uninstallProcess.ExitCode) }
      } elseif (Test-Path -LiteralPath $testExe) { throw 'Test installation has no own uninstaller; no broad deletion is allowed' }
      foreach ($path in $registryPaths) {
        if (Test-Path -LiteralPath $path) {
          Assert-TestRegistration $path | Out-Null
          Remove-Item -LiteralPath $path -Recurse
        }
      }
      $report.uninstall_verified = -not (Test-Path -LiteralPath $testExe)
      if (-not $report.uninstall_verified) { throw 'Test executable remains after uninstall' }
    }
  } catch { $report.cleanup_errors += $_.Exception.Message }
  # 即使安装尚未开始或前一项清理已抛错，也必须独立确认没有安装器仍在
  # 写同 GUID/同名快捷方式。无法核实就保留改名键，不冒险恢复原名。
  try {
    if (Test-InstallerStillRunning) { $recoveryAllowed = $false }
  } catch { $recoveryAllowed=$false; $report.cleanup_errors += 'Could not verify installer exit; defer restoring shared product names' }
  try {
    if ($uninstallProcess -and -not $uninstallProcess.HasExited) { $recoveryAllowed = $false }
  } catch { $recoveryAllowed=$false; $report.cleanup_errors += 'Could not verify uninstaller exit; defer restoring shared product names' }
  $report.recovery_deferred = -not $recoveryAllowed
  foreach ($row in $registryRows) {
    if (-not $recoveryAllowed) { continue }
    try {
      if ($row.present) {
        # 导出失败或尚未轮到此键时，它仍保持原位；不要把未发生的重命名
        # 误判成冲突，也不能尝试导入覆盖原键。
        if (-not $row.renamed -and -not (Test-Path -LiteralPath $row.saved)) {
          if (-not (Test-Path -LiteralPath $row.original)) { throw 'Untouched original registry key disappeared' }
          $row.restored = $true
          continue
        }
        if (Test-Path -LiteralPath $row.original) { throw 'Original registry name is occupied; retained saved key and .reg for recovery' }
        if (Test-Path -LiteralPath $row.saved) {
          Rename-Item -LiteralPath $row.saved -NewName $guid
        } elseif ($row.renamed) {
          if ((Get-FileHash -LiteralPath $row.backup -Algorithm SHA256).Hash -ne $row.backup_hash) {
            throw 'Retained registry backup changed; refusing to import it'
          }
          $process = Start-Process -FilePath $regExe -ArgumentList @('import',('"'+$row.backup+'"')) -WindowStyle Hidden -PassThru
          $process.WaitForExit()
          if ($process.ExitCode -ne 0) { throw 'Original registry import failed' }
        }
        if (-not (Test-Path -LiteralPath $row.original)) { throw 'Original registry key was not restored' }
        $restoredFile = $row.backup + '.restored.reg'
        Invoke-RegBackup $row.original $restoredFile
        if ((Get-FileHash -LiteralPath $restoredFile -Algorithm SHA256).Hash -ne $row.backup_hash) {
          throw 'Restored registry values differ from the retained original export'
        }
      } elseif (Test-Path -LiteralPath $row.original) { throw 'Unexpected registration remains; not deleting unverified owner' }
      $row.restored = $true
    } catch { $report.cleanup_errors += $_.Exception.Message }
  }
  foreach ($row in $shortcutRows) {
    if (-not $recoveryAllowed) { continue }
    try {
      if ($row.present -and $row.moved) {
        if ((Get-FileHash -LiteralPath $row.backup -Algorithm SHA256).Hash -ne $row.sha256) { throw 'Retained shortcut backup changed; refusing to restore it' }
        if (Test-Path -LiteralPath $row.original) {
          if (-not (Test-ShortcutTarget $row.original)) { throw 'Shortcut was changed by another owner; retained original backup' }
          Remove-Item -LiteralPath $row.original
        }
        Copy-Item -LiteralPath $row.backup -Destination $row.original
        if ((Get-FileHash -LiteralPath $row.original -Algorithm SHA256).Hash -ne $row.sha256) { throw 'Restored shortcut hash mismatch' }
      } elseif (-not $row.present -and (Test-Path -LiteralPath $row.original)) {
        if (-not (Test-ShortcutTarget $row.original)) { throw 'Generated shortcut owner is unknown; refusing to remove it' }
        Remove-Item -LiteralPath $row.original
      }
      $row.restored = $true
    } catch { $report.cleanup_errors += $_.Exception.Message }
  }
  $report.original_registry_restored = $registryRows.Count -eq 2 -and @($registryRows | Where-Object { -not $_.restored }).Count -eq 0
  $report.original_shortcuts_restored = $shortcutRows.Count -eq 2 -and @($shortcutRows | Where-Object { -not $_.restored }).Count -eq 0
  foreach ($name in $savedEnv.Keys) { [Environment]::SetEnvironmentVariable($name,$savedEnv[$name],'Process') }
  $report.success = -not $failure -and $report.cleanup_errors.Count -eq 0 -and $report.installation_verified -and $report.shortcuts_verified -and $report.uninstall_verified -and $report.original_registry_restored -and $report.original_shortcuts_restored -and (-not $RunUI -or $report.ui_verified) -and (-not $PreserveInstalledSnapshot -or $report.installed_snapshot.verified)
  Save-TestReport
}
$report | ConvertTo-Json -Depth 8
if (-not $report.success) {
  if ($failure) { Write-Error $failure }
  throw 'Isolated installer test failed; preserved profile, report and backups require review'
}
'Isolated Companion Studio installation/uninstall checks passed; original registration and shortcuts restored.'
