<#
.SYNOPSIS
    Updates the MTC Supplies Tracker to a new release, rolling back
    automatically if anything goes wrong.

.DESCRIPTION
    Usual ways to run it (administrator window):
        C:\MTCSupplies\mtc.cmd update "D:\Downloads\mtc-supplies-1.0.1.zip"
    or double-click update.cmd inside the unzipped NEW release folder.

    Order (same protocol as scripts/update.sh):
      1. Check the new release; build its folder, Python environment and web
         files while the old version keeps serving (no downtime yet).
      2. Enter an owned maintenance window (saving is paused for staff) and
         take a verified pre-update backup inside it.
      3. Stop the app, migrate the database with the new code, switch
         <root>\current to the new release (previous.txt records the old
         one), start the app, and require /readyz/ to report ok AND the new
         version.
      4. Release the maintenance window with its token.
    Any failure after the backup rolls back automatically: stop the app,
    switch back, restore the pre-update backup with restore_db (which hands
    the window to a fresh token), start the old version, check readiness and
    release the window. The newest three release folders are kept.

.PARAMETER ReleaseZip
    New release: a .zip or an unzipped folder. Default: the release this
    script belongs to (used by update.cmd).
.PARAMETER Root
    Install folder. Default: the install this script runs from, else C:\MTCSupplies.
.PARAMETER Takeover
    Take over a maintenance window left behind by a crashed earlier run
    (recorded in the audit log; saving stays paused throughout).
.PARAMETER Reinstall
    Allow re-installing the version that is already running.
.PARAMETER Yes
    Do not ask for confirmation.
.PARAMETER UseSystemCertificates
    Let uv trust the Windows certificate store (networks that inspect HTTPS).
