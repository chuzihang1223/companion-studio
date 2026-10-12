Add-Type -AssemblyName System.Runtime.WindowsRuntime
Add-Type -AssemblyName System.Drawing
function Await-WinRT($Operation,[Type]$ResultType){
 $method=[System.WindowsRuntimeSystemExtensions].GetMethods()|Where-Object {$_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1}|Select-Object -First 1
 $task=$method.MakeGenericMethod($ResultType).Invoke($null,@($Operation))
 try{if(-not $task.Wait(15000)){throw 'Windows Runtime operation timed out'}}catch{throw $_.Exception.GetBaseException().Message}
 return $task.Result
}
function Read-Thumbnail($Properties){
 if(-not $Properties.Thumbnail){return $null}
 $stream=$null;$reader=$null
 try{
  $stream=Await-WinRT ($Properties.Thumbnail.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType,Windows.Storage.Streams,ContentType=WindowsRuntime])
  if($stream.Size -eq 0 -or $stream.Size -gt 1048576){return $null}
  $reader=[Windows.Storage.Streams.DataReader,Windows.Storage.Streams,ContentType=WindowsRuntime]::new($stream.GetInputStreamAt(0))
  [uint32]$size=$stream.Size
  $loaded=Await-WinRT ($reader.LoadAsync($size)) ([uint32])
  if($loaded -ne $size){return $null}
  $bytes=New-Object byte[] $size;$reader.ReadBytes($bytes)
  $mime=[string]$stream.ContentType
  if($mime -notin @('image/png','image/jpeg','image/webp','image/bmp','image/gif')){return $null}
  return 'data:'+ $mime + ';base64,' + [Convert]::ToBase64String($bytes)
 }catch{return $null}finally{if($reader){$reader.Dispose()};if($stream){$stream.Dispose()}}
}
function Read-Media([string]$AppId,[string]$Action='get',[string]$SessionId='',[bool]$AllowBrowser=$false,[double]$Position=0){
 $managerType=[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime]
 $manager=Await-WinRT ($managerType::RequestAsync()) $managerType
 $rows=@();$current=$manager.GetCurrentSession();$currentSource=if($current){[string]$current.SourceAppUserModelId}else{''};$counts=@{};$controlled=$false
 foreach($media in $manager.GetSessions()){
  $source=[string]$media.SourceAppUserModelId
  $browser=$source -match 'chrome|msedge|firefox|opera|brave|vivaldi|browser'
  if($browser -and -not $AllowBrowser){continue}
  $pattern=if($AppId -eq 'netease'){'cloudmusic|netease'}elseif($AppId -eq 'qqmusic'){'qqmusic'}elseif($AppId -eq 'all'){'.'}else{'^$'}
  if($source -notmatch $pattern){continue}
  $ordinal=if($counts.ContainsKey($source)){$counts[$source]}else{0};$counts[$source]=$ordinal+1
  $id=$source+'#'+$ordinal
  if($Action -ne 'get' -and -not $controlled -and ((-not $SessionId) -or $SessionId -eq $id)){
   $operation=switch($Action){'play'{$media.TryPlayAsync()}'pause'{$media.TryPauseAsync()}'next'{$media.TrySkipNextAsync()}'previous'{$media.TrySkipPreviousAsync()}'seek'{$media.TryChangePlaybackPositionAsync([long]($Position*10000000))}default{throw 'Unsupported media action'}}
   if(-not (Await-WinRT $operation ([bool]))){throw 'Media session rejected control'}
   $controlled=$true
  }
  $properties=Await-WinRT ($media.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties,Windows.Media.Control,ContentType=WindowsRuntime])
  $timeline=$media.GetTimelineProperties();$playback=$media.GetPlaybackInfo();$controls=$playback.Controls
  $duration=$timeline.EndTime.TotalSeconds-$timeline.StartTime.TotalSeconds
  $positionValue=if($duration -gt 0){[Math]::Max(0,$timeline.Position.TotalSeconds-$timeline.StartTime.TotalSeconds)}else{$null}
  $rows+=@{id=$id;source=$source;source_app=$source;title=$properties.Title;artist=$properties.Artist;album=$properties.AlbumTitle;thumbnail=(Read-Thumbnail $properties);status=([string]$playback.PlaybackStatus).ToLowerInvariant();position_seconds=$positionValue;duration_seconds=$(if($duration -gt 0){$duration}else{$null});timeline_updated_at=$timeline.LastUpdatedTime.ToString('o');active=($source -eq $currentSource);is_browser=$browser;controls=@{play=$controls.IsPlayEnabled;pause=$controls.IsPauseEnabled;next=$controls.IsNextEnabled;previous=$controls.IsPreviousEnabled;seek=$controls.IsPlaybackPositionEnabled;volume=$false}}
 }
 if($Action -ne 'get' -and $SessionId -and -not $controlled){throw 'Selected media session is no longer available'}
 return @{available=$true;sessions=@($rows)}
}
function Read-WindowText([IntPtr]$Handle,[string]$CapturePath,[bool]$Passive=$false,[double]$Scale=1){
 if([CompanionWindows]::Locked()){throw 'desktop_locked: Windows desktop is locked'}
 if(-not $Passive){[CompanionWindows]::RequireFocus($Handle)}
 $bounds=New-Object CompanionWindows+Rect
 [CompanionWindows]::GetWindowRect($Handle,[ref]$bounds)|Out-Null
 $width=$bounds.Right-$bounds.Left;$height=$bounds.Bottom-$bounds.Top
 if($width -lt 50 -or $height -lt 50 -or $width -gt 4096 -or $height -gt 4096){throw 'Window bounds unsuitable for OCR'}
 $bitmap=New-Object System.Drawing.Bitmap $width,$height
 $graphics=[System.Drawing.Graphics]::FromImage($bitmap)
 try{
  if($Passive){
   if([CompanionWindows]::IsIconic($Handle)){throw 'background_capture_unavailable: target window minimized'}
   $context=$graphics.GetHdc();try{$printed=[CompanionWindows]::PrintWindow($Handle,$context,2)}finally{$graphics.ReleaseHdc($context)}
   if(-not $printed){throw 'background_capture_unavailable: PrintWindow rejected capture'}
  }else{$graphics.CopyFromScreen($bounds.Left,$bounds.Top,0,0,$bitmap.Size)}
  $bitmap.Save($CapturePath,[System.Drawing.Imaging.ImageFormat]::Png)
 }finally{$graphics.Dispose();$bitmap.Dispose()}
 if($Scale -gt 1){
  $Scale=[Math]::Min(2,[Math]::Min(4096/$width,4096/$height))
  $original=[Drawing.Image]::FromFile($CapturePath)
  $scaled=[Drawing.Bitmap]::new([int]($width*$Scale),[int]($height*$Scale));$g=[Drawing.Graphics]::FromImage($scaled)
  $scaledPath=[IO.Path]::ChangeExtension($CapturePath,'.ocr2.png')
  try{$g.InterpolationMode=[Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic;$g.DrawImage($original,0,0,$scaled.Width,$scaled.Height);$scaled.Save($scaledPath,[Drawing.Imaging.ImageFormat]::Png)}finally{$g.Dispose();$scaled.Dispose();$original.Dispose()}
  $snapshot=Read-ImageText $scaledPath ([int]($width*$Scale)) ([int]($height*$Scale)) $bounds.Left $bounds.Top $Passive
  foreach($item in $snapshot.lines){foreach($key in @('x','y','width','height')){$item[$key]=$item[$key]/$Scale};foreach($word in $item.words){foreach($key in @('x','y','width','height')){$word[$key]=$word[$key]/$Scale}}}
  $snapshot.width=$width;$snapshot.height=$height;$snapshot.scale=$Scale;return $snapshot
 }
 return Read-ImageText $CapturePath $width $height $bounds.Left $bounds.Top $Passive
}
function Read-ImageText([string]$CapturePath,[int]$Width,[int]$Height,[int]$OriginX=0,[int]$OriginY=0,[bool]$Passive=$false){
 $storageType=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
 $file=Await-WinRT ($storageType::GetFileFromPathAsync($CapturePath)) $storageType
 $stream=Await-WinRT ($file.OpenAsync([Windows.Storage.FileAccessMode,Windows.Storage,ContentType=WindowsRuntime]::Read)) ([Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime])
 try{
  $decoderType=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
  $decoder=Await-WinRT ($decoderType::CreateAsync($stream)) $decoderType
  $software=Await-WinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime])
  $ocrType=[Windows.Media.Ocr.OcrEngine,Windows.Media.Ocr,ContentType=WindowsRuntime]
  $language=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]::new('zh-Hans')
  $engine=$ocrType::TryCreateFromLanguage($language)
  if(-not $engine){$engine=$ocrType::TryCreateFromUserProfileLanguages()}
  if(-not $engine){throw 'No Windows OCR language installed'}
  $result=Await-WinRT ($engine.RecognizeAsync($software)) ([Windows.Media.Ocr.OcrResult,Windows.Media.Ocr,ContentType=WindowsRuntime])
  $lines=@()
  foreach($line in $result.Lines){
   $words=@($line.Words);if(-not $words.Count){continue}
   $left=($words|ForEach-Object {$_.BoundingRect.X}|Measure-Object -Minimum).Minimum
   $top=($words|ForEach-Object {$_.BoundingRect.Y}|Measure-Object -Minimum).Minimum
   $right=($words|ForEach-Object {$_.BoundingRect.X+$_.BoundingRect.Width}|Measure-Object -Maximum).Maximum
   $bottom=($words|ForEach-Object {$_.BoundingRect.Y+$_.BoundingRect.Height}|Measure-Object -Maximum).Maximum
   $lines+=@{text=$line.Text;x=$left;y=$top;width=$right-$left;height=$bottom-$top;words=@($words|ForEach-Object {@{text=$_.Text;x=$_.BoundingRect.X;y=$_.BoundingRect.Y;width=$_.BoundingRect.Width;height=$_.BoundingRect.Height}})}
  }
  return @{origin=@($OriginX,$OriginY);width=$Width;height=$Height;lines=@($lines);capture=$CapturePath;language=$engine.RecognizerLanguage.LanguageTag;capture_method=$(if($Passive){'print_window'}else{'foreground_window_region'})}
 }finally{if($software){$software.Dispose()};$stream.Dispose()}
}
