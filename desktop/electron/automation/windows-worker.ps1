$ErrorActionPreference='Stop'
[Console]::InputEncoding=New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding $false
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -Path (Join-Path $PSScriptRoot 'window-api.cs')
[CompanionWindows]::SetProcessDpiAwarenessContext([IntPtr](-4))|Out-Null
. (Join-Path $PSScriptRoot 'winrt.ps1')
Add-Type -Path (Join-Path $PSScriptRoot 'player-visual.cs') -ReferencedAssemblies 'System.Drawing'
. (Join-Path $PSScriptRoot 'qqmusic-automation.ps1')
function Window-State([long]$Handle){
 $pointer=[IntPtr]$Handle;[uint32]$owner=0
 [CompanionWindows]::GetWindowThreadProcessId($pointer,[ref]$owner)|Out-Null
 $bounds=New-Object CompanionWindows+Rect
 [CompanionWindows]::GetWindowRect($pointer,[ref]$bounds)|Out-Null
 $memory=$null;$started=$null
 try{$process=[Diagnostics.Process]::GetProcessById([int]$owner);$memory=$process.WorkingSet64;$started=$process.StartTime.ToUniversalTime().ToString('o')}catch{}
 return @{handle=$Handle;pid=$owner;title=[CompanionWindows]::Title($pointer);class_name=[CompanionWindows]::WindowClass($pointer);memory_bytes=$memory;started_at=$started;visible=[CompanionWindows]::IsWindowVisible($pointer);minimized=[CompanionWindows]::IsIconic($pointer);responding=[CompanionWindows]::Responding($pointer);foreground=([CompanionWindows]::GetAncestor([CompanionWindows]::GetForegroundWindow(),2) -eq $pointer);bounds=@($bounds.Left,$bounds.Top,($bounds.Right-$bounds.Left),($bounds.Bottom-$bounds.Top))}
}
function Inspect-Tree([IntPtr]$Handle){
 $script:treeDiagnostics=@()
 # Fetch each node's properties together. Reading Current repeatedly and asking
 # every pane for selection patterns can exhaust the budget on a cold QQ window.
 $cache=New-Object System.Windows.Automation.CacheRequest
 $cache.TreeScope=[System.Windows.Automation.TreeScope]::Element
 foreach($property in @('Name','AutomationId','ClassName','ControlType','IsEnabled','IsOffscreen','IsPassword','BoundingRectangle')){
  $cache.Add([System.Windows.Automation.AutomationElement]::("${property}Property"))
 }
 $cache.Add([System.Windows.Automation.SelectionItemPattern]::IsSelectedProperty)
 $root=([System.Windows.Automation.AutomationElement]::FromHandle($Handle)).GetUpdatedCache($cache)
 $walker=[System.Windows.Automation.TreeWalker]::RawViewWalker
 $queue=New-Object 'System.Collections.Generic.Queue[object]';$queue.Enqueue(@($root,0,$null))
 $clock=[Diagnostics.Stopwatch]::StartNew()
 $rows=@();$script:controls=@{}
 while($queue.Count -gt 0 -and $rows.Count -lt 1800 -and $clock.ElapsedMilliseconds -lt 3500){
  $entry=$queue.Dequeue();$node=$entry[0];$depth=$entry[1];$parentKey=$entry[2]
  try{
   $c=$node.Cached;if($c.IsPassword){continue}
   $id=[string]$rows.Count;$script:controls[$id]=$node
   $selected=$false
   try{$selection=$node.GetCachedPropertyValue([System.Windows.Automation.SelectionItemPattern]::IsSelectedProperty,$true);if($selection -is [bool]){$selected=$selection}}catch{}
   $rows+=@{key=$id;parent_key=$parentKey;depth=$depth;selected=$selected;name=$c.Name;automation_id=$c.AutomationId;class_name=$c.ClassName;type=$c.ControlType.ProgrammaticName.Replace('ControlType.','');enabled=$c.IsEnabled;visible=(-not $c.IsOffscreen);offscreen=$c.IsOffscreen;bounds=@($c.BoundingRectangle.X,$c.BoundingRectangle.Y,$c.BoundingRectangle.Width,$c.BoundingRectangle.Height)}
   if($depth -lt 40){$child=$walker.GetFirstChild($node,$cache);while($null -ne $child -and $clock.ElapsedMilliseconds -lt 3500){$queue.Enqueue(@($child,$depth+1,$id));$child=$walker.GetNextSibling($child,$cache)}}
  }catch{$script:treeDiagnostics+=@{depth=$depth;message=$_.Exception.Message;elapsed_ms=$clock.ElapsedMilliseconds}}
 }
 return @($rows)
}
function OCR-Chunks($Ocr){
 return @($Ocr.lines|ForEach-Object {
  $groups=New-Object Collections.ArrayList
  foreach($word in $_.words){$last=if($groups.Count){$groups[$groups.Count-1]}else{$null}
   if($last -and $word.x-$last.x-$last.width -lt [Math]::Max($word.height,$last.height)*.8){$last.text+=' '+$word.text;$last.width=$word.x+$word.width-$last.x}
   else{$groups.Add(@{text=$word.text;x=$word.x;y=$word.y;width=$word.width;height=$word.height})|Out-Null}
  };$groups
 })
}
while($null -ne ($line=[Console]::ReadLine())){
 try{
  $request=$line|ConvertFrom-Json
  if($request.action -eq 'media_all'){$result=Read-Media 'all' $request.operation $request.session_id ([bool]$request.allow_browser) ([double]$request.position)}
  elseif($request.action -eq 'media'){$result=Read-Media $request.app $request.operation}
  else{
   if($request.action -eq 'launch_entry'){
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=[string]$request.launch_target;$info.UseShellExecute=$true
    if($request.launch_arguments){$info.Arguments=[string]$request.launch_arguments}
    if($request.launch_cwd){$info.WorkingDirectory=[string]$request.launch_cwd}
    $launched=[Diagnostics.Process]::Start($info)
    $result=@{pid=$null;created=0}
    if($launched){$result.pid=$launched.Id;$result.created=[CompanionWindows]::ProcessCreated($launched.Id);$launched.Dispose()}
    [Console]::WriteLine((@{id=$request.id;result=$result}|ConvertTo-Json -Compress));continue
   }
   $matching=@([CompanionWindows]::MatchingProcesses([string[]](@($request.executable)+@($request.executables)|Where-Object {$_}),[string]$request.install_location,[string]$request.app_user_model_id,[bool]$request.include_descendants,[int[]]$request.descendant_seed_ids,[double[]]$request.descendant_seed_times))
   $windows=@([CompanionWindows]::WindowsForProcesses([int[]]$matching,$true))
   if($request.action -eq 'observe'){$result=@{processes=@($matching|ForEach-Object {@{pid=$_;path=[CompanionWindows]::ProcessPath($_)}});windows=@($windows|ForEach-Object {Window-State $_})}}
   elseif($request.action -eq 'list'){$result=@{windows=@($windows|ForEach-Object {Window-State $_})}}
   else{
    if(-not $windows.Count){throw 'Target window not found'}
    $handle=if($request.handle){[long]$request.handle}else{$windows[0]}
    if($handle -notin $windows){throw 'Window does not belong to authorized executable'}
    $pointer=[IntPtr]$handle
    switch($request.action){
     'state'{$result=Window-State $handle}
     'focus'{if([CompanionWindows]::Locked()){throw 'desktop_locked: Windows desktop is locked'};if(-not [CompanionWindows]::Focus($pointer)){throw ([CompanionWindows]::LastFocusFailure+': Windows did not focus target')};$result=Window-State $handle}
     'inspect'{$result=@{window=(Window-State $handle);elements=@(Inspect-Tree $pointer)};if($env:COMPANION_TEST_DIAGNOSTICS -eq '1'){$result.tree_diagnostics=$script:treeDiagnostics}}
     'close'{$result=@{requested=[CompanionWindows]::CloseWindow($pointer)}}
     'ocr'{$result=Read-WindowText $pointer $request.capture $false ([double]$request.scale)}
     'player'{$ocr=Read-WindowText $pointer $request.capture ([bool]$request.passive) ([double]$request.scale);$visual=[PlayerVisual]::Inspect($request.capture,[string]$request.app);$elements=if($request.app -eq 'qqmusic'){@(Inspect-Tree $pointer)}else{@()};$result=@{ocr=$ocr;visual=$visual;elements=$elements;window=(Window-State $handle)}}
     'click_player'{
      $ocr=Read-WindowText $pointer $request.capture;$visual=[PlayerVisual]::Inspect($request.capture,[string]$request.app)
      if($visual.Status -eq 'unknown'){throw 'Player control cannot be verified'}
      [CompanionWindows]::Click($pointer,[int]($ocr.origin[0]+$visual.X+$visual.Width/2),[int]($ocr.origin[1]+$visual.Y+$visual.Height/2),$false)
      $result=@{ok=$true;previous=$visual.Status;method='window_visual_player'}
     }
     'key'{[CompanionWindows]::Press($pointer,[string]$request.key);$result=@{ok=$true}}
     'qq_song_tab'{$result=Select-QQSongTab $pointer $request.capture}
     'qq_play_song'{$result=Play-QQSongRow $pointer $request}
     'type'{[CompanionWindows]::Type($pointer,[string]$request.text);$result=@{ok=$true}}
     'click_text'{
      $ocr=Read-WindowText $pointer $request.capture $false ([double]$request.scale)
      $matches=@($ocr.lines|Where-Object {$actual=$_.text -replace '\s','';$expected=[string]$request.text -replace '\s','';if($request.exact){$actual -eq $expected}else{$actual.Contains($expected)}})
      # Windows OCR may merge a complete tab strip into one line. Exact word
      # boxes still identify the requested tab without clicking the strip centre.
      if($request.exact -and -not $matches.Count){
       $chunks=OCR-Chunks $ocr
       $matches=@($chunks|Where-Object {($_.text -replace '\s','') -eq ([string]$request.text -replace '\s','')})
      }
      if($request.region -eq 'results'){$matches=@($matches|Where-Object {$_.y -gt $ocr.height*.22 -and $_.y -lt $ocr.height*.85 -and $_.x -gt $ocr.width*.18})}
      if($request.region -eq 'suggestions'){$matches=@($matches|Where-Object {$_.y -gt $ocr.height*.08 -and $_.y -lt $ocr.height*.18})}
      if($request.region -eq 'header'){$matches=@($matches|Where-Object {$_.y -lt $ocr.height*.12 -and $_.x -gt $ocr.width*.20 -and $_.x -lt $ocr.width*.58})}
      if($request.region -eq 'tabs'){$matches=@($matches|Where-Object {$_.y -gt $ocr.height*.14 -and $_.y -lt $ocr.height*.25 -and $_.x -gt $ocr.width*.20})}
      if(-not $matches.Count){throw 'OCR target text not found'}
      $item=$matches[[Math]::Min([int]$request.index,$matches.Count-1)]
      [CompanionWindows]::Click($pointer,[int]($ocr.origin[0]+$item.x+$item.width/2),[int]($ocr.origin[1]+$item.y+$item.height/2),[bool]$request.double)
      $result=@{ok=$true;method='window_ocr';matched=$item.text}
     }
     'click_search_header'{
      # Observe and click in the same operation: a blinking caret or changing
      # placeholder must not invalidate a literal text from the prior capture.
      $ocr=Read-WindowText $pointer $request.capture $false 2
      $header=@(OCR-Chunks $ocr|Where-Object {$_.y -lt $ocr.height*.10 -and $_.x -gt $ocr.width*.22 -and $_.x+$_.width -lt $ocr.width*.51 -and $_.width -gt 0 -and $_.text -match '[\u4e00-\u9fff]|[a-zA-Z]{2}'})
      $item=$header|Where-Object {$_.text -match '搜索|搜歌|搜你'}|Select-Object -First 1
      if(-not $item){$item=$header|Sort-Object y,@{Expression='width';Descending=$true}|Select-Object -First 1}
      if(-not $item){throw 'Fresh search header not observed'}
      [CompanionWindows]::Click($pointer,[int]($ocr.origin[0]+$item.x+$item.width/2),[int]($ocr.origin[1]+$item.y+$item.height/2),$false)
      $result=@{ok=$true;method='fresh_search_header'}
     }
     'click_song_tab'{
      $ocr=Read-WindowText $pointer $request.capture $false 2
      $tabs=@(OCR-Chunks $ocr|Where-Object {$_.y -gt $ocr.height*.14 -and $_.y -lt $ocr.height*.25 -and $_.x -gt $ocr.width*.20})
      $item=$tabs|Where-Object {($_.text -replace '\s','') -eq '单曲'}|Select-Object -First 1
      if(-not $item){
       $playlist=$tabs|Where-Object {($_.text -replace '\s','') -eq '歌单'}|Select-Object -First 1
       if($playlist){$item=$tabs|Where-Object {$_.x+$_.width -lt $playlist.x -and $playlist.x-$_.x -lt $ocr.width*.08 -and [Math]::Abs($_.y-$playlist.y) -lt $playlist.height/2}|Sort-Object x -Descending|Select-Object -First 1}
      }
      if(-not $item){throw ('Single-song tab not observed in fresh search page: '+(($tabs|ForEach-Object {$_.text})|ConvertTo-Json -Compress))}
      [CompanionWindows]::Click($pointer,[int]($ocr.origin[0]+$item.x+$item.width/2),[int]($ocr.origin[1]+$item.y+$item.height/2),$false)
      $result=@{ok=$true;method='fresh_song_tab';matched=$item.text}
     }
     'submit_exact_search'{
      $ocr=Read-WindowText $pointer $request.capture $false 2
      $expected=([string]$request.text -replace '[\s《》“”"''·・]','').ToLowerInvariant()
      $item=@($ocr.lines)+@(OCR-Chunks $ocr)|Where-Object {
       $text=($_.text -replace '[\s《》“”"''·・]','').ToLowerInvariant()
       $_.y -gt $ocr.height*.08 -and $_.y -lt $ocr.height*.19 -and $_.x -gt $ocr.width*.20 -and $_.x+$_.width -lt $ocr.width*.65 -and $text -match '^[q0]' -and $text.Substring(1) -eq $expected
      }|Sort-Object y|Select-Object -First 1
      if($item){[CompanionWindows]::Click($pointer,[int]($ocr.origin[0]+$item.x+$item.width/2),[int]($ocr.origin[1]+$item.y+$item.height/2),$false);$result=@{ok=$true;method='exact_query_suggestion'}}
      else{[CompanionWindows]::Press($pointer,'ENTER');$result=@{ok=$true;method='enter'}}
     }
     'element'{
      $elements=@(Inspect-Tree $pointer)
      $matches=@($elements|Where-Object {$_.enabled -and -not $_.offscreen -and (-not $request.type -or $_.type -eq $request.type) -and (-not $request.name -or $_.name.Contains([string]$request.name)) -and (-not $request.automation_id -or $_.automation_id -eq $request.automation_id) -and (-not $request.class_name -or $_.class_name -eq $request.class_name)})
      if(-not $matches.Count){throw 'UIA target element not found'}
      $item=$matches[0];$node=$script:controls[$item.key]
      [CompanionWindows]::RequireFocus($pointer)
      if($request.operation -eq 'set_text'){
       $value=$null
       if($node.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$value)){
        if($value.Current.IsReadOnly){throw 'Read-only element'};$value.SetValue([string]$request.text)
       }else{$node.SetFocus();[CompanionWindows]::Press($pointer,'CTRL+A');[CompanionWindows]::Type($pointer,[string]$request.text)}
      }
      else{$invoke=$null;if(-not $request.double -and $node.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)){$invoke.Invoke()}else{[CompanionWindows]::Click($pointer,[int]($item.bounds[0]+$item.bounds[2]/2),[int]($item.bounds[1]+$item.bounds[3]/2),[bool]$request.double)}}
      $result=@{ok=$true;method='uia';matched=$item.name}
     }
     default{throw 'Unsupported desktop action'}
    }
   }
  }
  [Console]::WriteLine((@{id=$request.id;result=$result}|ConvertTo-Json -Depth 12 -Compress))
 }catch{[Console]::WriteLine((@{id=$request.id;error=$_.Exception.Message}|ConvertTo-Json -Compress))}
}
