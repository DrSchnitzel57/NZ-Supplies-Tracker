@echo off
rem MTC Supplies Tracker - administrator commands. install/update copy this
rem file to the install folder (C:\MTCSupplies\mtc.cmd). Run it from an
rem administrator Command Prompt or PowerShell window, for example:
rem     C:\MTCSupplies\mtc status
rem     C:\MTCSupplies\mtc help
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0current\deploy\windows\manage.ps1" -Root "%~dp0." %*
exit /b %ERRORLEVEL%
