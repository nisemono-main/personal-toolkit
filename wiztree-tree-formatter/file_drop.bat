@echo off
REM Drop one or more CSV files onto this .bat to run the PowerShell script in the same folder.
REM Place this .bat in the same folder as formatter.ps1.

set ScriptDir=%~dp0
set Ps1=%ScriptDir%formatter.ps1

if not exist "%Ps1%" (
  echo PowerShell script not found: "%Ps1%"
  pause
  exit /b 1
)

REM If multiple files are dropped, handle the first one only (adjust if you want to loop)
if "%~1"=="" (
  REM no arg - just run the PS1 which will try to find CSVs in the script folder
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%Ps1%"
) else (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%Ps1%" "%~1"
)

pause