#>
param(
    [string]$ReleaseZip = '',
    [string]$Root = '',
    [switch]$Takeover,
    [switch]$Reinstall,
    [switch]$Yes,
    [switch]$UseSystemCertificates
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 1.0
. (Join-Path $PSScriptRoot 'common.ps1')

$S = @{
    Root = ''; OldRelease = ''; OldVersion = ''; OldPrevious = $null; NewRelease = ''; NewVersion = ''
    Owner = ''; Token = ''; BackupFile = ''; Staging = ''; Phase = 'checks'
    Migrated = $false; Switched = $false
}

function Write-MtcRecoveryHelp {
    param($State, [string]$Token)
    Write-Host ''
    Write-Host '    WHAT TO DO NOW' -ForegroundColor Red
    Write-Host '    Saving stays switched off for staff until this is resolved (their data is safe).'
    Write-Host ('    1. Read ' + $State.Root + '\data\logs\update.log and app.log, or send them to the developer.')
    Write-Host ('    2. Put the pre-update data back with the version that matches it:')
    Write-Host ('         ' + $State.Root + '\mtc.cmd restore "' + $State.BackupFile + '"')
    if ($Token) {
        Write-Host ('    3. Only if the app is working again but saving is still off, release the window:')
        Write-Host ('         ' + $State.Root + '\mtc.cmd maintenance --exit --token ' + $Token)
    }
}

function Invoke-MtcUpdateRollback {
    # Returns 1 when the old version is serving again with writes reopened,
    # 2 when manual recovery is needed.
    param($State)
    Write-MtcStep ('Rolling back to version ' + $State.OldVersion)
    if ($State.Migrated -or $State.Switched) {
        try {
            Stop-MtcAppTask -Root $State.Root
        } catch {
            Write-MtcFail ('Could not stop the app: ' + $_.Exception.Message)
            Write-MtcRecoveryHelp -State $State -Token $State.Token
            return 2
        }
    }
    $releaseToken = $State.Token
    try {
        if ($State.Switched) {
            Set-MtcCurrentRelease -Root $State.Root -Release $State.OldRelease
            Set-MtcPreviousRelease -Root $State.Root -Release $State.OldPrevious
            Write-MtcOk ('Switched back to ' + $State.OldRelease)
        }
        if ($State.Migrated) {
            Write-MtcInfo 'Restoring the database from the pre-update backup ...'
            $restoreArgs = @('restore_db', $State.BackupFile, '--expected-version', $State.OldVersion,
                '--allow-newer-data-loss', '--owner', $State.Owner, '--token', $State.Token)
            $restore = Invoke-MtcManage -Release $State.OldRelease -Arguments $restoreArgs -AllowFailure
            $token = Get-MtcRestoreToken $restore.Output
            if ($restore.ExitCode -ne 0 -and -not $token) {
                # Refused before touching data (for example the failed
                # migration left the window unreadable): explicit, audited
                # takeover. Writes stay closed throughout.
                Write-MtcInfo 'Retrying the restore with an explicit takeover of the maintenance window ...'
                $restore = Invoke-MtcManage -Release $State.OldRelease -AllowFailure -Arguments @('restore_db',
                    $State.BackupFile, '--expected-version', $State.OldVersion, '--allow-newer-data-loss', '--force-takeover')
                $token = Get-MtcRestoreToken $restore.Output
            }
            if ($restore.ExitCode -ne 0) {
                Write-MtcFail 'Restoring the pre-update backup failed (the restore keeps the live database untouched when it refuses).'
                if ($token) { $releaseToken = $token }
                Write-MtcRecoveryHelp -State $State -Token $releaseToken
                return 2
            }
            $releaseToken = $token
            Write-MtcOk 'Database restored from the pre-update backup'
        }
        Start-MtcAppTask
        $ready = Wait-MtcReady -ExpectedVersion $State.OldVersion -TimeoutSeconds 180
        if (-not $ready.Ok) {
            Write-MtcFail ('Version ' + $State.OldVersion + ' did not become ready again (' + $ready.Reason + ').')
            Write-MtcRecoveryHelp -State $State -Token $releaseToken
            return 2
        }
        Write-MtcOk ('Version ' + $State.OldVersion + ' is serving again')
        $exit = Invoke-MtcManage -Release $State.OldRelease -Arguments @('maintenance', '--exit', '--token', $releaseToken) `
            -AllowFailure -Quiet
        if ($exit.ExitCode -ne 0) {
            Write-MtcFail 'The app is running but saving is still paused (could not release the maintenance window).'
            Write-MtcRecoveryHelp -State $State -Token $releaseToken
            return 2
        }
        Write-MtcOk 'Saving re-enabled for staff'
        return 1
    } catch {
        Write-MtcFail ('Rollback step failed: ' + $_.Exception.Message)
        Write-MtcRecoveryHelp -State $State -Token $releaseToken
        return 2
    }
}

$exitCode = 0
try {
    Initialize-MtcConsole
    Assert-MtcAdmin
    $S.Root = Resolve-MtcRoot -Root $Root -ScriptDir $PSScriptRoot
    $S.OldRelease = Assert-MtcInstalled -Root $S.Root
    Set-MtcLog -Path (Join-Path $S.Root 'data\logs\update.log')
    Write-MtcLog 'update.ps1 started'
    Set-Location -LiteralPath $S.Root
    [Environment]::CurrentDirectory = $S.Root
    if (-not (Get-MtcTask -Name $MtcAppTaskName)) {
        throw 'The app''s scheduled task is missing; run install.ps1 -Repair first.'
    }
    $settings = Import-MtcSettings -Path (Join-Path $S.Root 'settings.env')
    Assert-MtcSettingsUsable $settings
    $S.OldVersion = Get-MtcReleaseVersion -Release $S.OldRelease
    $S.OldPrevious = Get-MtcPreviousRelease -Root $S.Root

    Write-MtcStep 'Checking the new release'
    if (-not $ReleaseZip) { $ReleaseZip = Join-Path $PSScriptRoot '..\..' }
    $source = ConvertTo-MtcFullPath $ReleaseZip
    if (Test-Path -LiteralPath $source -PathType Leaf) {
        if ($source -notlike '*.zip') { throw "Expected a .zip file or a folder, got $source" }
        $S.Staging = Join-Path $S.Root ('releases\.staging-' + (New-MtcStamp))
        Write-MtcInfo "Unpacking $source"
        Expand-Archive -LiteralPath $source -DestinationPath $S.Staging -Force
        $source = $S.Staging
    } elseif (-not (Test-Path -LiteralPath $source -PathType Container)) {
        throw "Release not found: $ReleaseZip"
    }
    $new = Get-MtcReleaseInfo -Path (Find-MtcReleaseRoot -Dir $source)
    $S.NewVersion = $new.Version
    $order = Compare-MtcVersion $new.Version $S.OldVersion
    if ($order -lt 0) {
        throw ("Release " + $new.Version + " is older than the running " + $S.OldVersion +
            '. To go back to the previous version use: mtc rollback')
    }
    if ($order -eq 0 -and -not $Reinstall) {
        throw ("Version " + $new.Version + ' is already running. Use -Reinstall to install it again.')
    }
    Write-MtcOk ('Update ' + $S.OldVersion + ' -> ' + $new.Version)
    Write-Host ''
    Write-Host '    During the update staff can still look things up, but saving is paused for a few'
    Write-Host '    minutes and the app restarts once. If anything fails it rolls back by itself.'
    if (-not (Read-MtcYesNo -Question 'Start the update now?' -Default $true -Assume:$Yes)) {
        throw 'Update cancelled; nothing was changed.'
    }

    Write-MtcStep ('Preparing ' + $new.Version + ' (the current version keeps running)')
    $S.Phase = 'prepare'
    Install-MtcTool -Root $S.Root -Name 'uv' -Version $MtcUvVersion -Url $MtcUvUrl -ChecksumUrl $MtcUvChecksumUrl `
        -Asset $MtcUvAsset -Algorithm $MtcUvAlgorithm -PinnedHash $MtcUvHash -ExeName 'uv.exe' -VersionArgs @('--version') | Out-Null
    Install-MtcPython -Root $S.Root -SystemCertificates:$UseSystemCertificates
    $S.NewRelease = Join-Path $S.Root ('releases\' + $new.Version + '-' + (New-MtcStamp))
    Copy-MtcReleaseFiles -Source $new.Path -Destination $S.NewRelease
    Initialize-MtcVenv -Root $S.Root -Release $S.NewRelease -SystemCertificates:$UseSystemCertificates
    Invoke-MtcManage -Release $S.NewRelease -Arguments @('check') -Quiet -FailMessage 'The new release failed its self-check' | Out-Null
    Invoke-MtcManage -Release $S.NewRelease -Arguments @('collectstatic', '--noinput') -Quiet `
        -FailMessage 'Collecting the new web page files failed' | Out-Null
    Write-MtcOk ('Prepared ' + $S.NewRelease)

    Write-MtcStep 'Pausing saving (maintenance window) and taking a verified backup'
    $S.Owner = 'update-' + $new.Version
    $enterArgs = @('maintenance', '--enter', '--owner', $S.Owner, '--reason', ('update to ' + $new.Version))
    if ($Takeover) { $enterArgs += '--force' }
    $enter = Invoke-MtcManage -Release $S.OldRelease -Arguments $enterArgs -AllowFailure -Quiet
    $S.Token = Get-MtcMaintenanceToken $enter.Output
    if ($enter.ExitCode -ne 0 -or -not $S.Token) {
        throw ('Could not pause saving: another operation holds the maintenance window (' +
            ($enter.Output.Trim()) + '). If an earlier update crashed, run the update again with -Takeover.')
    }
    $S.Phase = 'backup'
    $backupDir = Get-MtcBackupDir -Settings $settings -Root $S.Root
    $S.BackupFile = Join-Path $backupDir ('pre-update-' + (New-MtcStamp) + '.sqlite3')
    Invoke-MtcManage -Release $S.OldRelease -Arguments @('backup_db', '--dest', $S.BackupFile, '--quiesce',
        '--owner', $S.Owner, '--token', $S.Token) -FailMessage 'The pre-update backup failed' | Out-Null
    Write-MtcOk ('Backup saved: ' + $S.BackupFile)

    $S.Phase = 'switch'
    Write-MtcStep 'Stopping the app'
    Stop-MtcAppTask -Root $S.Root
    Write-MtcStep ('Updating the database for ' + $new.Version)
    $S.Migrated = $true
    Invoke-MtcManage -Release $S.NewRelease -Arguments @('migrate', '--noinput') -FailMessage 'Database migration failed' | Out-Null
    Write-MtcStep ('Switching to ' + $new.Version + ' and starting it')
    Set-MtcPreviousRelease -Root $S.Root -Release $S.OldRelease
    $S.Switched = $true
    Set-MtcCurrentRelease -Root $S.Root -Release $S.NewRelease
    Start-MtcAppTask
    $ready = Wait-MtcReady -ExpectedVersion $new.Version -TimeoutSeconds 180
    if (-not $ready.Ok) {
        $seen = $ready.Version
        if (-not $seen) { $seen = 'none' }
        throw ('Version ' + $new.Version + ' did not become ready (' + $ready.Reason + ', serving version: ' + $seen + ').')
    }
    Write-MtcOk ('Version ' + $ready.Version + ' is serving')
    $S.Phase = 'done'

    $exit = Invoke-MtcManage -Release $S.NewRelease -Arguments @('maintenance', '--exit', '--token', $S.Token) -AllowFailure -Quiet
    if ($exit.ExitCode -ne 0) {
        $exitCode = 2
        Write-MtcFail 'The update worked, but saving is still paused (the maintenance window could not be released).'
        Write-Host ('    Release it with: ' + $S.Root + '\mtc.cmd maintenance --exit --token ' + $S.Token)
    } else {
        Write-MtcOk 'Saving re-enabled for staff'
    }
    Copy-MtcLauncher -Root $S.Root -Release $S.NewRelease

    Write-MtcStep 'Tidying up old releases (keeping the newest three)'
    $all = @(Get-ChildItem -LiteralPath (Join-Path $S.Root 'releases') -Directory | Where-Object { $_.Name -notlike '.*' })
    foreach ($old in (Select-MtcReleasesToPrune -Releases $all -Pinned @($S.NewRelease, $S.OldRelease) -KeepCount $MtcKeepReleases)) {
        try {
            Remove-MtcTree -Path $old.FullName -Root $S.Root
            Write-MtcInfo ('Removed ' + $old.Name)
        } catch {
            Write-MtcWarn ('Could not remove ' + $old.Name + ': ' + $_.Exception.Message)
        }
    }
    Write-Host ''
    Write-Host ('Update complete: ' + $S.OldVersion + ' -> ' + $new.Version + '.') -ForegroundColor Green
    Write-Host ('Pre-update backup: ' + $S.BackupFile)
    Write-Host ('To go back to ' + $S.OldVersion + ' (this also restores that backup): ' + $S.Root + '\mtc.cmd rollback')
    Write-MtcLog 'update.ps1 finished OK'
} catch {
    $message = $_.Exception.Message
    Write-Host ''
    Write-MtcFail $message
    if ($S.Phase -eq 'switch') {
        Write-Host '    UPDATE FAILED after the backup. Rolling back automatically ...' -ForegroundColor Yellow
        $exitCode = Invoke-MtcUpdateRollback -State $S
        if ($exitCode -eq 1) {
            Write-Host ''
            Write-Host ('UPDATE FAILED, AND WAS ROLLED BACK: version ' + $S.OldVersion + ' is running again with the') -ForegroundColor Yellow
            Write-Host 'data exactly as it was before the update (saving was paused, so nothing was lost).' -ForegroundColor Yellow
            Write-Host ('Reason: ' + $message)
        } else {
            Write-Host ''
            Write-Host 'UPDATE FAILED AND THE AUTOMATIC ROLLBACK DID NOT FINISH. See WHAT TO DO NOW above.' -ForegroundColor Red
        }
    } elseif ($S.Phase -eq 'backup') {
        $exitCode = 1
        $exit = $null
        try {
            $exit = Invoke-MtcManage -Release $S.OldRelease -Arguments @('maintenance', '--exit', '--token', $S.Token) -AllowFailure -Quiet
        } catch {
            $exit = $null
        }
        if ($null -ne $exit -and $exit.ExitCode -eq 0) {
            Write-Host '    Update cancelled before anything changed; saving is enabled again.' -ForegroundColor Yellow
        } else {
            $exitCode = 2
            Write-Host ('    Update cancelled, but saving is still paused. Release it with: ' + $S.Root +
                '\mtc.cmd maintenance --exit --token ' + $S.Token) -ForegroundColor Red
        }
    } elseif ($S.Phase -eq 'done') {
        Write-Host ('    The update to ' + $S.NewVersion + ' itself succeeded; only a tidy-up step failed.') -ForegroundColor Yellow
    } else {
        $exitCode = 1
        Write-Host '    Update cancelled; the running version and its data were not touched.' -ForegroundColor Yellow
    }
    if ($S.Phase -eq 'prepare' -or $S.Phase -eq 'backup') {
        if ($S.NewRelease -and (Test-Path -LiteralPath $S.NewRelease)) {
            try { Remove-MtcTree -Path $S.NewRelease -Root $S.Root } catch { Write-MtcWarn ('Could not remove ' + $S.NewRelease) }
        }
    }
    if ($MtcLogFile) { Write-Host ('    Details: ' + $MtcLogFile) }
} finally {
    if ($S.Staging -and (Test-Path -LiteralPath $S.Staging)) {
        try { Remove-MtcTree -Path $S.Staging -Root $S.Root } catch { Write-MtcWarn ('Could not remove ' + $S.Staging) }
    }
}
exit $exitCode
