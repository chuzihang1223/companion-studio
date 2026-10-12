function Normalize-MusicText([string]$Text){return ($Text.Normalize([Text.NormalizationForm]::FormKC) -replace '[\s《》“”"''·・]','').ToLowerInvariant()}
function Test-MusicWordBoundary($Word,[double]$Gap){
 return (-not $Word -or (Normalize-MusicText $Word.text) -notmatch '[\p{L}\p{N}]' -or ($Word.width -gt $Word.height*2.2 -and (Normalize-MusicText $Word.text).Length -eq 1) -or $Gap -gt $Word.height*.8)
}
function Observed-QQTitles($Ocr,[string]$Title){
 $expected=Normalize-MusicText $Title
 foreach($line in $Ocr.lines){$words=@($line.words)
  for($start=0;$start -lt $words.Count;$start++){$text=''
   for($end=$start;$end -lt $words.Count;$end++){
    $text+=Normalize-MusicText $words[$end].text
    if($text.Length -gt $expected.Length){break};if($text -ne $expected){continue}
    $first=$words[$start];$last=$words[$end];$prior=if($start -gt 0){$words[$start-1]}else{$null};$next=if($end+1 -lt $words.Count){$words[$end+1]}else{$null}
    if((Test-MusicWordBoundary $prior ($first.x-$prior.x-$prior.width)) -and (Test-MusicWordBoundary $next ($next.x-$last.x-$last.width))){
     $group=@($words[$start..$end]);$top=($group|ForEach-Object {$_.y}|Measure-Object -Minimum).Minimum;$bottom=($group|ForEach-Object {$_.y+$_.height}|Measure-Object -Maximum).Maximum
     @{text=$Title;x=$first.x;y=$top;width=($last.x+$last.width-$first.x);height=($bottom-$top)}
    }
   }
  }
 }
}
function Tree-Descendants($Rows,[string]$Key){
 $keys=New-Object 'Collections.Generic.HashSet[string]';$keys.Add($Key)|Out-Null
 $changed=$true
 while($changed){$changed=$false;foreach($row in $Rows){if(-not $keys.Contains([string]$row.key) -and $keys.Contains([string]$row.parent_key)){$keys.Add([string]$row.key)|Out-Null;$changed=$true}}}
 return @($Rows|Where-Object {$keys.Contains([string]$_.key)})
}
function Find-QQSongRow($Rows,[string]$Title,[string]$Artist,[string]$Album){
 $expected=Normalize-MusicText $Title
 $matches=@($Rows|Where-Object {$_.enabled -and -not $_.offscreen -and $_.type -in @('Hyperlink','Text','DataItem') -and (Normalize-MusicText $_.name) -eq $expected})
 foreach($titleElement in $matches){
  $parent=$Rows|Where-Object {$_.key -eq $titleElement.parent_key}|Select-Object -First 1
  for($depth=0;$parent -and $depth -lt 10;$depth++){
   $children=Tree-Descendants $Rows $parent.key
   $singers=@($children|Where-Object {$_.name -match '^歌手[:：]'});$albums=@($children|Where-Object {$_.name -match '^专辑[:：]'});$clocks=@($children|Where-Object {$_.name -match '^时长[:：]\s*\d+:\d+'})
   if($singers.Count -eq 1 -and $albums.Count -eq 1 -and $clocks.Count -eq 1){
    $singer=$singers[0].name -replace '^歌手[:：]\s*','';$albumName=$albums[0].name -replace '^专辑[:：]\s*',''
    if((-not $Artist -or (Normalize-MusicText $singer).Contains((Normalize-MusicText $Artist))) -and (-not $Album -or (Normalize-MusicText $albumName) -eq (Normalize-MusicText $Album))){return @{title=$titleElement;container=$parent;children=$children;artist=$singer;album=$albumName;kind='song'}}
   }
   $parent=$Rows|Where-Object {$_.key -eq $parent.parent_key}|Select-Object -First 1
  }
 }
 return $null
}
function Invoke-ObservedControl([IntPtr]$Handle,$Element){
 [CompanionWindows]::RequireFocus($Handle)
 $node=$script:controls[[string]$Element.key];$pattern=$null
 if($node -and $node.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)){$pattern.Invoke()}
 else{[CompanionWindows]::Click($Handle,[int]($Element.bounds[0]+$Element.bounds[2]/2),[int]($Element.bounds[1]+$Element.bounds[3]/2),$false)}
}
function Select-QQSongTab([IntPtr]$Handle,[string]$Capture){
 $rows=Inspect-Tree $Handle
 $tab=$rows|Where-Object {$_.enabled -and -not $_.offscreen -and $_.type -in @('Button','TabItem') -and ($_.name -replace '^已选中\s*','') -eq '歌曲'}|Select-Object -First 1
 if($tab){Invoke-ObservedControl $Handle $tab;return @{ok=$true;method='fresh_uia_song_tab';category='song'}}
 $ocr=Read-WindowText $Handle $Capture $false 2;$chunks=OCR-Chunks $ocr
 $tabs=@($chunks|Where-Object {$_.y -gt $ocr.height*.12 -and $_.y -lt $ocr.height*.36 -and $_.x -gt $ocr.width*.2 -and (Normalize-MusicText $_.text) -in @('歌曲','视频','专辑','歌单','歌词','歌手','用户')})
 $tableHeaders=@($chunks|Where-Object {(Normalize-MusicText $_.text) -match '^歌名[/／]歌手$|^时长$'})
 # A delayed search suggestion may obscure all but the leftmost song tab.
 # The observed song-table headers plus that exact tab also identify this page.
 if($tabs.Count -lt 4 -and $tableHeaders.Count -lt 2){throw 'QQ search categories were not observed'}
 $tab=$tabs|Where-Object {(Normalize-MusicText $_.text) -eq '歌曲'}|Select-Object -First 1
 if(-not $tab){throw 'QQ song tab was not observed; album fallback is prohibited'}
 [CompanionWindows]::Click($Handle,[int]($ocr.origin[0]+$tab.x+$tab.width/2),[int]($ocr.origin[1]+$tab.y+$tab.height/2),$false)
 return @{ok=$true;method='fresh_visual_song_tab';category='song'}
}
function Play-QQSongRow([IntPtr]$Handle,$Request){
 $rows=Inspect-Tree $Handle
 $songHeader=$rows|Where-Object {(Normalize-MusicText $_.name) -match '歌名[/／]歌手'}|Select-Object -First 1
 $albumDetails=$rows|Where-Object {$_.name -match '专辑介绍|专辑简介|专辑信息'}|Select-Object -First 1
 $row=Find-QQSongRow $rows $Request.title $Request.artist $Request.album
 if($row -and ($songHeader -or ($Request.allow_album -and $albumDetails))){
  $button=$row.children|Where-Object {$_.enabled -and -not $_.offscreen -and $_.type -eq 'Button' -and $_.name -eq '播放'}|Select-Object -First 1
  if(-not $button){
   $cover=$row.children|Where-Object {$_.enabled -and -not $_.offscreen -and $_.type -eq 'Image' -and $_.bounds[2] -gt 12 -and $_.bounds[3] -gt 12 -and $_.bounds[0] -lt $row.title.bounds[0]}|Select-Object -First 1
   if($cover){[CompanionWindows]::Hover($Handle,[int]($cover.bounds[0]+$cover.bounds[2]/2),[int]($cover.bounds[1]+$cover.bounds[3]/2));Start-Sleep -Milliseconds 150
    $rows=Inspect-Tree $Handle;$row=Find-QQSongRow $rows $Request.title $Request.artist $Request.album
    if(-not $row){throw 'QQ song row changed after hover'}
    $button=$row.children|Where-Object {$_.enabled -and -not $_.offscreen -and $_.type -eq 'Button' -and $_.name -eq '播放'}|Select-Object -First 1
   }
  }
  if($button){Invoke-ObservedControl $Handle $button;return @{ok=$true;kind='song';method='fresh_uia_song_play_button';title=$row.title.name;artist=$row.artist;album=$row.album}}
  # Validated row, first-column title only; the album link is a separate sibling.
  [CompanionWindows]::Click($Handle,[int]($row.title.bounds[0]+$row.title.bounds[2]/2),[int]($row.title.bounds[1]+$row.title.bounds[3]/2),$true)
  return @{ok=$true;kind='song';method='fresh_uia_song_row';title=$row.title.name;artist=$row.artist;album=$row.album}
 }
 # Self-drawn/limited-UIA fallback still requires the observed song table,
 # correct column, exact title, corresponding artist line and row duration.
 $ocr=Read-WindowText $Handle $Request.capture $false 2;$chunks=OCR-Chunks $ocr
 $header=$chunks|Where-Object {(Normalize-MusicText $_.text) -match '歌名[/／]歌手'}|Select-Object -First 1
 if(-not $header){throw 'QQ song table was not observed; a matching album name is not a song'}
 $album=$chunks|Where-Object {(Normalize-MusicText $_.text) -eq '专辑' -and [Math]::Abs($_.y-$header.y) -lt $header.height*2 -and $_.x -gt $header.x}|Sort-Object x|Select-Object -First 1
 $duration=$chunks|Where-Object {(Normalize-MusicText $_.text) -eq '时长' -and [Math]::Abs($_.y-$header.y) -lt $header.height*2}|Select-Object -First 1
 if(-not $album -or -not $duration){throw 'QQ song table columns could not be classified'}
 $expected=Normalize-MusicText $Request.title
 $titles=@((@($chunks)+@(Observed-QQTitles $ocr $Request.title))|Where-Object {(Normalize-MusicText $_.text) -eq $expected -and $_.y -gt $header.y+$header.height -and $_.y -lt $ocr.height*.84 -and $_.x -ge $header.x-$header.height -and $_.x+$_.width -lt $album.x})
 foreach($title in $titles){
  $clock=$chunks|Where-Object {(Normalize-MusicText $_.text) -match '\d{1,2}:\d{2}' -and [Math]::Abs($_.y-$title.y) -lt $title.height*1.5 -and $_.x -ge $duration.x-$duration.height}|Select-Object -First 1
  $artist=($chunks|Where-Object {$_.y -ge $title.y+$title.height -and $_.y -lt $title.y+$title.height*2.8 -and $_.x -ge $title.x-$title.height -and $_.x -lt $album.x -and $_.text -match '[\p{L}]'}|ForEach-Object {$_.text}|Select-Object -Unique) -join ' '
  if($clock -and (-not $Request.artist -or (Normalize-MusicText $artist).Contains((Normalize-MusicText $Request.artist)))){
   [CompanionWindows]::Click($Handle,[int]($ocr.origin[0]+$title.x+$title.width/2),[int]($ocr.origin[1]+$title.y+$title.height/2),$true)
   return @{ok=$true;kind='song';method='fresh_visual_song_table_row';title=$title.text;artist=$artist}
  }
 }
 throw 'QQ exact playable song row was not found in the current table'
}
