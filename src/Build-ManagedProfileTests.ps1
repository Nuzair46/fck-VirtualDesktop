param()
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$dnlib=Join-Path $root 'tools\dnlib.dll'
$output=Join-Path $root 'tools\ManagedProfileFixtures.exe'
& $compiler /nologo /optimize+ /target:exe "/out:$output" "/reference:$dnlib" /reference:System.Core.dll (Join-Path $PSScriptRoot 'ManagedProfileFixtures.cs')
if($LASTEXITCODE -ne 0){throw 'Managed profile fixture compiler failed'}
