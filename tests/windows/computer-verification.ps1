# Pure production verifier, synthetic frames only. No capture or input.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '../../desktop/electron/automation/visual-actions.ps1')
# Load only the production hash function, avoiding all capture/COM initialization.
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot '../../desktop/electron/automation/visual-observation.ps1'),[ref]$tokens,[ref]$errors)
$hashFunction=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-VisualIdentityHash'},$true)
Invoke-Expression $hashFunction.Extent.Text
$condition=@{kind='element_absent';automation_id='StillExists'}
if(Test-VisualVerification @{elements=@();tree_truncated=$true} $condition $null){throw 'Partial tree cannot prove absence'}
if(-not (Test-VisualVerification @{elements=@();tree_truncated=$false} $condition $null)){throw 'Complete tree should prove absence'}
if(Test-VisualVerification @{elements=@(@{automation_id='StillExists';visible=$true});tree_truncated=$false} $condition $null){throw 'Existing element cannot be absent'}
if(Test-VisualVerification @{elements=@();masked_identity_hashes=@((Get-VisualIdentityHash 'StillExists'));tree_truncated=$false} $condition $null){throw 'Redacted element is not absent'}
if(Test-VisualVerification @{elements=@();masked_identity_hashes=@((Get-VisualIdentityHash 'stillexists'));tree_truncated=$false} $condition $null){throw 'Redacted identity normalization must match verifier'}
if(Test-VisualVerification @{elements=@();filtered_subtrees=$true;tree_truncated=$false} $condition $null){throw 'Hidden subtree cannot prove absence'}
'Partial-tree verification fail-closed PASS'
$rejected=$false
try{Assert-VisualObservationFresh @{created_ms=([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()-8100)}}catch{$rejected=$_.Exception.Message -eq 'COMPUTER_OBSERVATION_EXPIRED'}
if(-not $rejected){throw 'Expired capture must not dispatch'}
