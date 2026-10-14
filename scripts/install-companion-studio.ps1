param(
  [Parameter(Mandatory=$true)][string]$Delivery,
  [Parameter(Mandatory=$true)][string]$ProductHome,
  [string]$DesktopDirectory = [Environment]::GetFolderPath('Desktop'),
  [int]$BackendPort = 8770
)
$ErrorActionPreference = 'Stop'
$deliveryRoot = (Resolve-Path -LiteralPath $Delivery).Path
$studioHome = [IO.Path]::GetFullPath($ProductHome).TrimEnd('\')
if ($studioHome -eq [IO.Path]::GetPathRoot($studioHome).TrimEnd('\') -or $studioHome.StartsWith([Environment]::GetFolderPath('Windows'),[StringComparison]::OrdinalIgnoreCase)) { throw 'Choose a dedicated application folder' }
$manifest = Get-Content -LiteralPath (Join-Path $deliveryRoot 'build-manifest.json') -Raw | ConvertFrom-Json
if ($manifest.build.app_id -ne 'com.companionstudio.desktop' -or $manifest.build.product_name -ne 'Companion Studio' -or $manifest.build.clean_distribution -ne $true) { throw 'Not a clean Companion Studio build' }
$version = $manifest.build.version
$setupName = 'Companion-Studio-'+$version+'-Setup.exe'; $setup = Join-Path $deliveryRoot $setupName
if ((Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.artifacts.$setupName.sha256) { throw 'Installer hash mismatch' }
$destination = Join-Path $studioHome 'Release\installed'
$exe = Join-Path $destination 'CompanionStudio.exe'; $data = Join-Path $studioHome 'UserData'
$existingFiles = @(Get-ChildItem -LiteralPath $data -Force -ErrorAction SilentlyContinue)
if ($existingFiles.Count -and -not (Test-Path -LiteralPath (Join-Path $studioHome 'installation.json'))) { throw 'Existing profile has no Companion Studio installation marker; choose a new folder' }
if ($existingFiles.Count) {
  $oldInstallation = Get-Content -LiteralPath (Join-Path $studioHome 'installation.json') -Raw | ConvertFrom-Json
  if ($oldInstallation.product_name -ne 'Companion Studio') { throw 'Existing profile belongs to another product; choose a new folder' }
}
foreach ($process in @(Get-Process -Name CompanionStudio -ErrorAction SilentlyContinue)) {
  if ($process.Path -and [IO.Path]::GetFullPath($process.Path) -eq $exe) {
    $process.CloseMainWindow() | Out-Null
    if (-not $process.WaitForExit(15000)) { $process.Kill(); $process.WaitForExit(10000) | Out-Null }
  }
}
$before = [ordered]@{}
if (Test-Path -LiteralPath $data) {
  foreach ($file in (Get-ChildItem -LiteralPath $data -Recurse -File)) {
    $before[$file.FullName.Substring($data.Length+1)] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
  }
}
$env:TEMP = Join-Path $studioHome 'Cache'; $env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $studioHome,$data,$env:TEMP,(Join-Path $studioHome 'Release') | Out-Null
$utf8 = New-Object Text.UTF8Encoding($false)
if (-not (Test-Path -LiteralPath (Join-Path $data '.env'))) {
  [IO.File]::WriteAllText((Join-Path $data '.env'),("BACKEND_PORT=$BackendPort`r`nCOMPANION_CLEAN_DISTRIBUTION=1`r`nCOMPANION_DEFAULT_CHARACTER=assistant`r`nVOICE_PREFETCH_ENABLED=false`r`n"),$utf8)
}
$process = Start-Process -FilePath $setup -ArgumentList @('/S',('/D='+$destination)) -WindowStyle Hidden -PassThru
$process.WaitForExit()
if ($process.ExitCode -ne 0) { throw ('Installer failed: '+$process.ExitCode) }
foreach ($relative in @('CompanionStudio.exe','resources\app.asar','resources\companion-backend\companion-backend.exe')) {
  $file = Join-Path $destination $relative
  if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.artifacts.('win-unpacked\'+$relative).sha256) { throw ('Installed file mismatch: '+$relative) }
}
foreach ($relative in $before.Keys) {
  if ((Get-FileHash -LiteralPath (Join-Path $data $relative) -Algorithm SHA256).Hash -ne $before[$relative]) { throw 'Existing user data changed during installation' }
}
if ((Get-Item -LiteralPath $exe).VersionInfo.ProductVersion -notlike ($version+'*')) { throw 'Incorrect installed version' }
New-Item -ItemType Directory -Force -Path $DesktopDirectory | Out-Null
$shortcut = Join-Path $DesktopDirectory 'Companion Studio.lnk'
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcut); $link.TargetPath = $exe; $link.WorkingDirectory = $studioHome
$link.IconLocation = $exe+',0'; $link.Description = 'Companion Studio '+$version; $link.Save()
$report = @{pass=$true;product_name='Companion Studio';version=$version;build_id=$manifest.build.build_id;home=$studioHome;installed=$destination;desktop_shortcut=$shortcut;profile_imported=$false;existing_profile_preserved=$true;external_requests=0}
# Runtime reports contain local paths and stay in the private application folder.
[IO.File]::WriteAllText((Join-Path $studioHome 'installation.json'),($report|ConvertTo-Json -Depth 5),$utf8)
'Companion Studio installed; existing Studio user data preserved.'
