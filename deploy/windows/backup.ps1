<#
.SYNOPSIS
    Daily verified backup plus 30-day pruning (Windows version of
    scripts/daily_backup.sh).

.DESCRIPTION
    Run every day at 02:00 by the "MTC Supplies - Daily backup" scheduled
    task (as SYSTEM). Writes data\backups\daily-<yyyyMMdd-HHmmss>.sqlite3
    with backup_db --quiesce (a short owned maintenance window, verified,
    with a manifest), then prune_backups --days 30, which never deletes the
    newest verified pre-update/pre-reset recovery backup. Everything is
    logged to data\logs\backup.log; a failure exits non-zero so the task
    shows it ("mtc status").

.PARAMETER Root
    Install folder. Default: the install this script runs from, else C:\MTCSupplies.
#>
param(
    [string]$Root = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 1.0
. (Join-Path $PSScriptRoot 'common.ps1')

$exitCode = 0
try {
    Initialize-MtcConsole
    $Root = Resolve-MtcRoot -Root $Root -ScriptDir $PSScriptRoot
    Set-MtcLog -Path (Join-Path $Root 'data\logs\backup.log')
    $current = Assert-MtcInstalled -Root $Root
    $settings = Import-MtcSettings -Path (Join-Path $Root 'settings.env')
    $backupDir = Get-MtcBackupDir -Settings $settings -Root $Root
    $dest = Join-Path $backupDir ('daily-' + (New-MtcStamp) + '.sqlite3')
    Write-MtcLog 'daily backup start'
    Invoke-MtcManage -Release $current -Arguments @('backup_db', '--dest', $dest, '--quiesce') -Quiet `
        -FailMessage 'backup_db failed' | Out-Null
    Invoke-MtcManage -Release $current -Arguments @('prune_backups', '--days', [string]$MtcBackupRetentionDays,
        '--dir', $backupDir) -Quiet -FailMessage 'prune_backups failed' | Out-Null
    Write-MtcLog ('daily backup complete: ' + $dest)
} catch {
    $exitCode = 1
    Write-MtcLog ('DAILY BACKUP FAILED: ' + $_.Exception.Message)
    Write-Host ('Daily backup failed: ' + $_.Exception.Message)
}
exit $exitCode
