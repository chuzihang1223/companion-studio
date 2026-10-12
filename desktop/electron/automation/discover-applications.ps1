# Query mode reads registered sources, never recursively searches installation drives.
param([string]$Query='', [ValidateSet('all','registered','running','start')][string]$SourceGroup='all')
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding $false
$rows=New-Object 'System.Collections.Generic.List[object]'
$metadata=New-Object 'System.Collections.Generic.List[object]'
$sourceStatus=@{}
function Save-SourceStatus([string]$Name){$sourceStatus[$Name]=@{complete=($Error.Count -eq 0);error_count=$Error.Count;error_types=@($Error|Group-Object FullyQualifiedErrorId|ForEach-Object {@{type=$_.Name;count=$_.Count}})};$Error.Clear();if($env:COMPANION_DISCOVERY_TRACE){[Console]::Error.WriteLine($Name+' '+[DateTime]::UtcNow.ToString('o'))}}
$Error.Clear()
function Normal-Name([string]$Name){return ($Name.Normalize([Text.NormalizationForm]::FormKC).ToLowerInvariant() -replace '[\W_]','')}
function Match-Target($Values){if(-not $Query){return $true};$target=Normal-Name $Query;foreach($value in $Values){if($value -and ((Normal-Name ([string]$value)).Contains($target))){return $true}};return $false}
function Exe-Path([string]$Value){
 $expanded=[Environment]::ExpandEnvironmentVariables($Value)
 if($expanded -match '^\s*"([^"\r\n]+\.exe)"'){$candidate=$matches[1]}
 elseif($expanded -match '^\s*([^\r\n]+?\.exe)(?:\s|,|$)'){$candidate=$matches[1]}
 else{return $null}
 try{$candidate=[IO.Path]::GetFullPath($candidate)}catch{return $null}
 if(Test-Path -LiteralPath $candidate -PathType Leaf){return $candidate}
 return $null
}
function Safe-App([string]$Executable){
 return $Executable -and ([IO.Path]::GetFileName($Executable) -notmatch '^(unins|uninstall|setup|update|crash|maintenancetool|dpinst|vcredist|vc_redist)') -and ($Executable -notmatch '\\(?:Package Cache|Installer)\\')
}
function Add-App($Name,$Executable,$Source,$Location='',$Icon='',$Aumid='',$LaunchTarget='',$Arguments='',$Publisher='',$PackageFamily='',$Protocol='',$WorkingDirectory='',$ProcessNames=@(),$ObservedAlias=''){
 if($SourceGroup -eq 'registered' -and $Source -in @('running','start_apps')){return}
 if($SourceGroup -eq 'running' -and $Source -ne 'running'){return}
 if($SourceGroup -eq 'start' -and $Source -ne 'start_apps'){return}
 if($Query -and -not (Match-Target @($Name,$Executable,$Aumid,$LaunchTarget,$Publisher,$Protocol,$ObservedAlias))){return}
 if(-not $Name -or (-not $Executable -and -not $Aumid -and -not $LaunchTarget -and -not $Protocol)){return}
 if($Executable -and -not (Safe-App $Executable)){return}
 $kind=if($PackageFamily){'packaged'}elseif($Executable){'desktop'}else{'registered_shell'}
 $aliases=@()
 if($Executable){try{$version=[Diagnostics.FileVersionInfo]::GetVersionInfo($Executable);$aliases=@($version.ProductName,$version.FileDescription)|Where-Object {$_ -and $_.Length -lt 150}}catch{}}
 if($ObservedAlias){$aliases+=@($ObservedAlias)}
 $rows.Add(@{name=[string]$Name;aliases=@($aliases);executable=[string]$Executable;source=$Source;install_location=[string]$Location;icon=[string]$Icon;app_user_model_id=[string]$Aumid;launch_target=[string]$LaunchTarget;arguments=[string]$Arguments;publisher=[string]$Publisher;application_kind=$kind;package_family=[string]$PackageFamily;protocol=[string]$Protocol;working_directory=[string]$WorkingDirectory;process_names=@($ProcessNames)})
}
foreach($hive in $(if($SourceGroup -in @('all','registered')){@([Microsoft.Win32.RegistryHive]::LocalMachine,[Microsoft.Win32.RegistryHive]::CurrentUser)}else{@()})){
 foreach($view in @([Microsoft.Win32.RegistryView]::Registry64,[Microsoft.Win32.RegistryView]::Registry32)){
  $base=[Microsoft.Win32.RegistryKey]::OpenBaseKey($hive,$view)
  foreach($keyPath in @('SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall','SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths')){
   $parent=$base.OpenSubKey($keyPath);if(-not $parent){continue}
   foreach($name in $parent.GetSubKeyNames()){
    $key=$parent.OpenSubKey($name);if(-not $key){continue}
    if($keyPath.EndsWith('App Paths')){
     $exe=Exe-Path ([string]$key.GetValue(''))
     if($exe){Add-App ([IO.Path]::GetFileNameWithoutExtension($name)) $exe 'app_paths' ([IO.Path]::GetDirectoryName($exe)) $exe}
    }else{
     $display=[string]$key.GetValue('DisplayName');$location=[Environment]::ExpandEnvironmentVariables([string]$key.GetValue('InstallLocation'))
     $icon=[string]$key.GetValue('DisplayIcon');$exe=Exe-Path $icon;$publisher=[string]$key.GetValue('Publisher')
     # Never execute or infer a main application from UninstallString.
     if($Query -and -not (Match-Target @($display,$icon,$publisher))){$key.Dispose();continue}
     $metadata.Add(@{name=$display;publisher=$publisher;install_location=$location;source='registry_uninstall'})
     if($exe -and -not (Safe-App $exe)){$exe=$null}
     if(-not $exe -and $location -and (Test-Path -LiteralPath $location -PathType Container)){
      $candidates=@(Get-ChildItem -LiteralPath $location -Filter '*.exe' -File | Where-Object {(Safe-App $_.FullName) -and $_.Name -notmatch 'helper|service|container|tray'})
      if($candidates.Count -eq 1){$exe=$candidates[0].FullName}
     }
     Add-App $display $exe 'registry_uninstall' $location $icon '' '' '' $publisher
    }
    $key.Dispose()
   };$parent.Dispose()
  };$base.Dispose()
 }
}
$ws=New-Object -ComObject WScript.Shell
Save-SourceStatus 'registry'
foreach($source in $(if($SourceGroup -in @('all','registered')){@(@{folder=[Environment]::GetFolderPath('StartMenu');kind='start_menu';recurse=$true},@{folder=[Environment]::GetFolderPath('CommonStartMenu');kind='start_menu';recurse=$true},@{folder=[Environment]::GetFolderPath('Desktop');kind='desktop_shortcut';recurse=$false},@{folder=[Environment]::GetFolderPath('CommonDesktopDirectory');kind='desktop_shortcut';recurse=$false})}else{@()})){
 if(-not $source.folder){continue}
 $files=if($source.recurse){Get-ChildItem -LiteralPath $source.folder -File -Recurse}else{Get-ChildItem -LiteralPath $source.folder -File}
 foreach($file in $files){
  if($file.Extension -eq '.lnk'){
   if($Query -and -not (Match-Target @($file.BaseName,$file.FullName))){continue}
   $link=$ws.CreateShortcut($file.FullName);$exe=Exe-Path ('"'+$link.TargetPath+'"')
   Add-App $file.BaseName $exe $source.kind $link.WorkingDirectory $link.IconLocation '' $file.FullName $link.Arguments '' '' '' $link.WorkingDirectory
  }elseif($file.Extension -eq '.url'){
   $content=Get-Content -LiteralPath $file.FullName
   $url=($content|Where-Object {$_ -match '^URL='}|Select-Object -First 1) -replace '^URL=',''
   if($url -match '^([a-z][a-z0-9+.-]*):' -and $matches[1] -notin @('http','https','file','ftp','mailto')){
    $scheme=$matches[1];$registered=(Test-Path -LiteralPath ('Registry::HKEY_CURRENT_USER\Software\Classes\'+$scheme)) -or (Test-Path -LiteralPath ('Registry::HKEY_LOCAL_MACHINE\Software\Classes\'+$scheme))
    if($registered){Add-App $file.BaseName '' $source.kind '' '' '' '' '' '' '' $url}
   }
  }
 }
}
$shell=New-Object -ComObject Shell.Application
Save-SourceStatus 'shortcuts'
$apps=$shell.Namespace('shell:AppsFolder');$packages=@{}
if($SourceGroup -in @('all','registered')){foreach($package in Get-AppxPackage){$packages[[string]$package.PackageFamilyName]=$package}}
foreach($item in $(if($SourceGroup -in @('all','registered')){$apps.Items()}else{@()})){
 $aumid=[string]$item.ExtendedProperty('System.AppUserModel.ID')
 if($Query -and -not(Match-Target @($item.Name,$aumid))){continue}
 $target=[string]$item.ExtendedProperty('System.Link.TargetParsingPath');$exe=Exe-Path ('"'+$target+'"')
 if(-not $exe -and $aumid -match '^[a-z]:[\\/]'){$exe=Exe-Path ('"'+$aumid+'"');if(-not $exe){continue}}
 if($aumid -match '^https?://'){continue}
 $family='';$location='';$processNames=@()
 if($aumid -match '^([^!]+)!(.+)$' -and $packages.ContainsKey($matches[1])){
  $family=$matches[1];$applicationId=$matches[2];$package=$packages[$family];$location=$package.InstallLocation
  $manifest=Get-AppxPackageManifest -Package $package
  $application=@($manifest.Package.Applications.Application|Where-Object {$_.Id -eq $applicationId})|Select-Object -First 1
  if($application.Executable){
   $path=Join-Path $location $application.Executable;$parsed=Exe-Path ('"'+$path+'"');if($parsed){$exe=$parsed}
   $processNames=@([IO.Path]::GetFileName($application.Executable))
  }
 }
 if($aumid){Add-App $item.Name $exe 'apps_folder' $location '' $aumid '' '' '' $family '' '' $processNames}
}
Save-SourceStatus 'apps_folder'
# Start Apps includes registered Win32 entries as well as package identities.
foreach($entry in $(if($SourceGroup -in @('all','start')){Get-StartApps}else{@()})){
 $appId=[string]$entry.AppID;$exe=Exe-Path ('"'+$appId+'"')
 Add-App $entry.Name $exe 'start_apps' '' '' $appId
}
Save-SourceStatus 'start_apps'
foreach($hive in $(if($SourceGroup -in @('all','registered')){@([Microsoft.Win32.RegistryHive]::LocalMachine,[Microsoft.Win32.RegistryHive]::CurrentUser)}else{@()})){
 $base=[Microsoft.Win32.RegistryKey]::OpenBaseKey($hive,[Microsoft.Win32.RegistryView]::Default)
 $key=$base.OpenSubKey('Software\RegisteredApplications')
 if($key){foreach($name in $key.GetValueNames()){
  $capabilities=[string]$key.GetValue($name);$cap=$base.OpenSubKey($capabilities)
  if(-not $cap){continue};$icon=[string]$cap.GetValue('ApplicationIcon');$exe=Exe-Path $icon
  Add-App $name $exe 'registered_applications' '' $icon;$cap.Dispose()
 };$key.Dispose()};$base.Dispose()
}
Save-SourceStatus 'registered_applications'
foreach($hive in $(if($SourceGroup -in @('all','registered')){@([Microsoft.Win32.RegistryHive]::LocalMachine,[Microsoft.Win32.RegistryHive]::CurrentUser)}else{@()})){
 $base=[Microsoft.Win32.RegistryKey]::OpenBaseKey($hive,[Microsoft.Win32.RegistryView]::Default);$classes=$base.OpenSubKey('Software\Classes')
 if($classes){foreach($name in $classes.GetSubKeyNames()){
  if($name -in @('http','https','file','ftp','mailto','tel','sms','search','search-ms')){continue}
  $key=$classes.OpenSubKey($name);if(-not $key){continue}
  if($null -eq $key.GetValue('URL Protocol')){$key.Dispose();continue}
  $command=$key.OpenSubKey('shell\open\command');$exe=if($command){Exe-Path ([string]$command.GetValue(''))}else{$null}
  if($exe){Add-App $name $exe 'url_protocol' ([IO.Path]::GetDirectoryName($exe)) $exe '' '' '' '' '' ($name+':')}
  if($command){$command.Dispose()};$key.Dispose()
 };$classes.Dispose()};$base.Dispose()
}
# Observe only process metadata and the owning application; do not persist private window titles.
Save-SourceStatus 'protocols'
foreach($process in $(if($SourceGroup -in @('all','running')){Get-Process}else{@()})){
 $exe=$process.Path;if(-not $exe -or -not (Safe-App $exe)){continue}
 $description=$process.FileVersionInfo.FileDescription
 if($process.MainWindowHandle -ne 0 -or $description){
  $label=if($description){$description}else{$process.ProcessName}
  if(-not $Query -or (Match-Target @($label,$process.ProcessName,$process.MainWindowTitle))){Add-App $label $exe 'running' ([IO.Path]::GetDirectoryName($exe)) $exe '' '' '' '' '' '' '' @() $Query}
 }
}
Save-SourceStatus 'running'
[Console]::WriteLine((@{applications=@($rows.ToArray());catalog_metadata=@($metadata.ToArray());source_status=$sourceStatus;scanned_at=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Depth 7 -Compress))
