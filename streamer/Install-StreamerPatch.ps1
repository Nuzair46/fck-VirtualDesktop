[CmdletBinding()]
param(
    [ValidateSet('Install','Restore')][string]$Action = 'Install',
    [string]$PatchedExe,
    [string]$InstallDirectory = 'C:\Program Files\Virtual Desktop Streamer'
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding -ArgumentList $false
$OutputEncoding=[Console]::OutputEncoding
$vdDirectory = [IO.Path]::GetFullPath($InstallDirectory).TrimEnd('\')
if (!(Test-Path -LiteralPath $vdDirectory -PathType Container)) { throw 'The selected Streamer installation folder does not exist.' }
$vdTarget = Join-Path $vdDirectory 'VirtualDesktop.Streamer.exe'
$vdBackup = $null
$vdReceipt = Join-Path $vdDirectory 'vd-offline-install.json'
$vdTemp = Join-Path $vdDirectory ('vd-offline-' + [Guid]::NewGuid().ToString('N') + '.tmp')
foreach ($vdPath in @($vdTarget,$vdReceipt,$vdTemp)) {
    if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($vdPath)) -ne $vdDirectory) { throw 'Installer path escaped the selected directory.' }
}
if (Get-Process -Name 'VirtualDesktop.Streamer' -ErrorAction SilentlyContinue | Where-Object { !$_.Path -or [IO.Path]::GetFullPath($_.Path) -eq $vdTarget }) {
    throw 'Exit Virtual Desktop Streamer from its tray menu before installing or restoring. This script does not stop processes.'
}
function Get-VdHash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Assert-VdHashString([string]$Value) { if($Value -cnotmatch '^[a-f0-9]{64}$'){throw 'Invalid SHA-256 in patch or install receipt.'} }
function Resolve-VdBackup($Receipt) {
    Assert-VdHashString $Receipt.original_sha256
    Assert-VdHashString $Receipt.patched_sha256
    if($Receipt.schema_version -eq 2){$name=[string]$Receipt.backup_file}
    elseif($Receipt.schema_version -eq 1 -and $Receipt.original_sha256 -eq '0229a322137f55687240d2b51f5f7002056d9277d7fa001ec6cb4eb25af0bfb8'){$name='VirtualDesktop.Streamer.exe.original-1.34.25.0'}
    else {throw 'Unsupported install receipt.'}
    if(!$name -or [IO.Path]::GetFileName($name) -cne $name -or $name -notlike 'VirtualDesktop.Streamer.exe.original-*'){throw 'Unsafe backup path in install receipt.'}
    $path=Join-Path $vdDirectory $name
    if(!(Test-Path -LiteralPath $path -PathType Leaf) -or (Get-VdHash $path) -ne $Receipt.original_sha256){throw 'Original backup verification failed.'}
    return $path
}
if (!(Test-Path -LiteralPath $vdTarget -PathType Leaf)) { throw 'VirtualDesktop.Streamer.exe was not found in the selected installation.' }
$vdCurrent = Get-VdHash $vdTarget
if ($Action -eq 'Install') {
    if (!$PatchedExe -or !(Test-Path -LiteralPath $PatchedExe -PathType Leaf)) { throw 'Supply -PatchedExe pointing to the generated executable.' }
    $vdPatchManifest = $PatchedExe + '.patch.json'
    if (!(Test-Path -LiteralPath $vdPatchManifest -PathType Leaf)) { throw 'The generated .patch.json report must accompany the patched executable.' }
    $vdReport = Get-Content -LiteralPath $vdPatchManifest -Raw | ConvertFrom-Json
    if ($vdReport.schema_version -ne 2 -or !$vdReport.compatibility.compatible -or $vdReport.compatibility.profile_id -ne 'vd-streamer-local-v2' -or $vdReport.compatibility.source_sha256 -ne $vdReport.source_sha256) { throw 'Unsupported contextual patch report.' }
    $vdExpectedOriginal=[string]$vdReport.source_sha256
    Assert-VdHashString $vdExpectedOriginal
    Assert-VdHashString $vdReport.output_sha256
    if ($vdCurrent -ne $vdExpectedOriginal) { throw 'Installed executable differs from the exact source used to prepare this patch. Prepare again from the current installation.' }
    if(Test-Path -LiteralPath $vdReceipt -PathType Leaf){
        $prior=Get-Content -LiteralPath $vdReceipt -Raw | ConvertFrom-Json
        $null=Resolve-VdBackup $prior
        if($vdCurrent -ne $prior.original_sha256){
            # A vendor update can leave our old receipt behind. Keep that verified
            # recovery record while preparing a new receipt for the exact new source.
            $archive=Join-Path $vdDirectory ('vd-offline-install.'+$prior.original_sha256+'.'+$prior.patched_sha256+'.json')
            if(Test-Path -LiteralPath $archive){if((Get-VdHash $archive) -ne (Get-VdHash $vdReceipt)){throw 'Previous receipt archive conflicts with the current receipt.'}}
            else{[IO.File]::Copy($vdReceipt,$archive,$false)}
        }
    }
    $vdBackup=Join-Path $vdDirectory ('VirtualDesktop.Streamer.exe.original-'+$vdExpectedOriginal)
    $vdDesired = Get-VdHash $PatchedExe
    if ($vdDesired -ne $vdReport.output_sha256 -or $vdDesired -eq $vdExpectedOriginal) { throw 'Patched executable hash does not match its report.' }
    if (Test-Path -LiteralPath $vdBackup) {
        if ((Get-VdHash $vdBackup) -ne $vdExpectedOriginal) { throw 'Existing backup is not the expected original; leaving it untouched.' }
    } else { [IO.File]::Copy($vdTarget,$vdBackup,$false) }
    if ((Get-VdHash $vdBackup) -ne $vdExpectedOriginal) { throw 'Backup verification failed.' }
    $vdSource = [IO.Path]::GetFullPath($PatchedExe)
    $vdNewReceipt = [ordered]@{schema_version=2;original_sha256=$vdExpectedOriginal;patched_sha256=$vdDesired;backup_file=[IO.Path]::GetFileName($vdBackup);source_version=$vdReport.version;profile_id=$vdReport.compatibility.profile_id;installed_utc=[DateTime]::UtcNow.ToString('o')}
} else {
    if (!(Test-Path -LiteralPath $vdReceipt -PathType Leaf)) { throw 'A verified original backup and install receipt are required to restore.' }
    $vdInstalled = Get-Content -LiteralPath $vdReceipt -Raw | ConvertFrom-Json
    $vdBackup=Resolve-VdBackup $vdInstalled
    $vdExpectedOriginal=[string]$vdInstalled.original_sha256
    if ($vdCurrent -eq $vdExpectedOriginal) { [pscustomobject]@{Action='Restore';Status='AlreadyRestored';Executable=$vdTarget;SHA256=$vdCurrent;Backup=$vdBackup;Launched=$false} | ConvertTo-Json; return }
    if ($vdCurrent -ne $vdInstalled.patched_sha256) { throw 'The installed executable changed after patching. Restore stopped to avoid overwriting an update or another modification.' }
    $vdSource=$vdBackup
    $vdDesired=$vdExpectedOriginal
}
try {
    Copy-Item -LiteralPath $vdSource -Destination $vdTemp
    if ((Get-VdHash $vdTemp) -ne $vdDesired) { throw 'Staged executable verification failed.' }
    if ((Get-VdHash $vdTarget) -ne $vdCurrent) { throw 'Installed executable changed during preparation.' }
    if ($Action -eq 'Install') {
        # Write recovery information before changing the executable. If the
        # replacement fails, the receipt is harmless and the original remains.
        $receiptTemp=$vdReceipt+'.'+[Guid]::NewGuid().ToString('N')+'.tmp'
        try {
            [IO.File]::WriteAllText($receiptTemp,($vdNewReceipt|ConvertTo-Json),(New-Object Text.UTF8Encoding -ArgumentList $false))
            if(Test-Path -LiteralPath $vdReceipt){[IO.File]::Replace($receiptTemp,$vdReceipt,[System.Management.Automation.Language.NullString]::Value)}
            else{[IO.File]::Move($receiptTemp,$vdReceipt)}
        } finally {if(Test-Path -LiteralPath $receiptTemp){Remove-Item -LiteralPath $receiptTemp}}
    }
    # File.Replace is atomic on this volume. The verified backup remains intact.
    [IO.File]::Replace($vdTemp,$vdTarget,[System.Management.Automation.Language.NullString]::Value)
    if ((Get-VdHash $vdTarget) -ne $vdDesired) { throw 'Installed executable verification failed.' }
    [pscustomobject]@{Action=$Action;Executable=$vdTarget;SHA256=$vdDesired;Backup=$vdBackup;Launched=$false} | ConvertTo-Json
} finally {
    if (Test-Path -LiteralPath $vdTemp -PathType Leaf) { Remove-Item -LiteralPath $vdTemp }
}
