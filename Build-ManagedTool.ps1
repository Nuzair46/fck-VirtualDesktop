param([string]$DnlibPath)
$ErrorActionPreference='Stop'
if(!$DnlibPath){$DnlibPath=Join-Path $PSScriptRoot 'tools\dnlib\lib\net45\dnlib.dll'}
$dnlibPackageSha='63bc2f9579568204cc8b30fa9f6700a231bcf868a8032e09118887af3eafee58'
$dnlibDllSha='97ef5328d1d1703924c48d9cd1d45b7227fe3836e116dcded623b6a0dbd52c23'
$bin=Join-Path $PSScriptRoot 'tools'
$package=Join-Path $bin 'dnlib.zip'
function Assert-ManagedToolHash([string]$Path,[string]$Expected) {
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Expected) {
        throw ('Dependency hash mismatch; leaving the file untouched: ' + $Path)
    }
}
$compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if(!(Test-Path -LiteralPath $compiler)){throw '.NET Framework C# compiler not found'}
New-Item -ItemType Directory -Path $bin -Force | Out-Null
if(Test-Path -LiteralPath $package){Assert-ManagedToolHash $package $dnlibPackageSha}
if(!(Test-Path -LiteralPath $DnlibPath)) {
    $defaultDnlib=Join-Path $bin 'dnlib\lib\net45\dnlib.dll'
    if([IO.Path]::GetFullPath($DnlibPath) -ne [IO.Path]::GetFullPath($defaultDnlib)) {
        throw 'The supplied -DnlibPath does not exist; omit it to bootstrap pinned dnlib 4.5.0.'
    }
    if(!(Test-Path -LiteralPath $package)) {
        $download=Join-Path $bin ('dnlib-download-' + [Guid]::NewGuid().ToString('N') + '.zip')
        try {
            [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing -Uri 'https://api.nuget.org/v3-flatcontainer/dnlib/4.5.0/dnlib.4.5.0.nupkg' -OutFile $download
            Assert-ManagedToolHash $download $dnlibPackageSha
            Move-Item -LiteralPath $download -Destination $package
        } finally {
            if(Test-Path -LiteralPath $download){Remove-Item -LiteralPath $download}
        }
    }
    Expand-Archive -LiteralPath $package -DestinationPath (Join-Path $bin 'dnlib') -Force
}
Assert-ManagedToolHash $DnlibPath $dnlibDllSha
$runtimeDnlib=Join-Path $bin 'dnlib.dll'
if(Test-Path -LiteralPath $runtimeDnlib) {
    # A metadata inspection process may currently have the valid DLL loaded.
    # Reuse it instead of overwriting an identical locked dependency.
    Assert-ManagedToolHash $runtimeDnlib $dnlibDllSha
} else {
    Copy-Item -LiteralPath $DnlibPath -Destination $runtimeDnlib
    Assert-ManagedToolHash $runtimeDnlib $dnlibDllSha
}
& $compiler /nologo /optimize+ /target:exe "/out:$bin\PatchManaged.exe" "/reference:$DnlibPath" /reference:System.Core.dll /reference:System.Web.Extensions.dll (Join-Path $PSScriptRoot 'src\PatchManaged.cs') (Join-Path $PSScriptRoot 'src\ManagedProfile.cs')
if($LASTEXITCODE -ne 0){throw 'Managed patch tool compilation failed'}
$sourceHashes=[ordered]@{}
foreach($relative in @('src/PatchManaged.cs','src/ManagedProfile.cs','Build-ManagedTool.ps1')) {
    $sourceHashes[$relative]=(Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
}
$receipt=[ordered]@{schema_version=1;source_sha256=$sourceHashes;executable_sha256=(Get-FileHash -LiteralPath (Join-Path $bin 'PatchManaged.exe') -Algorithm SHA256).Hash.ToLowerInvariant()}
$receipt | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $bin 'PatchManaged.build.json') -Encoding UTF8
Get-Item -LiteralPath (Join-Path $bin 'PatchManaged.exe') | Select-Object FullName,Length
