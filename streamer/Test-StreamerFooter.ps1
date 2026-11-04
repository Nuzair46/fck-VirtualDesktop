param([Parameter(Mandatory=$true)][string]$PatchedPath)
$ErrorActionPreference='Stop'
$dnlib=Join-Path (Split-Path $PSScriptRoot -Parent) 'tools\dnlib.dll'
if((Get-FileHash -LiteralPath $dnlib).Hash.ToLowerInvariant() -ne '97ef5328d1d1703924c48d9cd1d45b7227fe3836e116dcded623b6a0dbd52c23'){throw 'dnlib dependency hash mismatch.'}
$null=[Reflection.Assembly]::LoadFrom($dnlib)
Add-Type -Path (Join-Path $PSScriptRoot 'StreamerFixture.cs') -ReferencedAssemblies @($dnlib,'System.Core.dll')
$actual=[StreamerFixture]::EvaluateFooter($PatchedPath)
if($actual -cne '9.8.7 fkvd'){throw 'Footer fixture did not append the requested suffix.'}
[pscustomobject]@{Expected='9.8.7 fkvd';Actual=$actual;VendorAssemblyExecuted=$false;VersionFormatter='owned stub'}|ConvertTo-Json -Compress
