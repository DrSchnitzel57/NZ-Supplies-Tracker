@echo off
setlocal EnableExtensions
title MTC Supplies Tracker - update
rem Double-click this file inside the UNZIPPED NEW release to update the
rem installed MTC Supplies Tracker (C:\MTCSupplies) to this release. It asks
rem for administrator rights, then runs update.ps1 from this folder, which
rem rolls back automatically if anything goes wrong. To update an install
rem in another folder run:  update.ps1 -Root D:\Folder

set "HERE=%~dp0"
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%HERE%update.ps1" goto notunzipped
if not exist "%HERE%common.ps1" goto notunzipped
if not exist "%HERE%..\..\manage.py" goto notunzipped

"%PS%" -NoProfile -ExecutionPolicy Bypass -Command "if (([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 0 } else { exit 1 }"
if errorlevel 1 goto elevate

for %%I in ("%HERE%..\..") do set "MTC_RELEASE=%%~fI"
"%PS%" -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath $env:MTC_RELEASE -Recurse -File | Unblock-File"

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%HERE%update.ps1"
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%

:elevate
if /i "%~1"=="--elevated" (
    echo Administrator rights are needed, but this window does not have them.
    pause
    exit /b 1
)
echo Asking Windows for administrator rights...
set "MTC_SELF=%~f0"
"%PS%" -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath $env:MTC_SELF -ArgumentList '--elevated' -Verb RunAs"
if errorlevel 1 (
    echo Administrator permission was not given, so nothing was changed.
    pause
    exit /b 1
)
exit /b 0

:notunzipped
echo This file must be run from the unzipped NEW release folder.
echo Right-click the release .zip, choose "Extract All...", then open the
echo extracted folder and double-click deploy\windows\update.cmd again.
pause
exit /b 1
