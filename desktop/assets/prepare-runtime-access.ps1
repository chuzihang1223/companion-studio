param([Parameter(Mandatory=$true)][string]$ProgramDirectory,[switch]$CheckOnly,[switch]$DevelopmentRuntime)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility') -ErrorAction Stop
$program = (Resolve-Path -LiteralPath $ProgramDirectory).Path.TrimEnd('\')
$executable = 'CompanionStudio.exe'
if ($DevelopmentRuntime) {
  $development = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\node_modules\electron\dist')).TrimEnd('\')
  if ($program -cne $development) { throw 'Development runtime must be this source tree''s locked Electron directory' }
  $executable = 'electron.exe'
}
if ($program -eq [IO.Path]::GetPathRoot($program).TrimEnd('\') -or
    -not (Test-Path -LiteralPath (Join-Path $program $executable))) {
  throw 'Choose the installed Companion Studio program directory'
}
# Windows LPAC 渲染进程需要读取运行文件。仅增只读/执行权限，不移除
# 原 ACL，不授予写入权限，也不让权限继承到后来建立的用户档案。
# Electron's install-directory validation also requires ALL APPLICATION PACKAGES
# when an inherited package SID exists. Both groups retain explicit read access
# only; the rules must not propagate into future profile/cache directories.
$sandboxSids = @('S-1-15-2-1','S-1-15-2-2')
$rules = @($sandboxSids | ForEach-Object {
  $sid = New-Object Security.Principal.SecurityIdentifier($_)
  New-Object Security.AccessControl.FileSystemAccessRule(
    $sid,[Security.AccessControl.FileSystemRights]::ReadAndExecute,
    [Security.AccessControl.InheritanceFlags]::None,
    [Security.AccessControl.PropagationFlags]::None,
    [Security.AccessControl.AccessControlType]::Allow
  )
})
$files = @($executable,'chrome_100_percent.pak','chrome_200_percent.pak',
  'd3dcompiler_47.dll','dxcompiler.dll','dxil.dll','ffmpeg.dll','icudtl.dat',
  'resources.pak','snapshot_blob.bin','v8_context_snapshot.bin','vk_swiftshader.dll',
  'vulkan-1.dll','resources\app.asar','resources\default_app.asar')
$targets = @($program)
foreach ($relative in $files) {
  $file = Join-Path $program $relative
  if (Test-Path -LiteralPath $file -PathType Leaf) { $targets += $file }
}
foreach ($folder in @('locales','resources')) {
  $directory = Join-Path $program $folder
  if (Test-Path -LiteralPath $directory -PathType Container) { $targets += $directory }
}
$locales = Join-Path $program 'locales'
if (Test-Path -LiteralPath $locales) {
  $targets += @(Get-ChildItem -LiteralPath $locales -File | Where-Object {
    # 区域既可能是 US/CN，也可能是 es-419 的三位数字；只选 locale PAK。
    $_.Name -cmatch '^[a-z]{2,3}(?:-(?:[A-Z][A-Za-z0-9]*|[0-9]{3}))?\.pak$'
  } | ForEach-Object { $_.FullName })
}
foreach ($target in $targets) {
  $full = [IO.Path]::GetFullPath($target)
  if ($full -ne $program -and -not $full.StartsWith($program+'\',[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Runtime read scope escaped the program directory'
  }
  $ancestor = $full
  while ($ancestor -and $ancestor.StartsWith($program,[StringComparison]::OrdinalIgnoreCase)) {
    if ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
      throw 'Runtime read preparation must not follow filesystem links'
    }
    if ($ancestor -eq $program) { break }
    $ancestor = [IO.Path]::GetDirectoryName($ancestor)
  }
  $acl = Get-Acl -LiteralPath $full
  if (-not $CheckOnly) {
    foreach ($rule in $rules) { $acl.AddAccessRule($rule) }
    Set-Acl -LiteralPath $full -AclObject $acl
  } else {
    foreach ($sid in $sandboxSids) {
      $allowed = @($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | Where-Object {
        $_.IdentityReference.Value -eq $sid -and
        $_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
        ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::ReadAndExecute) -eq [Security.AccessControl.FileSystemRights]::ReadAndExecute
      })
      if (-not $allowed.Count) { throw 'Public runtime sandbox read permission is missing' }
    }
  }
}
# 不枚举 UserData、Cache、用户导入素材或其他目录；日志只有范围与数量。
@{checked=[bool]$CheckOnly;public_runtime_targets=$targets.Count;
  sandbox_groups=$sandboxSids;inherited_permission_added=$false;user_profile_changed=$false} | ConvertTo-Json
