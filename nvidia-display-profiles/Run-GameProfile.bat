@echo off
setlocal

cd /d "%~dp0"
title Display Profile - GAME
cls

if /i "%~1"=="--elevated" goto run

net session >nul 2>&1
if not "%errorlevel%"=="0" (
    echo Requesting administrator privileges...
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList '--elevated' -WorkingDirectory '%~dp0' -Verb RunAs"
    exit /b
)

:run
echo Display Profile - GAME
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Set-DisplayProfile.ps1" GAME
exit /b %errorlevel%
