<#
.SYNOPSIS
    Removes the MTC Supplies Tracker's scheduled tasks, firewall rule and
    program files from this PC. The data (database, backups) and
    settings.env are KEPT unless -DeleteData is given.

.DESCRIPTION
    Usual way (administrator window):  C:\MTCSupplies\mtc.cmd uninstall
    Keeping data means a later install into the same folder picks it up
    again (same secret key, same database, same certificate).

.PARAMETER Root
    Install folder. Default: the install this script runs from, else C:\MTCSupplies.
.PARAMETER DeleteData
    Also delete the database, ALL backups, settings and certificate. You must
    type DELETE to confirm. Copy data\backups somewhere safe first if unsure.
.PARAMETER Yes
    Do not ask before removing the program (DeleteData still needs DELETE typed).
#>
param(
    [string]$Root = '',
    [switch]$DeleteData,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 1.0
. (Join-Path $PSScriptRoot 'common.ps1')

$exitCode = 0
try {
    Assert-MtcAdmin
    $Root = Resolve-MtcRoot -Root $Root -ScriptDir $PSScriptRoot
    $problem = Get-MtcRootPathProblem $Root
    if ($problem) { throw "Refusing to work on '$Root': $problem." }
    $looksInstalled = (Test-Path -LiteralPath (Join-Path $Root 'settings.env')) -or
        (Test-Path -LiteralPath (Join-Path $Root 'releases')) -or (Test-Path -LiteralPath (Join-Path $Root 'tools\caddy.exe'))
    if (-not (Test-Path -LiteralPath $Root) -or -not $looksInstalled) {
        throw "No MTC Supplies install found in $Root."
    }
    Write-Host ''
    Write-Host ('This removes the MTC Supplies Tracker program from ' + $Root + '.') -ForegroundColor Yellow
    Write-Host 'The app stops for everyone.'
    if ($DeleteData) {
        Write-Host 'ALL DATA WILL ALSO BE DELETED: the database, every backup, settings.env and the certificate.' -ForegroundColor Red
    } else {
        Write-Host ('Kept: ' + $Root + '\data (database, backups, logs), settings.env and MTC-Supplies-CA.crt.')
    }
    if (-not (Read-MtcYesNo -Question 'Continue?' -Default $false -Assume:$Yes)) { throw 'Cancelled; nothing was changed.' }
    if ($DeleteData) {
        $typed = Read-Host '    Type DELETE to permanently delete all data'
        if ($typed -cne 'DELETE') { throw 'Cancelled; nothing was changed.' }
    }

    Write-MtcStep 'Stopping and removing the scheduled tasks'
    Stop-MtcAppTask -Root $Root
    Stop-MtcHttpsTask -Root $Root
    foreach ($name in @($MtcAppTaskName, $MtcHttpsTaskName, $MtcBackupTaskName)) {
        if (Get-MtcTask -Name $name) {
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-MtcOk ('Removed task ' + $name)
        }
    }
    Stop-MtcStrayProcesses -Root $Root -Port $MtcAppPort -ImageName 'caddy'

    Write-MtcStep 'Removing the firewall rule'
    Get-NetFirewallRule -Name $MtcFirewallRuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Write-MtcOk 'Firewall rule removed'

    $ca = Join-Path $Root 'MTC-Supplies-CA.crt'
    if (Test-Path -LiteralPath $ca -PathType Leaf) {
        $thumbprint = Get-MtcCertificateThumbprint -Path $ca
        $trusted = @(Get-ChildItem -Path Cert:\LocalMachine\Root -ErrorAction SilentlyContinue |
                Where-Object { $_.Thumbprint -eq $thumbprint })
        if ($trusted.Count -gt 0 -and (Read-MtcYesNo -Question 'Also stop trusting the app''s certificate on this PC?' -Default ([bool]$DeleteData) -Assume:$Yes)) {
            $trusted | Remove-Item -Force
            Write-MtcOk 'Certificate removed from this PC''s trusted list'
        }
    }

    Write-MtcStep 'Removing the program files'
    $link = Join-Path $Root 'current'
    $item = Get-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue
    if ($null -ne $item -and ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        [System.IO.Directory]::Delete($link, $false)
    }
    foreach ($sub in @('releases', 'tools', 'current')) {
        Remove-MtcTree -Path (Join-Path $Root $sub) -Root $Root
    }
    foreach ($file in @('Caddyfile', 'previous.txt', 'mtc.cmd')) {
        $path = Join-Path $Root $file
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    }
    Write-MtcOk 'Program files removed'

    if ($DeleteData) {
        Write-MtcStep 'Deleting all data'
        Set-Location -LiteralPath ([System.IO.Path]::GetTempPath())
        [Environment]::CurrentDirectory = [System.IO.Path]::GetTempPath()
        try {
            Remove-MtcTree -Path $Root -Root $Root
            Write-MtcOk ($Root + ' deleted')
        } catch {
            Write-MtcWarn ('Some files could not be deleted (is a window open in ' + $Root + '?). Close it and delete the folder by hand.')
        }
    } else {
        Write-Host ''
        Write-Host ('Uninstalled. Your data is still in ' + $Root + '\data; installing again into ' + $Root + ' reuses it.')
    }
} catch {
    $exitCode = 1
    Write-MtcFail $_.Exception.Message
}
exit $exitCode
