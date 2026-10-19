<#
.SYNOPSIS
    Administrator commands for the MTC Supplies Tracker on this PC.

.DESCRIPTION
    Normally run through <root>\mtc.cmd from an administrator window:

      mtc status                      Tasks, readiness, version, last backup
      mtc start                       Start the app and HTTPS for everyone
      mtc stop                        Stop them until "mtc start" (survives a reboot)
      mtc update-online               Download the newest version from GitHub and update
      mtc create-admin NAME           Create an administrator (asks for a temporary password)
      mtc recover-admin NAME          Reset an administrator's password (temporary, must change)
      mtc import-original "FILE.html" -Username ADMIN
                                      Load the original tracker's data (empty app only)
      mtc import-bundle FILE.zip -Username ADMIN
                                      Load a full bundle exported from the app (empty app only)
      mtc backup                      Take a verified backup now (data\backups\manual-*)
      mtc restore BACKUP.sqlite3      Put a backup back (asks you to type RESTORE)
      mtc update RELEASE.zip          Update, with automatic rollback on failure
      mtc rollback                    Back to the previous version and its pre-update data
      mtc restart                     Restart the app and HTTPS
      mtc logs                        Show the end of the app, HTTPS and backup logs
      mtc repair [-HostAddress IP] [-Port N]
                                      Redo the Windows set-up (tasks, firewall, certificate, address)
      mtc uninstall [-DeleteData]     Remove tasks/firewall/program (data kept unless -DeleteData)
      mtc <anything else>             python manage.py <anything else>, e.g.
                                      mtc maintenance --check

    Common options: -Root <folder> (if not C:\MTCSupplies), -Takeover, -Yes.
    The Python commands get settings.env loaded into their environment and
    run with the active release's own Python.
#>

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 1.0
. (Join-Path $PSScriptRoot 'common.ps1')

function Show-MtcUsage {
    $help = Get-Help -Name (Join-Path $PSScriptRoot 'manage.ps1') -Full -ErrorAction SilentlyContinue
    if ($help -and $help.description) {
        $help.description | ForEach-Object { Write-Host $_.Text }
    } else {
        Write-Host 'Usage: mtc status | start | stop | update-online | create-admin NAME | import-original FILE -Username ADMIN | backup | restore FILE | update ZIP | rollback | restart | logs | repair | uninstall | <manage.py command>'
    }
}

