param([Parameter(Mandatory=$true)][string]$PatchedExe,[string]$OriginalExe='C:\Program Files\Virtual Desktop Streamer\VirtualDesktop.Streamer.exe')
$ErrorActionPreference='Stop'
$vdTestDirectory=Join-Path $PSScriptRoot ('installer-test-'+[Guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path $vdTestDirectory
$vdTestExe=Join-Path $vdTestDirectory 'VirtualDesktop.Streamer.exe'
$vdOriginal=$OriginalExe
$vdPatched=[IO.Path]::GetFullPath($PatchedExe)
$vdInstaller=Join-Path $PSScriptRoot 'Install-StreamerPatch.ps1'
# The fixture contains an inert copy, never a process. Mock enumeration only
# for this test because the sandbox cannot read the real process executable.
function Get-Process { param([string]$Name,[object]$ErrorAction) if($Name -ne 'VirtualDesktop.Streamer'){throw 'Unexpected process query'}; return @() }
$vdBefore=(Get-FileHash -LiteralPath $vdOriginal -Algorithm SHA256).Hash
Copy-Item -LiteralPath $vdOriginal -Destination $vdTestExe
try {
    & $vdInstaller -Action Install -PatchedExe $vdPatched -InstallDirectory $vdTestDirectory
    if ((Get-FileHash -LiteralPath $vdTestExe).Hash -ne (Get-FileHash -LiteralPath $vdPatched).Hash) { throw 'Install mismatch' }
    # A later modification must be refused, even when the original backup is valid.
    $changed=[IO.File]::ReadAllBytes($vdTestExe);$changed[100]=$changed[100] -bxor 1
    [IO.File]::WriteAllBytes($vdTestExe,$changed)
    $changedHash=(Get-FileHash -LiteralPath $vdTestExe).Hash
    $refused=$false
    try {& $vdInstaller -Action Restore -InstallDirectory $vdTestDirectory | Out-Null} catch {if($_.Exception.Message -notmatch 'changed after patching'){throw};$refused=$true}
    if(!$refused -or (Get-FileHash -LiteralPath $vdTestExe).Hash -ne $changedHash){throw 'Restore overwrote a later change'}
    [IO.File]::Copy($vdPatched,$vdTestExe,$true)
    & $vdInstaller -Action Restore -InstallDirectory $vdTestDirectory
    if ((Get-FileHash -LiteralPath $vdTestExe).Hash -ne $vdBefore) { throw 'Restore mismatch' }
    & $vdInstaller -Action Restore -InstallDirectory $vdTestDirectory
    if ((Get-FileHash -LiteralPath $vdOriginal).Hash -ne $vdBefore) { throw 'Real installation was changed' }
    [pscustomobject]@{install='passed';restore='passed';repeat_restore='passed';changed_current_refused='passed';source_unchanged=$true;process_enumeration='mocked: no process for inert fixture'} | ConvertTo-Json
} finally {
    # Delete only the exact generated scratch files under this test directory.
    if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($vdTestDirectory)) -ne [IO.Path]::GetFullPath($PSScriptRoot)) { throw 'Unexpected test path' }
    foreach ($vdName in @('VirtualDesktop.Streamer.exe',('VirtualDesktop.Streamer.exe.original-'+$vdBefore.ToLowerInvariant()),'vd-offline-install.json')) {
        $vdFile=Join-Path $vdTestDirectory $vdName
        if (Test-Path -LiteralPath $vdFile -PathType Leaf) { Remove-Item -LiteralPath $vdFile }
    }
    Remove-Item -LiteralPath $vdTestDirectory
}
