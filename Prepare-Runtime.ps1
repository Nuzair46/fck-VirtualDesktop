param()
$ErrorActionPreference='Stop'
$runtimeRoot=Join-Path $PSScriptRoot 'runtime'
$downloadRoot=Join-Path $runtimeRoot 'downloads'
$pins=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime-pins.json') -Raw | ConvertFrom-Json
if($pins.schema_version -ne 1 -or $pins.platform -ne 'windows-x64'){throw 'Unsupported runtime manifest'}
if(![Environment]::Is64BitOperatingSystem){throw 'This portable package requires 64-bit Windows'}
New-Item -ItemType Directory -Path $runtimeRoot,$downloadRoot -Force | Out-Null
function Assert-RuntimeArchive([string]$Path,$Pin) {
    $actual=(Get-FileHash -LiteralPath $Path -Algorithm $Pin.hash_algorithm).Hash.ToLowerInvariant()
    if($actual -ne $Pin.hash){throw ('Runtime archive hash mismatch: '+$Path)}
}
foreach($pin in $pins.runtimes) {
    if($pin.name -notin @('python','dotnet') -or [IO.Path]::GetFileName($pin.archive) -ne $pin.archive){throw 'Invalid runtime destination'}
    $archive=Join-Path $downloadRoot $pin.archive
    if(Test-Path -LiteralPath $archive) {
        Assert-RuntimeArchive $archive $pin
    } else {
        $temporary=Join-Path $downloadRoot ([Guid]::NewGuid().ToString('N')+'.zip')
        try {
            Write-Output ('Downloading portable '+$pin.name+' '+$pin.version)
            [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing -Uri $pin.url -OutFile $temporary
            Assert-RuntimeArchive $temporary $pin
            Move-Item -LiteralPath $temporary -Destination $archive
        } finally {
            if(Test-Path -LiteralPath $temporary){Remove-Item -LiteralPath $temporary}
        }
    }
    $destination=Join-Path $runtimeRoot $pin.name
    Write-Output ('Extracting verified '+$pin.name+' archive')
    Expand-Archive -LiteralPath $archive -DestinationPath $destination -Force
    if(!(Test-Path -LiteralPath (Join-Path $destination $pin.entrypoint))){throw ('Runtime entrypoint missing: '+$pin.name)}
    foreach($license in $pin.licenses) {
        if(!(Test-Path -LiteralPath (Join-Path $destination $license))){throw ('Runtime license missing: '+$license)}
    }
}
# Embeddable Python is isolated from user/site packages. Explicit project paths
# let the standalone backend and its sibling modules import from this bundle.
$pythonPaths=@('python314.zip','.','..\..','..\..\android-runtime','..\..\streamer')
[IO.File]::WriteAllLines((Join-Path $runtimeRoot 'python\python314._pth'),$pythonPaths,[Text.Encoding]::ASCII)
$files=foreach($name in @('python','dotnet')) {
    foreach($file in (Get-ChildItem -LiteralPath (Join-Path $runtimeRoot $name) -File -Recurse)) {
        [ordered]@{path=$file.FullName.Substring($runtimeRoot.Length+1).Replace('\','/');size=$file.Length;sha256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
    }
}
$receipt=[ordered]@{schema_version=1;created_utc=[DateTime]::UtcNow.ToString('o');runtimes=$pins.runtimes;files=@($files);system_install_performed=$false}
$receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $runtimeRoot 'runtime-manifest.json') -Encoding UTF8
Write-Output ('Portable runtimes ready: '+$runtimeRoot)
