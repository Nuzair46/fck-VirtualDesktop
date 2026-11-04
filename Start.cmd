@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch-Tool.ps1"
if errorlevel 1 (
  echo.
  echo The tool could not start. See the error above.
  pause
  exit /b 1
)
endlocal
