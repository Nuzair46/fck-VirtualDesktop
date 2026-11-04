[CmdletBinding()]
param(
 [Parameter(Mandatory=$true)][string]$Apk,
 [string]$Streamer='C:\Program Files\Virtual Desktop Streamer\VirtualDesktop.Streamer.exe',
 [Parameter(Mandatory=$true)][string]$OutputDirectory,
 [string]$Pairing,
 [string]$SigningDirectory,
 [string]$Python
)
$ErrorActionPreference='Stop'
if(!$Pairing){$Pairing=Join-Path $PSScriptRoot 'local\pairing.json'}
if(!$SigningDirectory){$SigningDirectory=Join-Path $PSScriptRoot 'android-runtime\keys'}
if(!$Python){
 $bundled=Join-Path $PSScriptRoot 'runtime\python\python.exe'
 if(Test-Path -LiteralPath $bundled){$Python=$bundled}
}
if(!$Python){
 $command=Get-Command python.exe -ErrorAction SilentlyContinue
 if($command -and $command.Source -notlike '*WindowsApps*'){$Python=$command.Source}
 else {
  $uvRoot=Join-Path $env:APPDATA 'uv\python'
  if(Test-Path -LiteralPath $uvRoot){
   $candidate=Get-ChildItem -LiteralPath $uvRoot -Filter python.exe -Recurse | Select-Object -First 1
   if($candidate){$Python=$candidate.FullName}
  }
 }
}
if(!$Python){throw 'Python 3.11+ not found. Supply -Python with the path to python.exe.'}
& $Python (Join-Path $PSScriptRoot 'build.py') --apk $Apk --streamer $Streamer --output $OutputDirectory --pairing $Pairing --signing-directory $SigningDirectory
if($LASTEXITCODE -ne 0){throw 'Patch build failed. See the verification logs in the output directory.'}
