param([Parameter(Mandatory=$true)][string]$InputPath,[Parameter(Mandatory=$true)][string]$OutputPath,[ValidateSet('layout','encoding','ambiguous')][string]$Mode='layout')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Inspect-Streamer.ps1') -InputPath $InputPath
$null=Get-VdPatternInspection $InputPath
$dnlib=Join-Path (Split-Path $PSScriptRoot -Parent) 'tools\dnlib.dll'
Add-Type -Path (Join-Path $PSScriptRoot 'StreamerFixture.cs') -ReferencedAssemblies @($dnlib,'System.Core.dll')
[StreamerFixture]::Create($InputPath,$OutputPath,$Mode)
