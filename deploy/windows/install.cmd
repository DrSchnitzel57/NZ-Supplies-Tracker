@echo off
setlocal EnableExtensions
title MTC Supplies Tracker - install
rem Double-click this file to install the MTC Supplies Tracker on this PC.
rem It asks Windows for administrator rights, then runs install.ps1 from this
rem folder. Options (-Root, -Port, -HostAddress, ...) are available by running
rem install.ps1 directly; see the top of that file.

set "HERE=%~dp0"
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%HERE%install.ps1" goto notunzipped
if not exist "%HERE%common.ps1" goto notunzipped
if not exist "%HERE%..\..\manage.py" goto notunzipped

"%PS%" -NoProfile -ExecutionPolicy Bypass -Command "if (([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 0 } else { exit 1 }"
if errorlevel 1 goto elevate

rem Files from a downloaded zip carry a "came from the internet" mark that
rem can block scripts; clear it for this release folder only.
for %%I in ("%HERE%..\..") do set "MTC_RELEASE=%%~fI"
"%PS%" -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath $env:MTC_RELEASE -Recurse -File | Unblock-File"

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%HERE%install.ps1"
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
    echo Administrator permission was not given, so nothing was installed.
    pause
    exit /b 1
)
exit /b 0

:notunzipped
echo This file must be run from the unzipped release folder.
echo Right-click the release .zip, choose "Extract All...", then open the
echo extracted folder and double-click deploy\windows\install.cmd again.
pause
exit /b 1
