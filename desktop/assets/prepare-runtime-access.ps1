param([Parameter(Mandatory=$true)][string]$ProgramDirectory,[switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility') -ErrorAction Stop
$program = (Resolve-Path -LiteralPath $ProgramDirectory).Path.TrimEnd('\')
if ($program -eq [IO.Path]::GetPathRoot($program).TrimEnd('\') -or
    -not (Test-Path -LiteralPath (Join-Path $program 'CompanionStudio.exe'))) {
  throw 'Choose the installed Companion Studio program directory'
}
# Windows LPAC 渲染进程需要读取运行文件。仅增只读/执行权限，不移除
# 原 ACL，不授予写入权限，也不让权限继承到后来建立的用户档案。
$sid = New-Object Security.Principal.SecurityIdentifier('S-1-15-2-2')
$rule = New-Object Security.AccessControl.FileSystemAccessRule(
  $sid,[Security.AccessControl.FileSystemRights]::ReadAndExecute,
  [Security.AccessControl.InheritanceFlags]::None,
  [Security.AccessControl.PropagationFlags]::None,
  [Security.AccessControl.AccessControlType]::Allow
)
$files = @('CompanionStudio.exe','chrome_100_percent.pak','chrome_200_percent.pak',
  'd3dcompiler_47.dll','dxcompiler.dll','dxil.dll','ffmpeg.dll','icudtl.dat',
  'resources.pak','snapshot_blob.bin','v8_context_snapshot.bin','vk_swiftshader.dll',
  'vulkan-1.dll','resources\app.asar')
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
    $_.Name -cmatch '^[a-z]{2,3}(?:-[A-Z][A-Za-z0-9]*)?\.pak$'
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
  if (-not $CheckOnly) {
    $acl = Get-Acl -LiteralPath $full
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $full -AclObject $acl
  }
}
# 不枚举 UserData、Cache、用户导入素材或其他目录；日志只有范围与数量。
@{checked=[bool]$CheckOnly;public_runtime_targets=$targets.Count;
  inherited_permission_added=$false;user_profile_changed=$false} | ConvertTo-Json
