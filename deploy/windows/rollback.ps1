<#
.SYNOPSIS
    Goes back to the release that ran before the last update, together with
    the data as it was just before that update.

.DESCRIPTION
    Usual way (administrator window):  C:\MTCSupplies\mtc.cmd rollback

    Uses previous.txt (written by update.ps1) and the newest verified
    pre-update-* backup that was taken by that previous version. You are
    shown when that backup was made and must type RESTORE: everything
    entered after that moment is removed from the app, but a safety copy of
    the current data (pre-rollback-*.sqlite3) is saved first.

    Protocol (shared with "mtc restore"): verify backup -> owned maintenance
    window -> safety backup -> stop app -> switch <root>\current back ->
    restore_db -> start -> readiness incl. version -> release the window.
    A rollback can be done once; to move forward again, run an update.

.PARAMETER Root
    Install folder. Default: the install this script runs from, else C:\MTCSupplies.
.PARAMETER BackupFile
    Use this backup instead of the newest matching pre-update backup.
.PARAMETER Takeover
    Take over a maintenance window left behind by a crashed operation (audited).
.PARAMETER Yes
    Skip the typed confirmation.
#>
param(
    [string]$Root = '',
    [string]$BackupFile = '',
    [switch]$Takeover,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 1.0
. (Join-Path $PSScriptRoot 'common.ps1')

$exitCode = 0
try {
    Initialize-MtcConsole
    Assert-MtcAdmin
    $Root = Resolve-MtcRoot -Root $Root -ScriptDir $PSScriptRoot
    $current = Assert-MtcInstalled -Root $Root
    Set-MtcLog -Path (Join-Path $Root 'data\logs\rollback.log')
    Write-MtcLog 'rollback.ps1 started'
    Set-Location -LiteralPath $Root
    [Environment]::CurrentDirectory = $Root
    $settings = Import-MtcSettings -Path (Join-Path $Root 'settings.env')
    Assert-MtcSettingsUsable $settings

    Write-MtcStep 'Finding the previous version'
    $target = Get-MtcPreviousRelease -Root $Root
    if (-not $target) {
        throw ('There is no previous version recorded (previous.txt). A rollback is only possible once, ' +
            'right after an update. To restore a backup with the current version use: mtc restore <backup>')
    }
    if (-not (Test-Path -LiteralPath (Get-MtcPython -Release $target) -PathType Leaf)) {
        throw "The previous release folder is missing or incomplete: $target"
    }
    if ($target.ToLowerInvariant() -eq $current.ToLowerInvariant()) { throw 'The previous release is the one already running.' }
    $targetVersion = Get-MtcReleaseVersion -Release $target
    $currentVersion = Get-MtcReleaseVersion -Release $current
    Write-MtcOk ('Running ' + $currentVersion + '; previous version ' + $targetVersion + ' (' + $target + ')')

    $backupDir = Get-MtcBackupDir -Settings $settings -Root $Root
    if ($BackupFile) {
        $chosen = ConvertTo-MtcFullPath $BackupFile
    } else {
        $candidates = @(Get-ChildItem -LiteralPath $backupDir -Filter 'pre-update-*.sqlite3' -File -ErrorAction SilentlyContinue |
                ForEach-Object { [pscustomobject]@{ Name = $_.Name; FullName = $_.FullName; Manifest = (Read-MtcManifest -BackupPath $_.FullName) } })
        $pick = Select-MtcRollbackBackup -Candidates $candidates -TargetVersion $targetVersion
        if ($null -eq $pick) {
            throw ("No completed pre-update backup made by version $targetVersion was found in $backupDir. " +
                'Pass one explicitly with -BackupFile, or ask the developer.')
        }
        $chosen = $pick.FullName
    }

    $result = Invoke-MtcRestoreFlow -Root $Root -CurrentRelease $current -TargetRelease $target -BackupFile $chosen `
        -Owner ('rollback-' + $targetVersion) -SafetyPrefix 'pre-rollback-' -BackupDir $backupDir -Takeover:$Takeover -Yes:$Yes
    $exitCode = $result.Code
    Write-Host ''
    if ($result.Code -eq 0) {
        Set-MtcPreviousRelease -Root $Root -Release $null
        Copy-MtcLauncher -Root $Root -Release $target
        Write-Host ('ROLLBACK COMPLETE: version ' + $targetVersion + ' is running again.') -ForegroundColor Green
        Write-Host $result.Message
        Write-Host ('To move forward again later, run an update with the newer release zip.')
        Write-MtcLog 'rollback.ps1 finished OK'
    } elseif ($result.Code -eq 1) {
        Write-MtcFail $result.Message
        Write-Host ('    Version ' + $currentVersion + ' is still running.') -ForegroundColor Yellow
    } else {
        Write-MtcFail $result.Message
        Write-Host '    ROLLBACK DID NOT FINISH. Saving stays paused until this is resolved.' -ForegroundColor Red
    }
} catch {
    $exitCode = 1
    Write-Host ''
    Write-MtcFail $_.Exception.Message
    Write-Host '    Nothing was changed.'
}
if ($MtcLogFile) { Write-Host ('Details: ' + $MtcLogFile) }
exit $exitCode