function Show-MtcStatus {
    param([string]$Root, $Settings)
    $current = Get-MtcCurrentRelease -Root $Root
    $previous = Get-MtcPreviousRelease -Root $Root
    $hostAddress = Get-MtcSetting $Settings 'APP_HOST' '?'
    $port = Get-MtcSetting $Settings 'APP_PORT' '?'
    Write-Host ''
    Write-Host 'MTC Supplies Tracker - status' -ForegroundColor White
    Write-Host ('  Install folder : ' + $Root)
    Write-Host ('  Staff address  : https://' + $hostAddress + ':' + $port)
    Write-Host ('  Running release: ' + (Get-MtcReleaseVersion -Release $current) + '  (' + $current + ')')
    if ($previous) {
        Write-Host ('  Previous       : ' + (Get-MtcReleaseVersion -Release $previous) + '  (mtc rollback goes back to it)')
    } else {
        Write-Host '  Previous       : none'
    }
    foreach ($name in @($MtcAppTaskName, $MtcHttpsTaskName, $MtcBackupTaskName)) {
        $task = Get-MtcTask -Name $name
        if ($null -eq $task) {
            Write-Host ('  ' + $name.PadRight(28) + ': MISSING (run: mtc repair)') -ForegroundColor Red
            continue
        }
        $info = Get-ScheduledTaskInfo -TaskName $name
        $last = 'never'
        if ($info.LastRunTime -and $info.LastRunTime.Year -gt 2000) { $last = $info.LastRunTime.ToString('yyyy-MM-dd HH:mm') }
        $line = '  {0}: {1}, last run {2} ({3})' -f $name.PadRight(28), $task.State, $last, (Format-MtcTaskResult ([long]$info.LastTaskResult))
        $color = 'Gray'
        if ([string]$task.State -eq 'Disabled') { $color = 'Red' }
        Write-Host $line -ForegroundColor $color
    }
    $appTask = Get-MtcTask -Name $MtcAppTaskName
    if ($null -ne $appTask -and [string]$appTask.State -eq 'Disabled') {
        Write-Host '  Stopped on purpose. Start it again with: mtc start' -ForegroundColor Yellow
    }
    $ready = Get-MtcReadiness
    if ($ready.Ok) {
        $maint = 'saving enabled'
        if ($ready.Maintenance) { $maint = 'SAVING PAUSED (maintenance window held; see: mtc maintenance --check)' }
        Write-Host ('  Readiness      : OK, version ' + $ready.Version + ', ' + $maint) -ForegroundColor Green
    } elseif ($ready.Reachable) {
        Write-Host ('  Readiness      : NOT READY (' + $ready.Reason + ')') -ForegroundColor Red
    } else {
        Write-Host '  Readiness      : app not answering on 127.0.0.1:8000' -ForegroundColor Red
    }
    $backupDir = Get-MtcBackupDir -Settings $Settings -Root $Root
    $files = @(Get-ChildItem -LiteralPath $backupDir -Filter '*.sqlite3' -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notlike '*.tmp-*' } | Sort-Object -Property LastWriteTime -Descending)
    if ($files.Count -gt 0) {
        $newest = $files[0]
        $age = (Get-Date) - $newest.LastWriteTime
        $color = 'Gray'
        if ($age.TotalHours -gt 26) { $color = 'Yellow' }
        Write-Host ('  Last backup    : {0} ({1:N1} MB, {2:yyyy-MM-dd HH:mm})' -f $newest.Name, ($newest.Length / 1MB), $newest.LastWriteTime) -ForegroundColor $color
        $total = ($files | Measure-Object -Property Length -Sum).Sum
        Write-Host ('  Backups        : {0} files, {1:N1} MB in {2}' -f $files.Count, ($total / 1MB), $backupDir)
    } else {
        Write-Host ('  Last backup    : none yet in ' + $backupDir) -ForegroundColor Yellow
    }
    try {
        $drive = Get-PSDrive -Name $Root.Substring(0, 1) -ErrorAction Stop
        Write-Host ('  Free disk space: {0:N1} GB' -f ($drive.Free / 1GB))
    } catch {
        # Not important enough to fail status.
    }
    $ca = Join-Path $Root 'MTC-Supplies-CA.crt'
    if (Test-Path -LiteralPath $ca -PathType Leaf) {
        Write-Host ('  Certificate    : https://' + $hostAddress + ':' + $port + '/ca.crt (thumbprint ' + (Get-MtcCertificateThumbprint -Path $ca) + ')')
    }
    Write-Host ('  Logs           : ' + (Join-Path $Root 'data\logs'))
    Write-Host ''
}

function Show-MtcLogs {
    param([string]$Root)
    foreach ($name in @('app.log', 'caddy.log', 'backup.log', 'update.log')) {
        $path = Join-Path (Join-Path $Root 'data\logs') $name
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Write-Host ''
            Write-Host ('----- ' + $path + ' (last 25 lines)') -ForegroundColor Cyan
            Get-Content -LiteralPath $path -Tail 25 | ForEach-Object { Write-Host $_ }
        }
    }
}

function Restart-MtcServices {
    param([string]$Root)
    Write-MtcStep 'Restarting the app and HTTPS'
    Stop-MtcAppTask -Root $Root
    Stop-MtcHttpsTask -Root $Root
    Start-MtcAppTask
    Start-MtcHttpsTask
    $ready = Wait-MtcReady -TimeoutSeconds 120
    if (-not $ready.Ok) { throw ('The app did not become ready (' + $ready.Reason + '); see mtc logs') }
    Write-MtcOk ('App ready, version ' + $ready.Version)
}

function Start-MtcServices {
    param([string]$Root, $Settings)
    Write-MtcStep 'Starting the app and HTTPS'
    Start-MtcAppTask
    Start-MtcHttpsTask
    $ready = Wait-MtcReady -TimeoutSeconds 120
    if (-not $ready.Ok) { throw ('The app did not become ready (' + $ready.Reason + '); see mtc logs') }
    Write-MtcOk ('Running, version ' + $ready.Version + '. Staff address: ' +
        (Get-MtcStaffUrl -HostAddress (Get-MtcSetting $Settings 'APP_HOST' '?') -Port (Get-MtcSetting $Settings 'APP_PORT' '?')))
}

