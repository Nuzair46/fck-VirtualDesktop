param([string]$PythonPath)
$ErrorActionPreference='Stop'
function Test-ToolPython([string]$Candidate) {
    if(!$Candidate -or !(Test-Path -LiteralPath $Candidate -PathType Leaf)){return $false}
    if($Candidate -like '*\Microsoft\WindowsApps\*'){return $false}
    try {
        & $Candidate -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>$null
        return $LASTEXITCODE -eq 0
    } catch {return $false}
}
if($PythonPath) {
    if(!(Test-ToolPython $PythonPath)){throw 'The supplied -PythonPath must point to Python 3.11 or newer.'}
} else {
    $candidates=New-Object 'System.Collections.Generic.List[string]'
    $candidates.Add((Join-Path $PSScriptRoot 'runtime\python\python.exe'))
    foreach($name in @('python.exe','python3.exe')) {
        $command=Get-Command $name -ErrorAction SilentlyContinue
        if($command){$candidates.Add($command.Source)}
    }
    $uvRoot=Join-Path $env:APPDATA 'uv\python'
    if(Test-Path -LiteralPath $uvRoot) {
        foreach($folder in (Get-ChildItem -LiteralPath $uvRoot -Directory | Sort-Object Name -Descending)) {
            $candidates.Add((Join-Path $folder.FullName 'python.exe'))
        }
    }
    foreach($candidate in $candidates) {
        if(Test-ToolPython $candidate){$PythonPath=$candidate;break}
    }
    if(!$PythonPath) {
        $py=Get-Command py.exe -ErrorAction SilentlyContinue
        if($py) {
            try {
                $found=& $py.Source -3 -c 'import sys; print(sys.executable)' 2>$null
                if($LASTEXITCODE -eq 0 -and (Test-ToolPython $found)){$PythonPath=$found}
            } catch {}
        }
    }
    if(!$PythonPath){throw 'Python 3.11 or newer was not found. Install Python, or run Launch-Tool.ps1 -PythonPath C:\path\to\python.exe.'}
}
$exe=Join-Path $PSScriptRoot 'tools\VdOfflineTool.exe'
$source=Join-Path $PSScriptRoot 'toolchain\ui\VdOfflineTool.cs'
if(!(Test-Path -LiteralPath $exe) -or (Get-Item -LiteralPath $source).LastWriteTimeUtc -gt (Get-Item -LiteralPath $exe).LastWriteTimeUtc) {
    & (Join-Path $PSScriptRoot 'Build-Tool.ps1')
}
# These are filesystem paths, so literal quotes cannot occur in either value.
# Trim trailing backslashes before quoting to preserve Windows argument parsing.
$rootArgument=$PSScriptRoot.TrimEnd('\')
$pythonArgument=[IO.Path]::GetFullPath($PythonPath)
$arguments='--root "' + $rootArgument + '" --python "' + $pythonArgument + '"'
Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $PSScriptRoot -WindowStyle Normal
