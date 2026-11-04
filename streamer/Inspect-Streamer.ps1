[CmdletBinding()]
param([string]$InputPath,[string]$ProfilePath,[switch]$Capture)
$ErrorActionPreference='Stop'
$script:VdInspectorRoot=$PSScriptRoot
[Console]::OutputEncoding=New-Object Text.UTF8Encoding -ArgumentList $false
$OutputEncoding=[Console]::OutputEncoding
function Get-VdPatternInspection([string]$Path,[string]$Profile,[bool]$CaptureProfile=$false) {
    if(!$Profile){$Profile=Join-Path $script:VdInspectorRoot 'streamer-spec.json'}
    $dnlib=Join-Path (Split-Path $script:VdInspectorRoot -Parent) 'tools\dnlib.dll'
    if(!(Test-Path -LiteralPath $dnlib -PathType Leaf)){throw 'Pinned dnlib is missing. Run Build-ManagedTool.ps1 first.'}
    if((Get-FileHash -LiteralPath $dnlib -Algorithm SHA256).Hash.ToLowerInvariant() -ne '97ef5328d1d1703924c48d9cd1d45b7227fe3836e116dcded623b6a0dbd52c23'){throw 'dnlib dependency hash mismatch.'}
    if(!('StreamerInspector' -as [type])){
        $null=[Reflection.Assembly]::LoadFrom($dnlib)
        Add-Type -Path (Join-Path $script:VdInspectorRoot 'StreamerInspector.cs') -ReferencedAssemblies @($dnlib,'System.Core.dll','System.Web.Extensions.dll')
    }
    return [StreamerInspector]::Inspect([IO.Path]::GetFullPath($Path),[IO.Path]::GetFullPath($Profile),$CaptureProfile)
}
if($MyInvocation.InvocationName -ne '.'){
    try {Get-VdPatternInspection $InputPath $ProfilePath $Capture.IsPresent;exit 0}
    catch {[pscustomobject]@{compatible=$false;error=$_.Exception.GetBaseException().Message}|ConvertTo-Json -Compress;exit 1}
}
