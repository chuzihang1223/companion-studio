# Electron keeps SemVer in FileVersion and uses a numeric ProductVersion for
# prereleases. Both are checked after the installed core-file SHA256 checks.
function Assert-InstalledVersion([object]$VersionInfo, [string]$Expected) {
  if ($Expected -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$') {
    throw 'Invalid manifest version'
  }
  if ($VersionInfo.FileVersion -ne $Expected) { throw 'Incorrect installed file version' }
  $numeric = ($Expected -split '[-+]')[0]
  if ($VersionInfo.ProductVersion -notmatch ('^' + [regex]::Escape($numeric) + '(?:\.0)?$')) {
    throw 'Incorrect installed product version'
  }
}
