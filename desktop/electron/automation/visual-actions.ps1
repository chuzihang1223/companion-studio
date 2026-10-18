# Invoked only by the existing authorized windows-worker switch. A frame is
# single-use for input; stale COM references are always replaced by a fresh tree.
function Assert-VisualObservationFresh($Frame){
 $age=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()-[long]$Frame.created_ms
 if($age -ge 8000 -or $age -lt -1000){throw 'COMPUTER_OBSERVATION_EXPIRED'}
}
function Test-VisualVerification($Frame,$Condition,$Before){
 # Missing elements cannot be proven by a partial traversal.
 if($Frame.tree_truncated){return $false}
 if($Condition.kind -eq 'element_absent' -and ($Frame.filtered_subtrees -or $Frame.masked_identity_hashes -contains (Get-VisualIdentityHash ([string]$Condition.automation_id)))){return $false}
 if(-not $Condition){return $false}
 if($Condition.kind -eq 'window_focused'){return [bool]$Frame.window.foreground}
 $matches=@($Frame.elements|Where-Object {$_.automation_id -eq $Condition.automation_id -and $_.visible})
 if($Condition.kind -eq 'element_absent'){return $matches.Count -eq 0}
 if($matches.Count -ne 1){return $false}
 $item=$matches[0]
 switch($Condition.kind){
  'element_present'{return $true}
  'text_equals'{return ($item.value -ceq $Condition.expected -or $item.name -ceq $Condition.expected)}
  'text_contains'{return ($item.value.Contains([string]$Condition.expected) -or $item.name.Contains([string]$Condition.expected))}
  'scroll_changed'{return $null -ne $item.scroll -and $null -ne $Before -and ($item.scroll -join ':') -ne ($Before -join ':')}
 }
 return $false
}
function Invoke-VisualAction($Request,[long]$Handle){
 $operation=[string]$Request.visual_operation
 if($operation -eq 'observe'){return New-VisualObservation $Request $Handle}
 if($operation -eq 'focus_window'){
  $null=Assert-VisualIdentity $Request $Handle
  if(-not [CompanionWindows]::Focus([IntPtr]$Handle)){throw 'FOCUS_LOST'}
  $frame=New-VisualObservation $Request $Handle
  return @{status=$(if($frame.window.foreground){'SUCCESS'}else{'FAILED'});verified=[bool]$frame.window.foreground;observation=$frame;method='window_identity'}
 }
 if($operation -eq 'wait_for_state'){
  $end=[DateTime]::UtcNow.AddSeconds([Math]::Min(12,[Math]::Max(.2,[double]$Request.timeout_seconds)))
  do{$frame=New-VisualObservation $Request $Handle;if(Test-VisualVerification $frame $Request.verification $null){return @{status='SUCCESS';verified=$true;observation=$frame;method='post_observation'}};Start-Sleep -Milliseconds 150}while([DateTime]::UtcNow -lt $end)
  return @{status='FAILED';verified=$false;code='POST_ACTION_VERIFICATION_FAILED';observation=$frame}
 }
 if($operation -notin @('click','type_text','scroll')){throw 'COMPUTER_OPERATION_UNSUPPORTED'}
 $previous=$script:visualFrames[[string]$Request.observation_id]
 if(-not $previous -or $previous.nonce -cne $Request.visual_nonce -or [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()-$previous.created_ms -gt 8000){throw 'COMPUTER_OBSERVATION_EXPIRED'}
 $script:visualFrames.Remove([string]$Request.observation_id)
 $current=Assert-VisualIdentity $Request $Handle
 if(-not $current.foreground){
  # Approving in Studio necessarily foregrounds our own UI. Restore only from
  # that exact main PID, never steal focus from another application. This stays
  # in the original single-use native action and works for Workflow approvals.
  if(-not [VisualWindow]::ForegroundOwnedBy([int]$Request.expected_ui_pid) -or -not [CompanionWindows]::Focus([IntPtr]$Handle)){throw 'FOCUS_LOST'}
 }
 [CompanionWindows]::RequireFocus([IntPtr]$Handle)
 $fresh=New-VisualObservation $Request $Handle
 if($fresh.tree_truncated){throw 'COMPUTER_OBSERVATION_INCOMPLETE'}
 if($fresh.geometry.layout_signature -cne $previous.geometry.layout_signature){throw 'DISPLAY_CHANGED'}
 if(($fresh.window.bounds -join ':') -cne ($previous.window.bounds -join ':') -or $fresh.geometry.window_dpi -ne $previous.geometry.window_dpi -or $fresh.geometry.monitor_dpi -ne $previous.geometry.monitor_dpi){throw 'DPI_MAPPING_INVALID'}
 $candidate=$Request.selected_candidate
 $row=$null;$node=$null;$beforeScroll=$null
 if($candidate.source -eq 'uia'){
  $matches=@($fresh.elements|Where-Object {$_.identity -ceq $candidate.identity -and $_.automation_id -ceq $candidate.automation_id -and $_.name -ceq $candidate.name -and $_.type -ceq $candidate.type -and $_.visible -and $_.enabled})
  if($matches.Count -ne 1){throw 'UIA_ELEMENT_STALE'}
  $row=$matches[0];$node=$script:visualControls[$row.identity];$beforeScroll=$row.scroll
 }elseif($candidate.source -eq 'ocr' -and $operation -eq 'click' -and $Request.coordinate_fallback){
  if(-not $fresh.ocr -or -not $previous.ocr){throw 'ELEMENT_NOT_FOUND'}
  $matches=@($fresh.ocr.lines|Where-Object {$_.text -ceq $candidate.name})
  if($matches.Count -ne 1 -or ($matches[0].bounds -join ':') -cne ($candidate.bounds -join ':')){throw 'ELEMENT_AMBIGUOUS'}
  $row=@{bounds=$matches[0].bounds;name=$candidate.name}
 }else{throw 'COMPUTER_CANDIDATE_INVALID'}
 $box=$row.bounds;$x=$box[0]+$box[2]/2;$y=$box[1]+$box[3]/2
 if($box[2] -le 0 -or $box[3] -le 0 -or -not [VisualWindow]::Hit($Handle,$x,$y)){throw 'ELEMENT_OCCLUDED'}
 if($row.name -match '(?i)delete|remove account|purchase|pay now|send message|submit order|删除|付款|支付|发送消息|购买|提权'){throw 'COMPUTER_HIGH_RISK_UNSUPPORTED'}
 $dispatched=$false;$method='uia';$timer=[Diagnostics.Stopwatch]::StartNew()
 try{
  # All argument/identity checks precede dispatch. From this point an exception
  # means UNKNOWN, never permission to repeat a possible external side effect.
  $current=Assert-VisualIdentity $Request $Handle
  if(-not $current.foreground){throw 'FOCUS_LOST'}
  [CompanionWindows]::RequireFocus([IntPtr]$Handle)
  Assert-VisualObservationFresh $fresh
  if($operation -eq 'type_text'){
   if($row.type -ne 'Edit' -or ([string]$Request.text).Length -gt 500 -or (Test-VisualSensitive ([string]$Request.text))){throw 'COMPUTER_INPUT_REJECTED'}
   $pattern=$null
   if(-not $node.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$pattern) -or $pattern.Current.IsReadOnly){throw 'COMPUTER_PATTERN_UNSUPPORTED'}
   # ValuePattern targets the observed textbox without stealing keyboard focus.
   [CompanionWindows]::RequireFocus([IntPtr]$Handle);Assert-VisualObservationFresh $fresh
   $dispatched=$true;$pattern.SetValue([string]$Request.text)
   $Request.verification=@{kind='text_equals';automation_id=$row.automation_id;expected=[string]$Request.text}
  }elseif($operation -eq 'click'){
   $pattern=$null
   if($node -and $node.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)){[CompanionWindows]::RequireFocus([IntPtr]$Handle);Assert-VisualObservationFresh $fresh;$dispatched=$true;$pattern.Invoke()}
   elseif($candidate.source -eq 'ocr' -and $Request.coordinate_fallback){Assert-VisualObservationFresh $fresh;$method='fresh_ocr_hit_test';$dispatched=$true;[CompanionWindows]::Click([IntPtr]$Handle,[int]$x,[int]$y,$false)}
   else{throw 'COMPUTER_PATTERN_UNSUPPORTED'}
  }else{
   $pattern=$null
   if(-not $node.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern,[ref]$pattern)){throw 'COMPUTER_PATTERN_UNSUPPORTED'}
   $none=[System.Windows.Automation.ScrollAmount]::NoAmount
   $up=[System.Windows.Automation.ScrollAmount]::SmallDecrement;$down=[System.Windows.Automation.ScrollAmount]::SmallIncrement
   $horizontal=$none;$vertical=$none
   switch($Request.direction){'up'{$vertical=$up};'down'{$vertical=$down};'left'{$horizontal=$up};'right'{$horizontal=$down};default{throw 'COMPUTER_ARGUMENT_INVALID'}}
   $Request.verification=@{kind='scroll_changed';automation_id=$row.automation_id}
   [CompanionWindows]::RequireFocus([IntPtr]$Handle);Assert-VisualObservationFresh $fresh;$dispatched=$true;$pattern.Scroll($horizontal,$vertical)
  }
  $deadline=[DateTime]::UtcNow.AddSeconds([Math]::Min(5,[Math]::Max(.2,[double]$Request.timeout_seconds)))
  do{
   Start-Sleep -Milliseconds 100;$after=New-VisualObservation $Request $Handle
   if(Test-VisualVerification $after $Request.verification $beforeScroll){return @{status='SUCCESS';verified=$true;executed=$true;method=$method;observation=$after;duration_ms=$timer.Elapsed.TotalMilliseconds}}
  }while([DateTime]::UtcNow -lt $deadline)
  return @{status='NEEDS_REVIEW';code='POST_ACTION_VERIFICATION_FAILED';verified=$false;executed=$true;observation=$after;duration_ms=$timer.Elapsed.TotalMilliseconds}
 }catch{
  if($dispatched){return @{status='UNKNOWN';code='ACTION_STATE_UNKNOWN';verified=$false;executed=$true;duration_ms=$timer.Elapsed.TotalMilliseconds}}
  throw
 }
}
