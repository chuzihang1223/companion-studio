# All COM objects stay on the original serial MTA worker. Returned frames contain
# plain bounded data only; no AutomationElement crosses the process boundary.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -Path (Join-Path $PSScriptRoot 'visual-window.cs') -ReferencedAssemblies 'System.Drawing','System.Windows.Forms'
$script:visualFrames=@{}
function Assert-VisualIdentity($Request,[long]$Handle){
 if(-not $Request.expected_expires_ms -or [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() -ge [long]$Request.expected_expires_ms){throw 'COMPUTER_PERMISSION_DENIED'}
 if([CompanionWindows]::Locked()){throw 'DESKTOP_LOCKED'}
 $state=Window-State $Handle
 if(-not $state.visible -or $state.minimized){throw 'WINDOW_NOT_VISIBLE'}
 if(-not $Request.expected_pid -or -not $Request.expected_started_at -or -not $Request.expected_executable -or -not $Request.visual_nonce -or ([string]$Request.visual_nonce).Length -ne 64){throw 'COMPUTER_PERMISSION_DENIED'}
 $exe=[CompanionWindows]::ProcessPath([int]$state.pid)
 if($state.pid -ne $Request.expected_pid -or $state.started_at -ne $Request.expected_started_at -or -not $exe.Equals([string]$Request.expected_executable,[StringComparison]::OrdinalIgnoreCase)){throw 'WINDOW_IDENTITY_CHANGED'}
 $denied='^(powershell|pwsh|cmd|conhost|windowsterminal|consent|lockapp|credentialuibroker|securityhealthui|taskmgr|regedit|mmc|keepass|keepassxc|1password|bitwarden|wechat|weixin|qq|tim|telegram|signal|discord|codex|chatgpt|companionstudio|chrome|msedge|firefox)\.exe$'
 if([IO.Path]::GetFileName($exe) -match $denied -or $state.title -match '密码|密钥|验证码|支付|付款|银行卡|sign.?in|password|windows security|user account control'){throw 'COMPUTER_SENSITIVE_TARGET'}
 return $state
}
function Test-VisualSensitive([string]$Text){
 return $Text -match '(?i)password|api.?key|access.?token|secret|密码|密钥|验证码|银行卡|sk-[a-z0-9_-]{12,}|github_pat_[a-z0-9_]{12,}|gh[pousr]_[a-z0-9]{12,}|bearer\s+\S{8,}|\beyJ[a-z0-9_-]{12,}\.|(?:\d[ -]?){13,19}|-----BEGIN .*PRIVATE KEY'
}
function Get-VisualIdentityHash([string]$Text){
 $Text=$Text.Substring(0,[Math]::Min(120,$Text.Length)).ToUpperInvariant()
 $hasher=[Security.Cryptography.SHA256]::Create()
 try{return [BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text))).Replace('-','').ToLowerInvariant()}finally{$hasher.Dispose()}
}
function Read-VisualTree([long]$Handle){
 $cache=New-Object System.Windows.Automation.CacheRequest
 $cache.TreeScope=[System.Windows.Automation.TreeScope]::Element
 foreach($p in @('Name','AutomationId','ControlType','IsEnabled','IsOffscreen','IsPassword','BoundingRectangle','NativeWindowHandle')){$cache.Add([System.Windows.Automation.AutomationElement]::("${p}Property"))}
 $root=([System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Handle)).GetUpdatedCache($cache)
 $walker=[System.Windows.Automation.TreeWalker]::RawViewWalker
 $queue=New-Object 'System.Collections.Generic.Queue[object]';$queue.Enqueue(@($root,0))
 $rows=New-Object Collections.ArrayList;$masks=New-Object Collections.ArrayList
 $maskedHashes=New-Object Collections.ArrayList;$filteredSubtrees=$false
 $clock=[Diagnostics.Stopwatch]::StartNew();$truncated=$false;$script:visualControls=@{}
 while($queue.Count -gt 0 -and $rows.Count -lt 600 -and $clock.ElapsedMilliseconds -lt 2500){
  $entry=$queue.Dequeue();$node=$entry[0];$depth=$entry[1]
  try{
   $current=$node.Cached;$r=$current.BoundingRectangle
   # Offscreen UIA nodes may expose Rectangle.Empty (Infinity). JSON must remain
   # finite, and such nodes cannot supply an actionable coordinate candidate.
   $finite=(-not $r.IsEmpty) -and $r.Width -gt 0 -and $r.Height -gt 0
   foreach($v in @($r.X,$r.Y,$r.Width,$r.Height)){if([double]::IsInfinity($v) -or [double]::IsNaN($v)){$finite=$false}}
   $name=[string]$current.Name;$value='';$pattern=$null
   $sensitive=$current.IsPassword -or [VisualWindow]::Password($current.NativeWindowHandle) -or (Test-VisualSensitive $name) -or (Test-VisualSensitive ([string]$current.AutomationId))
   if(-not $sensitive -and $node.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$pattern)){$value=[string]$pattern.Current.Value;$sensitive=Test-VisualSensitive $value}
   $rid=($node.GetRuntimeId() -join ':')
   if($sensitive){
    $maskedHashes.Add((Get-VisualIdentityHash ([string]$current.AutomationId)))|Out-Null
    if($null -ne $walker.GetFirstChild($node,$cache)){$filteredSubtrees=$true}
    if($finite){$left=[int][Math]::Floor($r.X)-2;$top=[int][Math]::Floor($r.Y)-2;$masks.Add(@($left,$top,([int][Math]::Ceiling($r.Right)-$left+2),([int][Math]::Ceiling($r.Bottom)-$top+2)))|Out-Null}
    elseif(-not $current.IsOffscreen){throw 'COMPUTER_UNMASKABLE_CONTENT'}
   }else{
    $row=@{identity=$rid;name=$name.Substring(0,[Math]::Min(160,$name.Length));automation_id=([string]$current.AutomationId);type=$current.ControlType.ProgrammaticName.Replace('ControlType.','');enabled=[bool]$current.IsEnabled;visible=((-not $current.IsOffscreen) -and $finite);bounds=$(if($finite){@($r.X,$r.Y,$r.Width,$r.Height)}else{@(0,0,0,0)});value=$value.Substring(0,[Math]::Min(500,$value.Length))}
    if($row.automation_id.Length -gt 120){$row.automation_id=$row.automation_id.Substring(0,120)}
    if($row.type -in @('List','Pane','Tree')){$scroll=$null;if($node.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern,[ref]$scroll)){$row.scroll=@($scroll.Current.HorizontalScrollPercent,$scroll.Current.VerticalScrollPercent)}}
    $rows.Add($row)|Out-Null;$script:visualControls[$rid]=$node
   }
   if(-not $sensitive){
    $child=$walker.GetFirstChild($node,$cache)
    if($depth -ge 24){if($null -ne $child){$truncated=$true}}
    else{while($null -ne $child -and $clock.ElapsedMilliseconds -lt 2500){$queue.Enqueue(@($child,$depth+1));$child=$walker.GetNextSibling($child,$cache)};if($null -ne $child){$truncated=$true}}
   }
  }catch{if($_.Exception.Message -match 'COMPUTER_UNMASKABLE_CONTENT'){throw};$truncated=$true}
 }
 if($queue.Count -or $clock.ElapsedMilliseconds -ge 2500){$truncated=$true}
 return @{elements=@($rows);masks=@($masks);truncated=$truncated;masked_identity_hashes=@($maskedHashes);filtered_subtrees=$filteredSubtrees;duration_ms=$clock.Elapsed.TotalMilliseconds}
}
function Read-VisualOCR($Snapshot,[string]$Language='auto'){
 $stream=$null;$writer=$null;$software=$null
 try{
  $ocrType=[Windows.Media.Ocr.OcrEngine,Windows.Media.Ocr,ContentType=WindowsRuntime]
  $available=@($ocrType::AvailableRecognizerLanguages|ForEach-Object {$_.LanguageTag})
  $engine=$null
  if($Language -in @('zh-Hans','en-US')){$languageObject=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]::new($Language);$engine=$ocrType::TryCreateFromLanguage($languageObject)}
  if(-not $engine -and $Language -ne 'auto'){throw 'OCR_LANGUAGE_UNAVAILABLE'}
  if(-not $engine){$engine=$ocrType::TryCreateFromUserProfileLanguages()}
  if(-not $engine){throw 'OCR_LANGUAGE_UNAVAILABLE'}
  $stream=[Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]::new()
  $writer=[Windows.Storage.Streams.DataWriter,Windows.Storage.Streams,ContentType=WindowsRuntime]::new($stream)
  $writer.WriteBytes([byte[]]$Snapshot.png)
  Await-WinRT ($writer.StoreAsync()) ([uint32])|Out-Null
  $writer.DetachStream()|Out-Null;$writer.Dispose();$writer=$null;$stream.Seek(0)
  $decoderType=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
  $decoder=Await-WinRT ($decoderType::CreateAsync($stream)) $decoderType
  $software=Await-WinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime])
  if($software.PixelWidth -gt $ocrType::MaxImageDimension -or $software.PixelHeight -gt $ocrType::MaxImageDimension){throw 'SCREEN_CAPTURE_LIMIT'}
  $recognized=Await-WinRT ($engine.RecognizeAsync($software)) ([Windows.Media.Ocr.OcrResult,Windows.Media.Ocr,ContentType=WindowsRuntime])
  $lines=New-Object Collections.ArrayList
  foreach($line in $recognized.Lines){
   if($lines.Count -ge 128){break}
   $words=@($line.Words);if(-not $words.Count){continue}
   $left=($words|ForEach-Object {$_.BoundingRect.X}|Measure-Object -Minimum).Minimum
   $top=($words|ForEach-Object {$_.BoundingRect.Y}|Measure-Object -Minimum).Minimum
   $right=($words|ForEach-Object {$_.BoundingRect.X+$_.BoundingRect.Width}|Measure-Object -Maximum).Maximum
   $bottom=($words|ForEach-Object {$_.BoundingRect.Y+$_.BoundingRect.Height}|Measure-Object -Maximum).Maximum
   $text=[string]$line.Text
   if(Test-VisualSensitive $text){throw 'SCREEN_CAPTURE_SENSITIVE_CONTENT'}
   $lines.Add(@{text=$text.Substring(0,[Math]::Min(200,$text.Length));bounds=@(($Snapshot.origin[0]+$left/$Snapshot.scale),($Snapshot.origin[1]+$top/$Snapshot.scale),(($right-$left)/$Snapshot.scale),(($bottom-$top)/$Snapshot.scale));image_bounds=@($left,$top,($right-$left),($bottom-$top));confidence=$null;language=$engine.RecognizerLanguage.LanguageTag})|Out-Null
  }
   if($recognized.Lines.Count -gt 128){throw 'SCREEN_CAPTURE_SENSITIVE_CONTENT'}
   return @{lines=@($lines);language=$engine.RecognizerLanguage.LanguageTag;available_languages=$available;confidence_supported=$false;truncated=$false}
 }finally{if($writer){$writer.Dispose()};if($software){$software.Dispose()};if($stream){$stream.Dispose()}}
}
function New-VisualObservation($Request,[long]$Handle){
 $observedAt=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds();$capturedAt=$observedAt
 $timer=[Diagnostics.Stopwatch]::StartNew();$state=Assert-VisualIdentity $Request $Handle
 $geometry=[VisualWindow]::Geometry($Handle);$tree=Read-VisualTree $Handle
 $image=$null;$ocr=$null;$captureError=$null;$ocrError=$null
 $captureMs=$null;$ocrMs=$null
 if($Request.visual_capture){
  try{
   if($tree.truncated){throw 'COMPUTER_UNMASKABLE_CONTENT'}
   $capturedAt=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds();$captureTimer=[Diagnostics.Stopwatch]::StartNew();$image=[VisualWindow]::Capture($Handle,([int[]]$Request.crop),([int[][]]$tree.masks),[string]$Request.capture_scope,[string]$Request.display_id);$captureMs=$captureTimer.Elapsed.TotalMilliseconds
   # OCR is also a privacy check for self-drawn content with no UIA name. Never
   # return an unchecked preview or upload image when OCR fails or sees a secret.
   $ocrTimer=[Diagnostics.Stopwatch]::StartNew()
   try{$ocr=Read-VisualOCR $image ([string]$Request.language)}catch{
    $ocrError=if($_.Exception.Message -match 'SCREEN_CAPTURE_SENSITIVE_CONTENT'){'SCREEN_CAPTURE_SENSITIVE_CONTENT'}elseif($_.Exception.Message -match 'OCR_LANGUAGE_UNAVAILABLE'){'OCR_LANGUAGE_UNAVAILABLE'}else{'OCR_FAILED'}
    $captureError=$ocrError;$image=$null;$ocr=$null
   }finally{$ocrMs=$ocrTimer.Elapsed.TotalMilliseconds}
  }catch{$captureError=if($_.Exception.Message -match 'COMPUTER_UNMASKABLE_CONTENT'){'COMPUTER_UNMASKABLE_CONTENT'}elseif($_.Exception.Message -match 'SCREEN_CAPTURE_PROTECTED'){'SCREEN_CAPTURE_PROTECTED'}else{'SCREEN_CAPTURE_FAILED'}}
 }
 # Recheck after the expensive COM/OCR operations. A changed window is never a
 # successful old observation, even when a screenshot API returned bytes.
 $after=Assert-VisualIdentity $Request $Handle
 if(($after.bounds -join ':') -ne ($state.bounds -join ':')){throw 'WINDOW_IDENTITY_CHANGED'}
 $identity=[Guid]::NewGuid().ToString();$now=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
 if($now-$observedAt -ge 8000){throw 'COMPUTER_OBSERVATION_EXPIRED'}
 if($ocr){foreach($line in $ocr.lines){$line.source_image_id=$identity;$line.capture_timestamp=$capturedAt;$line.coordinate_space='physical_screen_px'}}
 foreach($key in @($script:visualFrames.Keys)){if($now-$script:visualFrames[$key].created_ms -gt 8000){$script:visualFrames.Remove($key)}}
 if($script:visualFrames.Count -ge 8){$oldest=$script:visualFrames.Keys|Sort-Object {$script:visualFrames[$_].created_ms}|Select-Object -First 1;$script:visualFrames.Remove($oldest)}
 $frame=@{observation_id=$identity;created_ms=$observedAt;nonce=$Request.visual_nonce;window=$state;geometry=$geometry;elements=$tree.elements;tree_truncated=$tree.truncated;masked_identity_hashes=$tree.masked_identity_hashes;filtered_subtrees=$tree.filtered_subtrees;ocr=$ocr;capture_error=$captureError;ocr_error=$ocrError;privacy_checked=([bool]$image -and [bool]$ocr);duration_ms=$timer.Elapsed.TotalMilliseconds;uia_duration_ms=$tree.duration_ms;capture_duration_ms=$captureMs;ocr_duration_ms=$ocrMs}
 $script:visualFrames[$identity]=$frame
 $result=@{};foreach($key in $frame.Keys){if($key -ne 'nonce'){$result[$key]=$frame[$key]}}
 if($image){$result.image=@{data_uri=('data:image/png;base64,'+[Convert]::ToBase64String([byte[]]$image.png));width=$image.width;height=$image.height;origin=$image.origin;scale=$image.scale;redacted_regions=$image.redacted_regions;scope=$image.scope;source_image_id=$identity;coordinate_space='image_px'}}
 return $result
}
