param()
$ErrorActionPreference='Stop'
$compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if(!(Test-Path -LiteralPath $compiler)){throw '.NET Framework compiler not found. Install .NET Framework 4.8 or build this frontend with its C# compiler.'}
$source=Join-Path $PSScriptRoot 'toolchain\ui\VdOfflineTool.cs'
$manifest=Join-Path $PSScriptRoot 'toolchain\ui\app.manifest'
$bin=Join-Path $PSScriptRoot 'tools'
New-Item -ItemType Directory -Path $bin -Force | Out-Null
$exe=Join-Path $bin 'VdOfflineTool.exe'
& $compiler /nologo /optimize+ /target:winexe /platform:anycpu "/out:$exe" "/win32manifest:$manifest" /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll $source
if($LASTEXITCODE -ne 0){throw 'Frontend compilation failed'}
Get-Item -LiteralPath $exe | Select-Object FullName,Length
