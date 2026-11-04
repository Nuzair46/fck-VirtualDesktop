$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'StreamerHost.ps1')
$script:VdTestProcesses=@()
$script:VdTestLaunches=@()
$script:VdTestClearOnExit=$true
$script:VdTestsPassed=0
function Get-VdProcesses {param([string]$Executable);return $script:VdTestProcesses}
function Get-VdServices {param([string]$Directory);return @()}
function Start-Process {
    param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,[switch]$PassThru,$ErrorAction)
    $script:VdTestLaunches += [pscustomobject]@{File=$FilePath;Arguments=$ArgumentList;WindowStyle=$WindowStyle}
    $fake=[pscustomobject]@{Id=9001;HasExited=$false}
    $fake | Add-Member ScriptMethod WaitForExit {param($milliseconds);if($script:VdTestClearOnExit){$script:VdTestProcesses=@()};return $true}
    $fake | Add-Member ScriptMethod Refresh {}
    return $fake
}
function Stop-Process {param([int]$Id,$ErrorAction);if($Id -notin @($script:VdTestProcesses.Id)){throw 'Unexpected stop PID'};$script:VdTestProcesses=@()}
function Stop-Service {throw 'No services should be stopped in these fixtures'}
function Start-Service {throw 'No services should be started in these fixtures'}
function Assert-VdTest([bool]$Condition,[string]$Message){if(!$Condition){throw $Message}}
function Expect-VdFailure([scriptblock]$Body,[string]$Pattern){
    $failed=$false
    try{& $Body | Out-Null}catch{$failed=$true;if($_.Exception.Message -notmatch $Pattern){throw "Unexpected error: $($_.Exception.Message)"}}
    Assert-VdTest $failed ('Expected failure: '+$Pattern)
}
function Pass-VdTest {$script:VdTestsPassed++}
$testRoot=Join-Path ([IO.Path]::GetTempPath()) ('vd-host-test-'+[Guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path $testRoot
$originalExe='C:\Program Files\Virtual Desktop Streamer\VirtualDesktop.Streamer.exe'
$originalHash=Get-VdHash $originalExe
$liveSettings=Join-Path $env:ProgramData 'Virtual Desktop\StreamerSettings.json'
$liveSettingsHash=if(Test-Path -LiteralPath $liveSettings){Get-VdHash $liveSettings}else{$null}
try {
    $install=Join-Path $testRoot 'install';$null=New-Item -ItemType Directory -Path $install
    Copy-Item -LiteralPath $originalExe -Destination (Join-Path $install 'VirtualDesktop.Streamer.exe')
    $identity=Get-VdIdentity $install
    Assert-VdTest $identity.SupportedStock 'Exact original fixture was not identified'
    Pass-VdTest
    $settings=Join-Path $testRoot 'settings.json'
    $fixture='{"Accounts":{"OculusQuest":["existing-user"],"Pico":["other-user"]},"AllowRemoteConnections":true,"Unrelated":{"Nested":[1,{"Name":"keep"}]},"Gain":0.125}'
    [IO.File]::WriteAllText($settings,$fixture)
    $before=Get-VdHash $settings
    $state=Join-Path $testRoot 'state'
    $staged=Stage-VdSettings $identity $settings $state 'vdlocaltest'
    Assert-VdTest ((Get-VdHash $settings) -eq $before) 'Staging changed the source settings'
    $value=Get-VdJson $staged.StagedPath
    Assert-VdTest ($value.Accounts.OculusQuest.Count -eq 2 -and $value.Accounts.OculusQuest[0] -eq 'existing-user') 'Existing Quest accounts were not retained'
    Assert-VdTest ($value.Accounts.Pico[0] -eq 'other-user' -and $value.Unrelated.Nested[1].Name -eq 'keep' -and $value.Gain -eq 0.125) 'Unrelated settings changed'
    Assert-VdTest (!$value.AllowRemoteConnections) 'Remote connections remain enabled'
    Assert-VdTest (((ConvertTo-Json $staged -Depth 8) -notmatch 'existing-user|other-user')) 'Personal fixture account appeared in output metadata'
    Pass-VdTest
    $again=Stage-VdSettings $identity $settings $state 'vdlocaltest'
    Assert-VdTest ($again.Status -eq 'AlreadyStaged') 'Repeated stage not idempotent'
    Pass-VdTest
    $applied=Apply-VdSettings $identity $settings $state
    Assert-VdTest ((Get-VdHash $settings) -eq $staged.AfterSHA256 -and $applied.Status -eq 'Applied') 'Apply mismatch'
    $again=Apply-VdSettings $identity $settings $state
    Assert-VdTest ($again.Status -eq 'AlreadyApplied') 'Repeated apply not idempotent'
    Pass-VdTest
    $restored=Restore-VdSettings $identity $settings $state
    Assert-VdTest ((Get-VdHash $settings) -eq $before -and [IO.File]::ReadAllText($settings) -ceq $fixture) 'Restore was not byte exact'
    $again=Restore-VdSettings $identity $settings $state
    Assert-VdTest ($again.Status -eq 'AlreadyRestored') 'Repeated restore not idempotent'
    Pass-VdTest
    [IO.File]::WriteAllText($settings,$fixture+' ')
    $changed=Get-VdHash $settings
    Expect-VdFailure {Apply-VdSettings $identity $settings $state} 'changed after staging'
    Assert-VdTest ((Get-VdHash $settings) -eq $changed) 'Changed source overwritten'
    Pass-VdTest
    [IO.File]::WriteAllText($settings,$fixture)
    $null=Apply-VdSettings $identity $settings $state
    $edited=Get-VdJson $settings
    $edited | Add-Member -NotePropertyName NewUserPreference -NotePropertyValue 'preserve'
    $edited.Accounts.OculusQuest+=@('user-added-later')
    Write-VdJson $settings $edited
    $changed=Get-VdHash $settings
    $checked=Check-VdRestoreSettings $identity $settings $state
    Assert-VdTest ($checked.Mode -eq 'MergeCurrent' -and (Get-VdHash $settings) -eq $changed) 'Restore preflight mutated current settings'
    $invalid=Get-VdJson $settings;$invalid.Accounts='unsupported';Write-VdJson $settings $invalid
    $badHash=Get-VdHash $settings
    Expect-VdFailure {Check-VdRestoreSettings $identity $settings $state} 'schema is unsupported'
    Assert-VdTest ((Get-VdHash $settings) -eq $badHash) 'Conflicting schema changed during preflight'
    Write-VdJson $settings $edited
    Pass-VdTest
    $null=Restore-VdSettings $identity $settings $state
    $merged=Get-VdJson $settings
    Assert-VdTest ($merged.NewUserPreference -eq 'preserve' -and $merged.Accounts.OculusQuest -contains 'user-added-later' -and $merged.Accounts.OculusQuest -contains 'existing-user' -and $merged.Accounts.OculusQuest -notcontains 'vdlocaltest' -and $merged.AllowRemoteConnections) 'Surgical restore failed to preserve user modifications or undo our fields'
    Pass-VdTest
    $merged.AllowRemoteConnections=$false;Write-VdJson $settings $merged
    $postRestoreHash=Get-VdHash $settings
    $null=Restore-VdSettings $identity $settings $state
    Assert-VdTest ((Get-VdHash $settings) -eq $postRestoreHash) 'Repeat restore overwrote settings changed after completed restoration'
    Pass-VdTest
    $missing=Join-Path $testRoot 'new-settings.json'
    $missingState=Join-Path $testRoot 'missing-state'
    $null=Stage-VdSettings $identity $missing $missingState 'vdlocaltest'
    Assert-VdTest (!(Test-Path -LiteralPath $missing)) 'Staging created missing live settings'
    $null=Apply-VdSettings $identity $missing $missingState
    Assert-VdTest ((Get-VdJson $missing).Accounts.OculusQuest[0] -eq 'vdlocaltest') 'Minimal settings malformed'
    $null=Restore-VdSettings $identity $missing $missingState
    Assert-VdTest (!(Test-Path -LiteralPath $missing)) 'Restoring initially absent settings did not remove generated file'
    Pass-VdTest
    $newFile=Join-Path $testRoot 'new-user-settings.json';$newState=Join-Path $testRoot 'new-user-state'
    $null=Stage-VdSettings $identity $newFile $newState 'vdlocaltest'
    $null=Apply-VdSettings $identity $newFile $newState
    $newValue=Get-VdJson $newFile;$newValue | Add-Member -NotePropertyName UserCreatedValue -NotePropertyValue 7;Write-VdJson $newFile $newValue
    $null=Restore-VdSettings $identity $newFile $newState
    $newValue=Get-VdJson $newFile
    Assert-VdTest ($newValue.UserCreatedValue -eq 7 -and !$newValue.PSObject.Properties['Accounts'] -and !$newValue.PSObject.Properties['AllowRemoteConnections']) 'Restore deleted or retained the wrong fields in a user-modified new settings file'
    Pass-VdTest
    $preexisting=Join-Path $testRoot 'preexisting-pair.json';$preexistingState=Join-Path $testRoot 'preexisting-state'
    [IO.File]::WriteAllText($preexisting,'{"Accounts":{"OculusQuest":["vdlocaltest"]},"AllowRemoteConnections":false}')
    $null=Stage-VdSettings $identity $preexisting $preexistingState 'vdlocaltest'
    $null=Apply-VdSettings $identity $preexisting $preexistingState
    $preValue=Get-VdJson $preexisting;$preValue.AllowRemoteConnections=$true;Write-VdJson $preexisting $preValue
    $null=Restore-VdSettings $identity $preexisting $preexistingState
    $preValue=Get-VdJson $preexisting
    Assert-VdTest ($preValue.AllowRemoteConnections -and $preValue.Accounts.OculusQuest -contains 'vdlocaltest') 'Restore overwrote a user-enabled remote setting or removed a preexisting account'
    Pass-VdTest
    $neverApplied=Join-Path $testRoot 'never-applied.json';$neverState=Join-Path $testRoot 'never-state'
    [IO.File]::WriteAllText($neverApplied,'{"AllowRemoteConnections":true}')
    $null=Stage-VdSettings $identity $neverApplied $neverState 'vdlocaltest'
    [IO.File]::WriteAllText($neverApplied,'{"AllowRemoteConnections":false,"UserAdded":true}')
    $neverHash=Get-VdHash $neverApplied
    $null=Restore-VdSettings $identity $neverApplied $neverState
    Assert-VdTest ((Get-VdHash $neverApplied) -eq $neverHash) 'Restore modified settings from a stage which was never applied'
    Pass-VdTest
    $limit=Join-Path $testRoot 'limit.json';[IO.File]::WriteAllText($limit,'{"Accounts":{"OculusQuest":["one","two","three","four","five"]}}')
    $limitHash=Get-VdHash $limit
    Expect-VdFailure {Stage-VdSettings $identity $limit (Join-Path $testRoot 'limit-state') 'vdlocaltest'} 'five Quest accounts'
    Assert-VdTest ((Get-VdHash $limit) -eq $limitHash) 'Account limit failure changed settings'
    Pass-VdTest
    $duplicate=Join-Path $testRoot 'duplicate.json';[IO.File]::WriteAllText($duplicate,'{"Accounts":{"OculusQuest":["VDLOCALTEST"]},"AllowRemoteConnections":true}')
    $d=Stage-VdSettings $identity $duplicate (Join-Path $testRoot 'duplicate-state') 'vdlocaltest'
    Assert-VdTest ((Get-VdJson $d.StagedPath).Accounts.OculusQuest.Count -eq 1) 'Case-insensitive duplicate was added'
    Pass-VdTest
    $script:VdTestProcesses=@([pscustomobject]@{Id=42;Executable=$null;VerifiedPath=$false})
    Expect-VdFailure {Apply-VdSettings $identity $missing $missingState} 'running|could not be verified'
    Expect-VdFailure {Stop-VdStreamer $identity (Join-Path $testRoot 'unknown-process') $false 1} 'Cannot verify'
    Assert-VdTest ($script:VdTestLaunches.Count -eq 0) 'Unknown process caused a launch'
    Pass-VdTest
    $script:VdTestProcesses=@()
    $processState=Join-Path $testRoot 'process-state'
    $stopped=Stop-VdStreamer $identity $processState $false 1
    Assert-VdTest (!$stopped.WasRunning) 'Stopped-state receipt wrong'
    $started=Start-VdStreamer $identity $processState $false
    Assert-VdTest ($started.Status -eq 'LeftStopped' -and $script:VdTestLaunches.Count -eq 0) 'Initially stopped Streamer was launched'
    Pass-VdTest
    $script:VdTestProcesses=@([pscustomobject]@{Id=42;Executable=$identity.Executable;VerifiedPath=$true})
    $stopped=Stop-VdStreamer $identity (Join-Path $testRoot 'graceful-state') $false 1
    Assert-VdTest ($stopped.WasRunning -and !$stopped.Forced -and $script:VdTestLaunches[0].Arguments -eq '/Exit' -and $script:VdTestLaunches[0].WindowStyle -eq 'Hidden') 'Graceful exit command malformed'
    $started=Start-VdStreamer $identity (Join-Path $testRoot 'graceful-state') $false
    Assert-VdTest ($started.Status -eq 'Started' -and $script:VdTestLaunches[1].WindowStyle -eq 'Hidden') 'Start did not restore prior running state'
    Pass-VdTest
    $stoppedLater=Stop-VdStreamer $identity (Join-Path $testRoot 'graceful-state') $false 1
    Assert-VdTest (!$stoppedLater.WasRunning) 'A completed old process receipt incorrectly reused historical running state'
    Pass-VdTest
    $script:VdTestProcesses=@([pscustomobject]@{Id=42;Executable=$identity.Executable;VerifiedPath=$true})
    $script:VdTestClearOnExit=$false
    $stopped=Stop-VdStreamer $identity (Join-Path $testRoot 'forced-state') $true 1
    Assert-VdTest ($stopped.Forced -and $script:VdTestProcesses.Count -eq 0) 'Explicit forced fallback failed'
    Pass-VdTest
    $updater=Join-Path $identity.InstallDirectory 'Updater.exe'
    $updaterBackup=$updater+'.bak'
    $updaterState=Join-Path $testRoot 'updater-state'
    $neverUpdaterState=Join-Path $testRoot 'never-updater-state'
    Assert-VdTest ((Restore-VdUpdater $identity $neverUpdaterState).Status -eq 'NoChangesApplied') 'Missing updater journal was not harmless'
    Assert-VdTest ((Get-VdUpdaterPlan $identity $updaterState $false).Status -eq 'UpdaterMissing' -and !(Test-Path -LiteralPath $updaterState)) 'Missing updater preflight wrote state'
    Pass-VdTest
    [IO.File]::WriteAllText($updater,'inert updater fixture')
    $updaterHash=Get-VdHash $updater
    $plan=Get-VdUpdaterPlan $identity $updaterState $false
    Assert-VdTest ($plan.Status -eq 'ReadyToDisable' -and !(Test-Path -LiteralPath $updaterState) -and (Get-VdHash $updater) -eq $updaterHash) 'Disable preflight changed updater or state'
    $disabled=Disable-VdUpdater $identity $updaterState
    Assert-VdTest ($disabled.Status -eq 'Disabled' -and !(Test-Path -LiteralPath $updater) -and (Get-VdHash $updaterBackup) -eq $updaterHash) 'Updater rename failed'
    Assert-VdTest ((Disable-VdUpdater $identity $updaterState).Status -eq 'AlreadyDisabled') 'Repeated updater disable was not idempotent'
    Pass-VdTest
    Assert-VdTest ((Restore-VdUpdater $identity $neverUpdaterState).Status -eq 'NoChangesApplied' -and (Test-Path -LiteralPath $updaterBackup)) 'Missing updater journal claimed an unowned .bak'
    Expect-VdFailure {Get-VdUpdaterPlan $identity $neverUpdaterState $false} 'already exists'
    Pass-VdTest
    [IO.File]::WriteAllText($updater,'new updater from third party')
    Expect-VdFailure {Get-VdUpdaterPlan $identity $updaterState $true} 'Both Updater'
    Expect-VdFailure {Restore-VdUpdater $identity $updaterState} 'Both Updater'
    Assert-VdTest ([IO.File]::ReadAllText($updater) -eq 'new updater from third party') 'Updater conflict overwrote user file'
    Remove-Item -LiteralPath $updater
    [IO.File]::WriteAllText($updaterBackup,'modified backup')
    Expect-VdFailure {Get-VdUpdaterPlan $identity $updaterState $true} 'changed'
    Expect-VdFailure {Restore-VdUpdater $identity $updaterState} 'changed'
    [IO.File]::WriteAllText($updaterBackup,'inert updater fixture')
    Pass-VdTest
    # Simulate interruption after rename but before Disabled was journaled.
    $updaterReceipt=Get-VdJson $disabled.ReceiptPath;$updaterReceipt.Status='Prepared';Write-VdJson $disabled.ReceiptPath $updaterReceipt
    Assert-VdTest ((Get-VdUpdaterPlan $identity $updaterState $true).Status -eq 'ReadyToRestore') 'Prepared journal did not recover completed rename'
    Assert-VdTest ((Restore-VdUpdater $identity $updaterState).Status -eq 'Restored' -and (Get-VdHash $updater) -eq $updaterHash -and !(Test-Path -LiteralPath $updaterBackup)) 'Updater restoration failed'
    Assert-VdTest ((Restore-VdUpdater $identity $updaterState).Status -eq 'AlreadyRestored') 'Repeated updater restore was not idempotent'
    Pass-VdTest
    # Simulate interruption after Prepared but before any rename.
    $updaterReceipt=Get-VdJson $disabled.ReceiptPath;$updaterReceipt.Status='Prepared';Write-VdJson $disabled.ReceiptPath $updaterReceipt
    Assert-VdTest ((Restore-VdUpdater $identity $updaterState).Status -eq 'AlreadyRestored') 'Prepared journal with untouched original was not harmless'
    Pass-VdTest
    Assert-VdTest ((Get-VdHash $originalExe) -eq $originalHash) 'Real executable changed'
    if($liveSettingsHash){Assert-VdTest ((Get-VdHash $liveSettings) -eq $liveSettingsHash) 'Real settings changed'}
    [pscustomobject]@{Passed=$script:VdTestsPassed;RealExecutableUnchanged=$true;RealSettingsUnchanged=$true;ProcessAndServiceActions='mocked';LiveStopOrLaunch=$false} | ConvertTo-Json
} finally {
    # This fresh GUID fixture is directly inside TEMP; verify that boundary first.
    $resolved=Get-VdFullPath $testRoot
    $tempRoot=Get-VdFullPath ([IO.Path]::GetTempPath())
    if([IO.Path]::GetDirectoryName($resolved) -ne $tempRoot -or [IO.Path]::GetFileName($resolved) -notlike 'vd-host-test-*'){throw 'Refusing unexpected test cleanup path'}
    Remove-Item -LiteralPath $resolved -Recurse
}
