[CmdletBinding()]
param([string]$OutputDirectory)
$ErrorActionPreference='Stop'
if (!$OutputDirectory) { $OutputDirectory=Join-Path $PSScriptRoot 'dist' }
$OutputDirectory=[IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Choose a new release output directory; existing releases are preserved.' }
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
$marker=Join-Path $OutputDirectory 'BUILD-INCOMPLETE.txt'
Set-Content -LiteralPath $marker -Value 'Release packaging is incomplete.' -Encoding UTF8
Push-Location $PSScriptRoot
try {
    & (Join-Path $PSScriptRoot 'Prepare-Runtime.ps1')
    & (Join-Path $PSScriptRoot 'Build-ManagedTool.ps1')
    & (Join-Path $PSScriptRoot 'Build-Tool.ps1')
    & (Join-Path $PSScriptRoot 'toolchain\Test-Frontend.ps1')
    $python=Join-Path $PSScriptRoot 'runtime\python\python.exe'
    & $python (Join-Path $PSScriptRoot 'android-runtime\prepare_signing_tools.py')
    if ($LASTEXITCODE -ne 0) { throw 'Android signing tools could not be provisioned.' }
    & $python (Join-Path $PSScriptRoot 'android-runtime\prepare_adb.py')
    if ($LASTEXITCODE -ne 0) { throw 'Bundled ADB could not be provisioned.' }
    & $python -c 'import build; build.ensure_verifier()'
    if ($LASTEXITCODE -ne 0) { throw 'IL verifier could not be provisioned.' }
    & $python -m unittest -b toolchain.test_device toolchain.test_orchestration -q
    if ($LASTEXITCODE -ne 0) { throw 'Tool transaction tests failed.' }
    $payloadDirectory=Join-Path $OutputDirectory 'VirtualDesktop-OfflineTool'
    & $python (Join-Path $PSScriptRoot 'package_toolchain.py') --destination $payloadDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Portable payload packaging failed.' }
    $payloadZip=Join-Path $OutputDirectory 'VirtualDesktop-OfflineTool-portable.zip'
    $exe=Join-Path $OutputDirectory 'VirtualDesktop-OfflineTool.exe'
    & (Join-Path $PSScriptRoot 'Build-Standalone.ps1') -PayloadZip $payloadZip -OutputExe $exe
    $report=Join-Path $OutputDirectory 'standalone-verification.json'
    $process=Start-Process -FilePath $exe -ArgumentList @('--verify-only','--report',('"'+$report+'"')) -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0 -or !(Test-Path -LiteralPath $report)) { throw 'Standalone payload verification failed.' }
    $hash=(Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath (Join-Path $OutputDirectory 'SHA256SUMS.txt') -Value ($hash+'  VirtualDesktop-OfflineTool.exe') -Encoding ASCII
    Remove-Item -LiteralPath $marker
    [pscustomobject]@{Executable=$exe;SHA256=$hash;Verification=$report;RuntimeTested=$false} | ConvertTo-Json
} finally { Pop-Location }