function Invoke-MtcOnlineUpdate {
    # "Update" in the tray icon (run as SYSTEM) or "mtc update-online":
    # fetch the newest version from the GitHub repository and run THAT
    # release's update.ps1 (backup first, automatic rollback on failure).
    param([string]$Root, $Settings, [string]$Current)
    $repo = Get-MtcSetting $Settings 'MTC_UPDATE_REPO' $MtcUpdateRepo
    $branch = Get-MtcSetting $Settings 'MTC_UPDATE_BRANCH' $MtcUpdateBranch
    $urls = Get-MtcUpdateUrls -Repo $repo -Branch $branch
    $log = Join-Path $Root 'data\logs\update-online.log'
    Set-MtcLog -Path $log
    $running = Get-MtcReleaseVersion -Release $Current
    Write-MtcStep ('Checking ' + $repo + ' (' + $branch + ') for a newer version than ' + $running)
    Write-MtcUpdateStatus -State 'checking' -Version $running -Message 'Checking for a newer version'
    Enable-MtcTls12
    try {
        $latest = ([string](Invoke-WebRequest -Uri $urls.Version -UseBasicParsing -TimeoutSec 60).Content).Trim()
    } catch {
        Write-MtcUpdateStatus -State 'failed' -Version $running -Message 'Could not reach GitHub. Nothing was changed.'
        throw ('Could not reach GitHub (' + $_.Exception.Message + '). Nothing was changed.')
    }
    if (-not (Test-MtcVersionString $latest)) {
        Write-MtcUpdateStatus -State 'failed' -Version $running -Message 'GitHub did not return a version number.'
        throw ('GitHub returned "' + $latest + '" instead of a version number. Nothing was changed.')
    }
    if ((Compare-MtcVersion $latest $running) -le 0) {
        Write-MtcUpdateStatus -State 'current' -Version $running -Message ('Already up to date (' + $running + ')')
        Write-MtcOk ('Already up to date (' + $running + ')')
        return 0
    }
    Write-MtcUpdateStatus -State 'running' -Version $latest -Message ('Updating to ' + $latest)
    $work = Join-Path $Root ('data\imports\online-' + $latest + '-' + (New-MtcStamp))
    $code = 1
    try {
        New-Item -ItemType Directory -Path $work -Force | Out-Null
        $zip = Join-Path $work 'release.zip'
        Write-MtcStep ('Downloading ' + $latest)
        Invoke-MtcDownload -Url $urls.Zip -OutFile $zip
        Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $work 'src') -Force
        $release = Find-MtcReleaseRoot -Dir (Join-Path $work 'src')
        $found = Get-MtcReleaseVersion -Release $release
        if ($found -ne $latest) { throw ('The download is version ' + $found + ', not ' + $latest + '. Try again in a minute.') }
        $script = Join-Path $release 'deploy\windows\update.ps1'
        $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        Write-MtcStep ('Running the ' + $latest + ' updater (details in ' + $log + ')')
        & $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $script -Root $Root -Yes *>> $log
        $code = $LASTEXITCODE
    } catch {
        Write-MtcUpdateStatus -State 'failed' -Version $running -Message ($_.Exception.Message)
        throw
    } finally {
        if (Test-Path -LiteralPath $work) {
            try { Remove-MtcTree -Path $work -Root $Root } catch { Write-MtcWarn ('Could not remove ' + $work) }
        }
    }
    if ($code -eq 0) {
        Write-MtcUpdateStatus -State 'done' -Version $latest -Message ('Updated to ' + $latest)
        Write-MtcOk ('Updated to ' + $latest)
    } else {
        Write-MtcUpdateStatus -State 'failed' -Version $running -Message 'The update did not finish; the previous version was kept or put back.'
        Write-MtcFail ('The update did not finish (exit code ' + $code + '); see ' + $log)
    }
    return $code
}

function Stop-MtcServices {
    param([string]$Root)
    Write-MtcStep 'Stopping the app and HTTPS for everyone'
    Stop-MtcAppTask -Root $Root
    if (Get-MtcTask -Name $MtcHttpsTaskName) { Disable-ScheduledTask -TaskName $MtcHttpsTaskName | Out-Null }
    Stop-MtcHttpsTask -Root $Root
    Write-MtcOk 'Stopped. It stays stopped, even after this PC restarts, until you start it again.'
    Write-Host '    Start it again with: mtc start. The nightly backup still runs.'
}

