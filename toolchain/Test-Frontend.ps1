param([string]$OutputDirectory)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
if(!$OutputDirectory){$OutputDirectory=Join-Path $root 'tools\frontend-checks'}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$exe=Join-Path $OutputDirectory 'FrontendChecks.exe'
& $compiler /nologo /target:exe "/out:$exe" /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll (Join-Path $PSScriptRoot 'ui\FrontendChecks.cs')
if($LASTEXITCODE -ne 0){throw 'Frontend checks could not compile.'}
& $exe $root $OutputDirectory
if($LASTEXITCODE -ne 0){throw 'Frontend checks failed.'}
