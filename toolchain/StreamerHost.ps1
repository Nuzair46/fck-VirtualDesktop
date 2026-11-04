[CmdletBinding()]
param(
    [ValidateSet('Detect','StageSettings','ApplySettings','CheckRestoreSettings','RestoreSettings','Stop','Start','CheckDisableUpdater','DisableUpdater','CheckRestoreUpdater','RestoreUpdater')][string]$Action='Detect',
    [string]$InstallDirectory,
    [string]$SettingsPath=(Join-Path $env:ProgramData 'Virtual Desktop\StreamerSettings.json'),
    [string]$StateDirectory,
    [string]$Account,
    [switch]$ForceStop,
    [switch]$StartIfStopped,
    [ValidateRange(1,30)][int]$WaitSeconds=12
)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding -ArgumentList $false
$OutputEncoding=[Console]::OutputEncoding
$script:VdStockHash='0229a322137f55687240d2b51f5f7002056d9277d7fa001ec6cb4eb25af0bfb8'
$script:VdPatternCache=@{}
$script:VdHostRoot=$PSScriptRoot

function Get-VdFullPath([string]$Path) { [IO.Path]::GetFullPath($Path).TrimEnd('\') }
function Get-VdHash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Get-VdBytesHash([byte[]]$Bytes) {
    $h=[Security.Cryptography.SHA256]::Create()
    try { [BitConverter]::ToString($h.ComputeHash($Bytes)).Replace('-','').ToLowerInvariant() } finally { $h.Dispose() }
}
function Write-VdNewBytes([string]$Path,[byte[]]$Bytes) {
    $f=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $f.Write($Bytes,0,$Bytes.Length); $f.Flush($true) } finally { $f.Dispose() }
}
function Write-VdJson([string]$Path,$Value,[switch]$New) {
    $text=ConvertTo-Json -InputObject $Value -Depth 100
    $bytes=(New-Object Text.UTF8Encoding -ArgumentList $false).GetBytes($text+"`n")
    if($New){ Write-VdNewBytes $Path $bytes; return }
    $temp=Join-Path ([IO.Path]::GetDirectoryName($Path)) ('vd-state-'+[Guid]::NewGuid().ToString('N')+'.tmp')
    try {
        Write-VdNewBytes $temp $bytes
        if(Test-Path -LiteralPath $Path -PathType Leaf){[IO.File]::Replace($temp,$Path,[System.Management.Automation.Language.NullString]::Value)}
        else {[IO.File]::Move($temp,$Path)}
    } finally {if(Test-Path -LiteralPath $temp -PathType Leaf){Remove-Item -LiteralPath $temp}}
}
function Get-VdJson([string]$Path) {
    try {$value=[IO.File]::ReadAllText($Path) | ConvertFrom-Json}
    catch {throw ('Could not parse JSON file: '+$Path+'. File contents were not printed.')}
    if($value -isnot [Management.Automation.PSCustomObject]){throw 'Expected a JSON object.'}
    return $value
}
function Get-VdInstallCandidates {
    $paths=New-Object 'Collections.Generic.List[string]'
    $registry=@('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*','HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*')
    foreach($item in @(Get-ItemProperty -Path $registry -ErrorAction SilentlyContinue | Where-Object {$_.DisplayName -eq 'Virtual Desktop Streamer'})){
        if($item.InstallLocation){$paths.Add((Get-VdFullPath $item.InstallLocation))}
    }
    foreach($base in @($env:ProgramFiles,${env:ProgramFiles(x86)})){
        if($base){$paths.Add((Get-VdFullPath (Join-Path $base 'Virtual Desktop Streamer')))}
    }
    @($paths | Select-Object -Unique | Where-Object {Test-Path -LiteralPath (Join-Path $_ 'VirtualDesktop.Streamer.exe') -PathType Leaf})
}
function Resolve-VdInstall([string]$Directory) {
    if($Directory){
        $full=Get-VdFullPath $Directory
        if(!(Test-Path -LiteralPath (Join-Path $full 'VirtualDesktop.Streamer.exe') -PathType Leaf)){throw 'The selected folder does not contain VirtualDesktop.Streamer.exe.'}
        return $full
    }
    $candidates=@(Get-VdInstallCandidates)
    if($candidates.Count -eq 0){throw 'Virtual Desktop Streamer installation was not found.'}
    if($candidates.Count -gt 1){throw 'Multiple Streamer installations found. Supply -InstallDirectory explicitly.'}
    return $candidates[0]
}
function Get-VdProcessPath($Process) {
    try {if($Process.Path){return (Get-VdFullPath $Process.Path)}} catch {}
    if(!('VdHost.Native' -as [type])){
        Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
namespace VdHost {
 public static class Native {
  [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,int id);
  [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool QueryFullProcessImageName(IntPtr h,uint flags,StringBuilder path,ref int length);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
  public static string Image(int id) {
   IntPtr h=OpenProcess(0x1000,false,id); if(h==IntPtr.Zero)return null;
   try {var p=new StringBuilder(32768);int n=p.Capacity;return QueryFullProcessImageName(h,0,p,ref n)?p.ToString():null;}
   finally {CloseHandle(h);}
  }
 }
}
'@
    }
    try {$path=[VdHost.Native]::Image($Process.Id);if($path){return (Get-VdFullPath $path)}} catch {}
    return $null
}
function Get-VdProcesses([string]$Executable) {
    $records=@()
    foreach($process in @(Get-Process -Name 'VirtualDesktop.Streamer' -ErrorAction SilentlyContinue)){
        $path=Get-VdProcessPath $process
        if(!$path -or $path -eq $Executable){
            $records += [pscustomobject]@{Id=$process.Id;Executable=$path;VerifiedPath=[bool]$path}
        }
    }
    return $records
}
function Get-VdServices([string]$Directory) {
    $records=@()
    foreach($service in @(Get-Service -Name '*VirtualDesktop*' -ErrorAction SilentlyContinue)){
        $registryPath='HKLM:\SYSTEM\CurrentControlSet\Services\'+$service.Name
        try {$image=(Get-ItemProperty -LiteralPath $registryPath -ErrorAction Stop).ImagePath} catch {continue}
        if(!$image){continue}
        $match=[regex]::Match([Environment]::ExpandEnvironmentVariables($image),'^(?:"(?<path>[^\"]+\.exe)"|(?<path>.+?\.exe))(?:\s|$)',[Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if(!$match.Success){continue}
        $path=Get-VdFullPath $match.Groups['path'].Value
        # The separately installed legacy Virtual Desktop Service is excluded.
        if([IO.Path]::GetDirectoryName($path) -eq $Directory -and [IO.Path]::GetFileName($path) -like 'VirtualDesktop.*.exe'){
            $records += [pscustomobject]@{Name=$service.Name;Executable=$path;Status=$service.Status.ToString()}
        }
    }
    return $records
}
function Get-VdIdentity([string]$Directory) {
    $exe=Join-Path $Directory 'VirtualDesktop.Streamer.exe'
    $hash=Get-VdHash $exe
    $patched=$false
    $receipt=Join-Path $Directory 'vd-offline-install.json'
    if(Test-Path -LiteralPath $receipt -PathType Leaf){
        try {
            $r=Get-VdJson $receipt
            $backupName=if($r.schema_version -eq 2){[string]$r.backup_file}elseif($r.schema_version -eq 1 -and $r.original_sha256 -eq $script:VdStockHash){'VirtualDesktop.Streamer.exe.original-1.34.25.0'}else{''}
            if($backupName -and [IO.Path]::GetFileName($backupName) -ceq $backupName -and $backupName -like 'VirtualDesktop.Streamer.exe.original-*' -and $r.original_sha256 -cmatch '^[a-f0-9]{64}$'){
                $backup=Join-Path $Directory $backupName
                $patched=($r.patched_sha256 -eq $hash -and (Test-Path -LiteralPath $backup -PathType Leaf) -and (Get-VdHash $backup) -eq $r.original_sha256)
            }
        } catch {}
    }
    $supported=$false;$compatibilityError=$null
    if(!$patched){
        if($script:VdPatternCache.ContainsKey($hash)){$supported=[bool]$script:VdPatternCache[$hash]}
        else {
            try {
                . (Join-Path (Split-Path $script:VdHostRoot -Parent) 'streamer\Inspect-Streamer.ps1')
                $inspection=Get-VdPatternInspection $exe | ConvertFrom-Json
                $supported=($inspection.compatible -and $inspection.source_sha256 -eq $hash)
                $script:VdPatternCache[$hash]=$supported
            } catch {$compatibilityError=$_.Exception.GetBaseException().Message}
        }
    }
    [pscustomobject]@{Executable=$exe;InstallDirectory=$Directory;FileVersion=[Diagnostics.FileVersionInfo]::GetVersionInfo($exe).FileVersion;SHA256=$hash;SupportedStock=$supported;SupportedPatched=$patched;CompatibilityError=$compatibilityError}
}
function Assert-VdSupported($Identity) {
    if(!$Identity.SupportedStock -and !$Identity.SupportedPatched){throw 'Unsupported or unrecognized modified Streamer. No application or settings changes were made.'}
}
function Assert-VdStopped([string]$Executable) {
    $processes=@(Get-VdProcesses $Executable)
    if($processes.Count){throw 'Streamer is running, or a Streamer process path could not be verified. Stop it first; elevated access may be required.'}
}
function Get-VdStatePaths([string]$Directory,[switch]$Create) {
    if(!$Directory){throw '-StateDirectory is required for this action.'}
    $full=Get-VdFullPath $Directory
    if($Create){$null=New-Item -ItemType Directory -Path $full -Force}
    if(!(Test-Path -LiteralPath $full -PathType Container)){throw 'State directory does not exist.'}
    [pscustomobject]@{Directory=$full;Receipt=(Join-Path $full 'settings-receipt.json');Backup=(Join-Path $full 'StreamerSettings.original.json');Staged=(Join-Path $full 'StreamerSettings.staged.json');ProcessReceipt=(Join-Path $full 'process-receipt.json')}
}
function Get-VdUpdaterPlan($Identity,[string]$State,[bool]$Restore) {
    if(!$State){throw '-StateDirectory is required for updater actions.'}
    $directory=$Identity.InstallDirectory
    $statePath=Get-VdFullPath $State
    $receiptPath=Join-Path $statePath 'updater-receipt.json'
    $updater=Join-Path $directory 'Updater.exe'
    $backup=Join-Path $directory 'Updater.exe.bak'
    $exists=Test-Path -LiteralPath $updater
    $backupExists=Test-Path -LiteralPath $backup
    $plan=[ordered]@{Action=if($Restore){'CheckRestoreUpdater'}else{'CheckDisableUpdater'};Status=$null;UpdaterPath=$updater;BackupPath=$backup;ReceiptPath=$receiptPath;SHA256=$null;HasReceipt=$false}
    if(!(Test-Path -LiteralPath $receiptPath -PathType Leaf)){
        # An unowned .bak is never interpreted as a backup belonging to us.
        if($Restore){$plan.Status='NoChangesApplied';return [pscustomobject]$plan}
        if($backupExists){throw 'Updater.exe.bak already exists without this session receipt; it will not be overwritten.'}
        if(!$exists){$plan.Status='UpdaterMissing';return [pscustomobject]$plan}
        if(!(Test-Path -LiteralPath $updater -PathType Leaf)){throw 'Updater.exe is not a regular file.'}
        $plan.SHA256=Get-VdHash $updater;$plan.Status='ReadyToDisable';return [pscustomobject]$plan
    }
    $r=Get-VdJson $receiptPath
    if($r.SchemaVersion -ne 1 -or $r.InstallDirectory -ne $directory -or $r.UpdaterPath -ne $updater -or $r.BackupPath -ne $backup -or $r.SHA256 -cnotmatch '^[a-f0-9]{64}$' -or $r.Status -notin @('Prepared','Disabled','Restoring','Restored')){throw 'Updater receipt does not match this installation or supported journal schema.'}
    $plan.HasReceipt=$true;$plan.SHA256=$r.SHA256
    if($exists -and $backupExists){throw 'Both Updater.exe and Updater.exe.bak exist. Refusing to overwrite either file.'}
    if(!$exists -and !$backupExists){throw 'Both recorded updater paths are missing. Preserve the receipt and restore manually from a verified source.'}
    $currentPath=if($backupExists){$backup}else{$updater}
    if(!(Test-Path -LiteralPath $currentPath -PathType Leaf) -or (Get-VdHash $currentPath) -ne $r.SHA256){throw 'Updater changed after this session recorded it. Refusing to overwrite the changed file.'}
    if($backupExists){$plan.Status=if($Restore){'ReadyToRestore'}else{'AlreadyDisabled'}}
    else{$plan.Status=if($Restore){'AlreadyRestored'}else{'ReadyToDisable'}}
    return [pscustomobject]$plan
}
function Disable-VdUpdater($Identity,[string]$State) {
    Assert-VdSupported $Identity
    Assert-VdStopped $Identity.Executable
    $plan=Get-VdUpdaterPlan $Identity $State $false
    if($plan.Status -eq 'UpdaterMissing'){$plan.Action='DisableUpdater';return $plan}
    $null=New-Item -ItemType Directory -Path (Get-VdFullPath $State) -Force
    if($plan.HasReceipt){$r=Get-VdJson $plan.ReceiptPath}
    else{$r=[pscustomobject]@{SchemaVersion=1;InstallDirectory=$Identity.InstallDirectory;UpdaterPath=$plan.UpdaterPath;BackupPath=$plan.BackupPath;SHA256=$plan.SHA256;Status='Prepared';CreatedUTC=[DateTime]::UtcNow.ToString('o')}}
    if($plan.Status -eq 'AlreadyDisabled'){$r.Status='Disabled';Write-VdJson $plan.ReceiptPath $r;$plan.Action='DisableUpdater';return $plan}
    $r.Status='Prepared'
    Write-VdJson $plan.ReceiptPath $r -New:(!$plan.HasReceipt)
    # Recheck after journaling, then use non-overwriting same-directory rename.
    if((Get-VdHash $plan.UpdaterPath) -ne $plan.SHA256 -or (Test-Path -LiteralPath $plan.BackupPath)){throw 'Updater paths changed during preparation.'}
    [IO.File]::Move($plan.UpdaterPath,$plan.BackupPath)
    if((Get-VdHash $plan.BackupPath) -ne $plan.SHA256){throw 'Renamed updater failed verification; preserve its receipt.'}
    $r.Status='Disabled';Write-VdJson $plan.ReceiptPath $r
    $plan.Action='DisableUpdater';$plan.Status='Disabled';return $plan
}
function Restore-VdUpdater($Identity,[string]$State) {
    $plan=Get-VdUpdaterPlan $Identity $State $true
    $plan.Action='RestoreUpdater'
    if($plan.Status -eq 'NoChangesApplied'){return $plan}
    $r=Get-VdJson $plan.ReceiptPath
    if($plan.Status -eq 'AlreadyRestored'){$r.Status='Restored';Write-VdJson $plan.ReceiptPath $r;return $plan}
    $r.Status='Restoring';Write-VdJson $plan.ReceiptPath $r
    if((Get-VdHash $plan.BackupPath) -ne $plan.SHA256 -or (Test-Path -LiteralPath $plan.UpdaterPath)){throw 'Updater paths changed before restore.'}
    [IO.File]::Move($plan.BackupPath,$plan.UpdaterPath)
    if((Get-VdHash $plan.UpdaterPath) -ne $plan.SHA256){throw 'Restored updater failed verification; preserve its receipt.'}
    $r.Status='Restored';Write-VdJson $plan.ReceiptPath $r
    $plan.Status='Restored';return $plan
}
function Get-VdBoundReceipt($Paths,[string]$Directory,[string]$Settings) {
    $receipt=Get-VdJson $Paths.Receipt
    if($receipt.SchemaVersion -ne 1 -or $receipt.InstallDirectory -ne $Directory -or $receipt.SettingsPath -ne $Settings){throw 'Settings receipt does not match the selected installation and settings path.'}
    if($receipt.SourceExists -and (!(Test-Path -LiteralPath $Paths.Backup -PathType Leaf) -or (Get-VdHash $Paths.Backup) -ne $receipt.BeforeSHA256)){throw 'Original settings backup failed verification.'}
    if(!(Test-Path -LiteralPath $Paths.Staged -PathType Leaf) -or (Get-VdHash $Paths.Staged) -ne $receipt.AfterSHA256){throw 'Staged settings failed verification.'}
    return $receipt
}
function Get-VdSettingsSummary($Receipt,$Paths,[string]$Status) {
    [pscustomobject]@{Action=$Action;Status=$Status;SettingsPath=$Receipt.SettingsPath;SourceExists=[bool]$Receipt.SourceExists;BeforeSHA256=$Receipt.BeforeSHA256;AfterSHA256=$Receipt.AfterSHA256;BackupPath=if($Receipt.SourceExists){$Paths.Backup}else{$null};StagedPath=$Paths.Staged;ReceiptPath=$Paths.Receipt;Account=$Receipt.Account;RemoteConnections=$false}
}
function Stage-VdSettings($Identity,[string]$Settings,[string]$State,[string]$LocalAccount) {
    Assert-VdSupported $Identity
    if($LocalAccount -cnotmatch '^[a-z0-9][a-z0-9._-]{2,29}$'){throw 'A lowercase local pairing account of 3-30 characters is required.'}
    $paths=Get-VdStatePaths $State -Create
    if(Test-Path -LiteralPath $paths.Receipt){
        $prior=Get-VdBoundReceipt $paths $Identity.InstallDirectory $Settings
        if($prior.Account -cne $LocalAccount){throw 'State directory already belongs to a different pairing account.'}
        return (Get-VdSettingsSummary $prior $paths 'AlreadyStaged')
    }
    if((Test-Path -LiteralPath $paths.Backup) -or (Test-Path -LiteralPath $paths.Staged)){throw 'Incomplete prior settings stage exists; preserve it and choose a fresh state directory.'}
    $exists=Test-Path -LiteralPath $Settings -PathType Leaf
    $before=$null
    if($exists){
        $bytes=[IO.File]::ReadAllBytes($Settings)
        $before=Get-VdBytesHash $bytes
        $value=Get-VdJson $Settings
        if((Get-VdHash $Settings) -ne $before){throw 'Settings changed during staging. Retry in a new state directory.'}
    } else {$value=[pscustomobject]@{}}
    if(!$value.PSObject.Properties['Accounts']){$value | Add-Member -NotePropertyName Accounts -NotePropertyValue ([pscustomobject]@{})}
    if($value.Accounts -isnot [Management.Automation.PSCustomObject]){throw 'Unsupported Accounts JSON schema.'}
    $quest=@()
    if($value.Accounts.PSObject.Properties['OculusQuest']){
        if($value.Accounts.OculusQuest -isnot [array]){throw 'Accounts.OculusQuest must be a string array.'}
        $quest=@($value.Accounts.OculusQuest)
        if(@($quest | Where-Object {$_ -isnot [string]}).Count){throw 'Accounts.OculusQuest contains unsupported values.'}
    }
    if($quest -inotcontains $LocalAccount){
        if($quest.Count -ge 5){throw 'The Streamer already has five Quest accounts. Remove one explicitly before adding this pairing.'}
        $quest+=@($LocalAccount)
    }
    if($value.Accounts.PSObject.Properties['OculusQuest']){$value.Accounts.OculusQuest=[object[]]$quest}
    else {$value.Accounts | Add-Member -NotePropertyName OculusQuest -NotePropertyValue ([object[]]$quest)}
    if($value.PSObject.Properties['AllowRemoteConnections']){
        if($value.AllowRemoteConnections -isnot [bool]){throw 'AllowRemoteConnections must be a JSON boolean.'}
        $value.AllowRemoteConnections=$false
    } else {$value | Add-Member -NotePropertyName AllowRemoteConnections -NotePropertyValue $false}
    if($exists){Write-VdNewBytes $paths.Backup $bytes;if((Get-VdHash $paths.Backup) -ne $before){throw 'Settings backup verification failed.'}}
    Write-VdJson $paths.Staged $value -New
    $receipt=[pscustomobject]@{SchemaVersion=1;InstallDirectory=$Identity.InstallDirectory;SettingsPath=$Settings;SourceExists=[bool]$exists;BeforeSHA256=$before;AfterSHA256=(Get-VdHash $paths.Staged);Account=$LocalAccount;Status='Staged';StagedUtc=[DateTime]::UtcNow.ToString('o')}
    Write-VdJson $paths.Receipt $receipt -New
    Get-VdSettingsSummary $receipt $paths 'Staged'
}
function Set-VdSettingsFile([string]$Target,[byte[]]$Bytes,[bool]$ExpectedExists,[string]$ExpectedHash) {
    $parent=[IO.Path]::GetDirectoryName($Target)
    $null=New-Item -ItemType Directory -Path $parent -Force
    $temp=Join-Path $parent ('vd-settings-'+[Guid]::NewGuid().ToString('N')+'.tmp')
    try {
        Write-VdNewBytes $temp $Bytes
        if((Get-VdHash $temp) -ne (Get-VdBytesHash $Bytes)){throw 'Temporary settings verification failed.'}
        if($ExpectedExists){
            if(!(Test-Path -LiteralPath $Target -PathType Leaf) -or (Get-VdHash $Target) -ne $ExpectedHash){throw 'Settings changed after staging; refusing to overwrite them.'}
            [IO.File]::Replace($temp,$Target,[System.Management.Automation.Language.NullString]::Value)
        } else {
            if(Test-Path -LiteralPath $Target){throw 'Settings appeared after staging; refusing to overwrite them.'}
            [IO.File]::Move($temp,$Target)
        }
        if((Get-VdHash $Target) -ne (Get-VdBytesHash $Bytes)){throw 'Settings write verification failed.'}
    } finally {if(Test-Path -LiteralPath $temp -PathType Leaf){Remove-Item -LiteralPath $temp}}
}
function Apply-VdSettings($Identity,[string]$Settings,[string]$State) {
    Assert-VdSupported $Identity
    Assert-VdStopped $Identity.Executable
    $paths=Get-VdStatePaths $State
    $receipt=Get-VdBoundReceipt $paths $Identity.InstallDirectory $Settings
    $current=if(Test-Path -LiteralPath $Settings -PathType Leaf){Get-VdHash $Settings}else{$null}
    if($current -eq $receipt.AfterSHA256){$receipt.Status='Applied';Write-VdJson $paths.Receipt $receipt;return (Get-VdSettingsSummary $receipt $paths 'AlreadyApplied')}
    if(($receipt.SourceExists -and $current -ne $receipt.BeforeSHA256) -or (!$receipt.SourceExists -and $current)){throw 'Settings changed after staging; refusing to overwrite them. Prepare a new session.'}
    $receipt.Status='Applying';Write-VdJson $paths.Receipt $receipt
    Set-VdSettingsFile $Settings ([IO.File]::ReadAllBytes($paths.Staged)) $receipt.SourceExists $receipt.BeforeSHA256
    $receipt.Status='Applied';Write-VdJson $paths.Receipt $receipt
    Get-VdSettingsSummary $receipt $paths 'Applied'
}
function New-VdRestorePlan($Receipt,$Paths,[string]$Settings) {
    $current=if(Test-Path -LiteralPath $Settings -PathType Leaf){Get-VdHash $Settings}else{$null}
    $plan=[pscustomobject]@{Mode='';CurrentSHA256=$current;TargetSHA256=$null;Bytes=$null;AccountRemoved=$false;RemoteSettingRestored=$false}
    if($Receipt.Status -in @('Staged','Restored')){
        $plan.Mode='NoChangesApplied';$plan.TargetSHA256=$current;return $plan
    }
    if(($Receipt.SourceExists -and $current -eq $Receipt.BeforeSHA256) -or (!$Receipt.SourceExists -and !$current) -or ($Receipt.Status -eq 'Restored' -and $Receipt.PSObject.Properties['RestoredSHA256'] -and $current -eq $Receipt.RestoredSHA256)){
        $plan.Mode='AlreadyRestored';$plan.TargetSHA256=$current;return $plan
    }
    if(!$current){throw 'Current settings are missing. Automatic restore refuses to recreate a file that may have been deliberately removed.'}
    if($current -eq $Receipt.AfterSHA256){
        if($Receipt.SourceExists){$plan.Mode='ExactBackup';$plan.Bytes=[IO.File]::ReadAllBytes($Paths.Backup);$plan.TargetSHA256=$Receipt.BeforeSHA256}
        else {$plan.Mode='DeleteCreated'}
        return $plan
    }
    if($Receipt.Status -eq 'Applying'){throw 'Interrupted settings apply has conflicting current bytes; restore requires manual review.'}
    # The app commonly saves unrelated values on exit. Undo only our two fields
    # in that current document, keeping its other settings and newly added users.
    $value=Get-VdJson $Settings
    if((Get-VdHash $Settings) -ne $current){throw 'Settings changed while planning restore.'}
    $original=if($Receipt.SourceExists){Get-VdJson $Paths.Backup}else{[pscustomobject]@{}}
    $originalQuest=@()
    $originalHasAccounts=[bool]$original.PSObject.Properties['Accounts']
    $originalHasQuest=$originalHasAccounts -and [bool]$original.Accounts.PSObject.Properties['OculusQuest']
    if($originalHasQuest){$originalQuest=@($original.Accounts.OculusQuest)}
    if($value.PSObject.Properties['Accounts']){
        if($value.Accounts -isnot [Management.Automation.PSCustomObject]){throw 'Current Accounts schema is unsupported; restore was not applied.'}
        if($value.Accounts.PSObject.Properties['OculusQuest']){
            if($value.Accounts.OculusQuest -isnot [array] -or @($value.Accounts.OculusQuest | Where-Object {$_ -isnot [string]}).Count){throw 'Current Quest account schema is unsupported; restore was not applied.'}
            $quest=@($value.Accounts.OculusQuest)
            if($originalQuest -inotcontains $Receipt.Account){
                $matches=@($quest | Where-Object {$_ -ieq $Receipt.Account})
                if($matches.Count -gt 1){throw 'Multiple copies of the pairing account exist; restore requires manual review.'}
                if($matches.Count){
                    $quest=@($quest | Where-Object {$_ -ine $Receipt.Account})
                    $value.Accounts.OculusQuest=[object[]]$quest
                    $plan.AccountRemoved=$true
                }
                if(!$originalHasQuest -and !$quest.Count){$value.Accounts.PSObject.Properties.Remove('OculusQuest')}
            }
        }
        if(!$originalHasAccounts -and @($value.Accounts.PSObject.Properties).Count -eq 0){$value.PSObject.Properties.Remove('Accounts')}
    }
    if($value.PSObject.Properties['AllowRemoteConnections']){
        if($value.AllowRemoteConnections -isnot [bool]){throw 'Current remote-connection setting is not a boolean; restore was not applied.'}
        if(!$value.AllowRemoteConnections){
            if($original.PSObject.Properties['AllowRemoteConnections']){
                if($original.AllowRemoteConnections -isnot [bool]){throw 'Original remote-connection schema is unsupported.'}
                $value.AllowRemoteConnections=$original.AllowRemoteConnections
            } else {$value.PSObject.Properties.Remove('AllowRemoteConnections')}
            $plan.RemoteSettingRestored=$true
        }
    }
    $json=ConvertTo-Json -InputObject $value -Depth 100
    $plan.Bytes=(New-Object Text.UTF8Encoding -ArgumentList $false).GetBytes($json+"`n")
    $plan.TargetSHA256=Get-VdBytesHash $plan.Bytes
    $plan.Mode='MergeCurrent'
    return $plan
}
function Get-VdRestoreSummary($Receipt,$Paths,$Plan,[string]$Status) {
    [pscustomobject]@{Action=$Action;Status=$Status;SettingsPath=$Receipt.SettingsPath;Mode=$Plan.Mode;CurrentSHA256=$Plan.CurrentSHA256;TargetSHA256=$Plan.TargetSHA256;AccountRemoved=$Plan.AccountRemoved;RemoteSettingRestored=$Plan.RemoteSettingRestored;OtherCurrentSettingsPreserved=($Plan.Mode -eq 'MergeCurrent');ReceiptPath=$Paths.Receipt}
}
function Check-VdRestoreSettings($Identity,[string]$Settings,[string]$State) {
    $paths=Get-VdStatePaths $State
    $receipt=Get-VdBoundReceipt $paths $Identity.InstallDirectory $Settings
    $plan=New-VdRestorePlan $receipt $paths $Settings
    Get-VdRestoreSummary $receipt $paths $plan 'RestoreReady'
}
function Restore-VdSettings($Identity,[string]$Settings,[string]$State) {
    Assert-VdSupported $Identity
    Assert-VdStopped $Identity.Executable
    $paths=Get-VdStatePaths $State
    $receipt=Get-VdBoundReceipt $paths $Identity.InstallDirectory $Settings
    $plan=New-VdRestorePlan $receipt $paths $Settings
    if($plan.Mode -in @('AlreadyRestored','NoChangesApplied')){return (Get-VdRestoreSummary $receipt $paths $plan 'AlreadyRestored')}
    $currentBackup=Join-Path $paths.Directory ('StreamerSettings.pre-restore-'+$plan.CurrentSHA256+'.json')
    if(Test-Path -LiteralPath $currentBackup){
        if((Get-VdHash $currentBackup) -ne $plan.CurrentSHA256){throw 'Pre-restore backup failed verification.'}
    } else {
        $currentBytes=[IO.File]::ReadAllBytes($Settings)
        if((Get-VdBytesHash $currentBytes) -ne $plan.CurrentSHA256){throw 'Settings changed before their recovery backup.'}
        Write-VdNewBytes $currentBackup $currentBytes
        if((Get-VdHash $currentBackup) -ne $plan.CurrentSHA256){throw 'Pre-restore backup verification failed.'}
    }
    $receipt.Status='Restoring';Write-VdJson $paths.Receipt $receipt
    if($plan.Mode -eq 'DeleteCreated'){
        # This is the exact newly-created file bound to the receipt; no directory removal.
        if((Get-VdHash $Settings) -ne $plan.CurrentSHA256){throw 'Settings changed before removal.'}
        Remove-Item -LiteralPath $Settings
    } else {Set-VdSettingsFile $Settings $plan.Bytes $true $plan.CurrentSHA256}
    $receipt | Add-Member -NotePropertyName RestoredSHA256 -NotePropertyValue $plan.TargetSHA256 -Force
    $receipt | Add-Member -NotePropertyName PreRestoreBackup -NotePropertyValue $currentBackup -Force
    $receipt.Status='Restored';Write-VdJson $paths.Receipt $receipt
    Get-VdRestoreSummary $receipt $paths $plan 'Restored'
}
function Stop-VdStreamer($Identity,[string]$State,[bool]$MayForce,[int]$Timeout) {
    Assert-VdSupported $Identity
    $paths=Get-VdStatePaths $State -Create
    $processes=@(Get-VdProcesses $Identity.Executable)
    if(@($processes | Where-Object {!$_.VerifiedPath}).Count){throw 'Cannot verify a running Streamer process executable. Retry elevated or exit it manually.'}
    $services=@(Get-VdServices $Identity.InstallDirectory | Where-Object {$_.Status -eq 'Running'})
    $prior=$null
    if(Test-Path -LiteralPath $paths.ProcessReceipt){
        $prior=Get-VdJson $paths.ProcessReceipt
        if($prior.SchemaVersion -ne 1 -or $prior.Executable -ne $Identity.Executable){throw 'Process receipt belongs to another installation.'}
        if($prior.Status -in @('Stopping','Stopped') -and !$processes.Count -and !$services.Count){return [pscustomobject]@{Action='Stop';Status='AlreadyStopped';WasRunning=[bool]$prior.WasRunning;ReceiptPath=$paths.ProcessReceipt}}
    }
    $resumePending=$prior -and $prior.Status -in @('Stopping','Stopped')
    $recordedServices=$services
    if($resumePending){$recordedServices=@(@($prior.Services)+$services | Sort-Object Name -Unique)}
    $receipt=[pscustomobject]@{SchemaVersion=1;Executable=$Identity.Executable;SHA256=$Identity.SHA256;WasRunning=($processes.Count -gt 0);Processes=$processes;Services=$services;Status='Stopping';Forced=$false}
    $receipt.Services=$recordedServices
    if($resumePending){$receipt.WasRunning=[bool]($receipt.WasRunning -or $prior.WasRunning)}
    Write-VdJson $paths.ProcessReceipt $receipt
    foreach($service in $services){
        # Only service images directly inside this installation were admitted.
        Stop-Service -Name $service.Name -ErrorAction Stop
        (Get-Service -Name $service.Name -ErrorAction Stop).WaitForStatus('Stopped',[TimeSpan]::FromSeconds($Timeout))
    }
    if($processes.Count){
        if((Get-VdHash $Identity.Executable) -ne $Identity.SHA256){throw 'Streamer executable changed before the exit request.'}
        # Static App/Arguments evidence confirms /Exit sends its supported exit IPC.
        $exitProcess=Start-Process -FilePath $Identity.Executable -ArgumentList '/Exit' -WorkingDirectory $Identity.InstallDirectory -WindowStyle Hidden -PassThru -ErrorAction Stop
        $null=$exitProcess.WaitForExit([Math]::Min(3000,$Timeout*1000))
        $timer=[Diagnostics.Stopwatch]::StartNew()
        do {$remaining=@(Get-VdProcesses $Identity.Executable);if(!$remaining.Count){break};Start-Sleep -Milliseconds 200} while($timer.Elapsed.TotalSeconds -lt $Timeout)
        if($remaining.Count -and $MayForce){
            foreach($record in $remaining){
                if(!$record.VerifiedPath -or $record.Id -notin @($processes.Id)){throw 'Unexpected or unverifiable process appeared; forced stop was not applied.'}
                Stop-Process -Id $record.Id -ErrorAction Stop
            }
            Start-Sleep -Milliseconds 300
            $receipt.Forced=$true
        }
        Assert-VdStopped $Identity.Executable
    }
    $receipt.Status='Stopped';Write-VdJson $paths.ProcessReceipt $receipt
    [pscustomobject]@{Action='Stop';Status='Stopped';WasRunning=[bool]$receipt.WasRunning;Forced=[bool]$receipt.Forced;ServicesStopped=@($services.Name);ReceiptPath=$paths.ProcessReceipt}
}
function Start-VdStreamer($Identity,[string]$State,[bool]$EvenIfStopped) {
    Assert-VdSupported $Identity
    $paths=Get-VdStatePaths $State
    $receipt=Get-VdJson $paths.ProcessReceipt
    if($receipt.SchemaVersion -ne 1 -or $receipt.Executable -ne $Identity.Executable){throw 'Process receipt belongs to another installation.'}
    foreach($service in @($receipt.Services)){
        $current=@(Get-VdServices $Identity.InstallDirectory | Where-Object {$_.Name -eq $service.Name -and $_.Executable -eq $service.Executable})
        if($current.Count -ne 1){throw 'Service image changed after stopping; start was refused.'}
        if($current[0].Status -ne 'Running'){Start-Service -Name $service.Name -ErrorAction Stop}
    }
    $running=@(Get-VdProcesses $Identity.Executable)
    if(@($running | Where-Object {!$_.VerifiedPath}).Count){throw 'Cannot verify an existing Streamer process path; start was refused.'}
    if($running.Count){return [pscustomobject]@{Action='Start';Status='AlreadyRunning';IsRunning=$true;Processes=$running}}
    if(!$receipt.WasRunning -and !$EvenIfStopped){return [pscustomobject]@{Action='Start';Status='LeftStopped';IsRunning=$false}}
    if((Get-VdHash $Identity.Executable) -ne $Identity.SHA256){throw 'Streamer executable changed before launch.'}
    $process=Start-Process -FilePath $Identity.Executable -WorkingDirectory $Identity.InstallDirectory -WindowStyle Hidden -PassThru -ErrorAction Stop
    Start-Sleep -Milliseconds 750
    $process.Refresh()
    if($process.HasExited){throw 'Streamer exited during startup. Inspect its log before continuing.'}
    $receipt.Status='Started';Write-VdJson $paths.ProcessReceipt $receipt
    [pscustomobject]@{Action='Start';Status='Started';IsRunning=$true;ProcessId=$process.Id;RuntimeStreamingTested=$false}
}
function Invoke-VdHost {
    if($Action -eq 'Detect' -and !$InstallDirectory){
        $candidates=@(Get-VdInstallCandidates)
        if($candidates.Count -ne 1){return [pscustomobject]@{Action='Detect';Found=($candidates.Count -gt 0);IsAmbiguous=($candidates.Count -gt 1);Candidates=$candidates;IsRunning=$null;SettingsPath=(Get-VdFullPath $SettingsPath)}}
        $directory=$candidates[0]
    } else {$directory=Resolve-VdInstall $InstallDirectory}
    $identity=Get-VdIdentity $directory
    $settings=Get-VdFullPath $SettingsPath
    switch($Action){
        'Detect' {
            $processes=@(Get-VdProcesses $identity.Executable)
            $exists=Test-Path -LiteralPath $settings -PathType Leaf
            return [pscustomobject]@{Action='Detect';Found=$true;IsAmbiguous=$false;Candidates=@($directory);InstallDirectory=$directory;Executable=$identity.Executable;Version=$identity.FileVersion;Sha256=$identity.SHA256;SupportedStock=$identity.SupportedStock;SupportedPatched=$identity.SupportedPatched;CompatibilityError=$identity.CompatibilityError;IsRunning=($processes.Count -gt 0);ProcessInspectionComplete=(@($processes | Where-Object {!$_.VerifiedPath}).Count -eq 0);Processes=$processes;Services=@(Get-VdServices $directory);SettingsPath=$settings;SettingsExists=[bool]$exists;SettingsSHA256=if($exists){Get-VdHash $settings}else{$null}}
        }
        'StageSettings' {return (Stage-VdSettings $identity $settings $StateDirectory $Account)}
        'ApplySettings' {return (Apply-VdSettings $identity $settings $StateDirectory)}
        'CheckRestoreSettings' {return (Check-VdRestoreSettings $identity $settings $StateDirectory)}
        'RestoreSettings' {return (Restore-VdSettings $identity $settings $StateDirectory)}
        'Stop' {return (Stop-VdStreamer $identity $StateDirectory $ForceStop.IsPresent $WaitSeconds)}
        'Start' {return (Start-VdStreamer $identity $StateDirectory $StartIfStopped.IsPresent)}
        'CheckDisableUpdater' {Assert-VdSupported $identity;return (Get-VdUpdaterPlan $identity $StateDirectory $false)}
        'DisableUpdater' {return (Disable-VdUpdater $identity $StateDirectory)}
        'CheckRestoreUpdater' {return (Get-VdUpdaterPlan $identity $StateDirectory $true)}
        'RestoreUpdater' {return (Restore-VdUpdater $identity $StateDirectory)}
    }
}
if($MyInvocation.InvocationName -ne '.'){
    try {Invoke-VdHost | ConvertTo-Json -Depth 15;exit 0}
    catch {[pscustomobject]@{Action=$Action;Error=$_.Exception.Message;Succeeded=$false}|ConvertTo-Json -Depth 5;exit 1}
}