$exitCode = 0
try {
    Initialize-MtcConsole
    $parsed = Split-MtcArguments -Raw $args -ValueOptions @('Root', 'Username', 'HostAddress', 'Port', 'BackupFile') `
        -SwitchOptions @('Takeover', 'Yes', 'Reinstall', 'DeleteData', 'UseSystemCertificates')
    $opts = $parsed.Options
    $rest = @($parsed.Rest)
    $verb = ''
    if ($rest.Count -gt 0) { $verb = $rest[0].ToLowerInvariant() }
    $verbArgs = @()
    if ($rest.Count -gt 1) { $verbArgs = @($rest[1..($rest.Count - 1)]) }
    if (-not $verb -or $verb -eq 'help' -or $verb -eq '-?' -or $verb -eq '/?') {
        Show-MtcUsage
        exit 0
    }

    Assert-MtcAdmin
    $rootOption = ''
    if ($opts.ContainsKey('Root')) { $rootOption = $opts['Root'] }
    $Root = Resolve-MtcRoot -Root $rootOption -ScriptDir $PSScriptRoot
    $current = Assert-MtcInstalled -Root $Root
    $settings = Import-MtcSettings -Path (Join-Path $Root 'settings.env')

    switch ($verb) {
        'status' {
            Show-MtcStatus -Root $Root -Settings $settings
        }
        'logs' {
            Show-MtcLogs -Root $Root
        }
        'restart' {
            Restart-MtcServices -Root $Root
        }
        'start' {
            Start-MtcServices -Root $Root -Settings $settings
        }
        'stop' {
            Stop-MtcServices -Root $Root
        }
        'update-online' {
            $exitCode = Invoke-MtcOnlineUpdate -Root $Root -Settings $settings -Current $current
        }
        { $_ -eq 'create-admin' -or $_ -eq 'recover-admin' } {
            if ($verbArgs.Count -lt 1) { throw "Usage: mtc $verb NAME" }
            if (-not (Test-MtcUsername $verbArgs[0])) { throw 'Usernames use letters, digits and . @ + - _ only (no spaces).' }
            $manageArgs = @('recover_admin', '--username', $verbArgs[0])
            if ($verb -eq 'create-admin') { $manageArgs += '--create' }
            Write-Host 'Type a temporary password twice (nothing shows while typing); it must be changed at first sign-in.'
            Invoke-MtcManageInteractive -Release $current -Arguments $manageArgs
            if ($LASTEXITCODE -ne 0) { throw 'The account was not changed.' }
        }
        'import-original' {
            if ($verbArgs.Count -lt 1 -or -not $opts.ContainsKey('Username')) {
                throw 'Usage: mtc import-original "C:\path\MTC Missionary Supplies Tracker.html" -Username ADMIN'
            }
            $source = ConvertTo-MtcFullPath $verbArgs[0]
            if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "File not found: $source" }
            $bundle = Join-Path $Root 'data\imports\initial.zip'
            Write-MtcStep 'Converting the original tracker file (counts only are shown; no personal data)'
            Invoke-MtcManage -Release $current -Arguments @('seed_bundle', '--source', $source, '--output', $bundle) `
                -FailMessage 'Reading the original tracker file failed' | Out-Null
            Write-MtcStep 'Importing into the app (only works while the app holds no data)'
            Invoke-MtcManage -Release $current -Arguments @('import_bundle', $bundle, '--username', $opts['Username']) `
                -FailMessage 'The import failed (nothing was imported)' | Out-Null
            Write-MtcOk ('Imported. The converted bundle is kept at ' + $bundle + ' (private: contains personal data).')
            Write-Host '    Keep the original .html file somewhere private, or delete it if it is no longer needed.'
        }
        'import-bundle' {
            if ($verbArgs.Count -lt 1 -or -not $opts.ContainsKey('Username')) { throw 'Usage: mtc import-bundle FILE.zip -Username ADMIN' }
            Invoke-MtcManage -Release $current -Arguments @('import_bundle', (ConvertTo-MtcFullPath $verbArgs[0]), '--username',
                $opts['Username']) -FailMessage 'The import failed (nothing was imported)' | Out-Null
        }
        'backup' {
            $dest = Join-Path (Get-MtcBackupDir -Settings $settings -Root $Root) ('manual-' + (New-MtcStamp) + '.sqlite3')
            Invoke-MtcManage -Release $current -Arguments @('backup_db', '--dest', $dest, '--quiesce') -FailMessage 'The backup failed' | Out-Null
            Write-MtcOk ('Backup saved: ' + $dest)
        }
        'restore' {
            if ($verbArgs.Count -lt 1) { throw 'Usage: mtc restore BACKUP.sqlite3 [-Takeover] [-Yes]' }
            Set-MtcLog -Path (Join-Path $Root 'data\logs\restore.log')
            Set-Location -LiteralPath $Root
            [Environment]::CurrentDirectory = $Root
            Assert-MtcSettingsUsable $settings
            $backupDir = Get-MtcBackupDir -Settings $settings -Root $Root
            $result = Invoke-MtcRestoreFlow -Root $Root -CurrentRelease $current -TargetRelease $current `
                -BackupFile (ConvertTo-MtcFullPath $verbArgs[0]) -Owner 'restore' -SafetyPrefix 'pre-restore-' `
                -BackupDir $backupDir -Takeover:([bool]$opts['Takeover']) -Yes:([bool]$opts['Yes'])
            $exitCode = $result.Code
            if ($result.Code -eq 0) { Write-MtcOk $result.Message } else { Write-MtcFail $result.Message }
        }
        'update' {
            if ($verbArgs.Count -lt 1) { throw 'Usage: mtc update RELEASE.zip [-Takeover] [-Yes]' }
            $params = @{ Root = $Root; ReleaseZip = (ConvertTo-MtcFullPath $verbArgs[0]) }
            foreach ($switchName in @('Takeover', 'Reinstall', 'Yes', 'UseSystemCertificates')) {
                if ($opts.ContainsKey($switchName)) { $params[$switchName] = $true }
            }
            & (Join-Path $PSScriptRoot 'update.ps1') @params
            $exitCode = $LASTEXITCODE
        }
        'rollback' {
            $params = @{ Root = $Root }
            if ($opts.ContainsKey('BackupFile')) { $params['BackupFile'] = $opts['BackupFile'] }
            foreach ($switchName in @('Takeover', 'Yes')) { if ($opts.ContainsKey($switchName)) { $params[$switchName] = $true } }
            & (Join-Path $PSScriptRoot 'rollback.ps1') @params
            $exitCode = $LASTEXITCODE
        }
        'repair' {
            $params = @{ Root = $Root; Repair = $true }
            if ($opts.ContainsKey('HostAddress')) { $params['HostAddress'] = $opts['HostAddress'] }
            if ($opts.ContainsKey('Port')) { $params['Port'] = [int]$opts['Port'] }
            foreach ($switchName in @('Yes', 'UseSystemCertificates')) { if ($opts.ContainsKey($switchName)) { $params[$switchName] = $true } }
            & (Join-Path $PSScriptRoot 'install.ps1') @params
            $exitCode = $LASTEXITCODE
        }
        'uninstall' {
            # Run from a temporary copy: uninstalling removes this folder.
            $temp = Join-Path ([System.IO.Path]::GetTempPath()) ('mtc-uninstall-' + (New-MtcStamp))
            New-Item -ItemType Directory -Path $temp -Force | Out-Null
            Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'uninstall.ps1') -Destination $temp
            Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'common.ps1') -Destination $temp
            Set-Location -LiteralPath $temp
            [Environment]::CurrentDirectory = $temp
            $params = @{ Root = $Root }
            foreach ($switchName in @('DeleteData', 'Yes')) { if ($opts.ContainsKey($switchName)) { $params[$switchName] = $true } }
            & (Join-Path $temp 'uninstall.ps1') @params
            $exitCode = $LASTEXITCODE
        }
        default {
            # Anything else goes straight to manage.py (interactive, as typed).
            Invoke-MtcManageInteractive -Release $current -Arguments $rest
            $exitCode = $LASTEXITCODE
        }
    }
} catch {
    $exitCode = 1
    Write-MtcFail $_.Exception.Message
}
exit $exitCode
