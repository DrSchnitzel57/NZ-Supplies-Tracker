@echo off
rem Runs Caddy (LAN HTTPS) for the MTC Supplies Tracker. Started by the
rem "MTC Supplies - HTTPS" scheduled task as SYSTEM:   run-caddy.cmd "<root>"
rem Caddy keeps its certificates (including the local root CA staff PCs
rem trust) in <root>\data\caddy\caddy, chosen through XDG_DATA_HOME.
setlocal
set "MTC_ROOT=%~1"
if "%MTC_ROOT%"=="" for %%I in ("%~dp0..\..\..") do set "MTC_ROOT=%%~fI"
set "XDG_DATA_HOME=%MTC_ROOT%\data\caddy"
set "XDG_CONFIG_HOME=%MTC_ROOT%\data\caddy-config"
if not exist "%MTC_ROOT%\data\logs" mkdir "%MTC_ROOT%\data\logs"
"%MTC_ROOT%\tools\caddy.exe" run --config "%MTC_ROOT%\Caddyfile" --adapter caddyfile >> "%MTC_ROOT%\data\logs\caddy-console.log" 2>&1
exit /b %ERRORLEVEL%
