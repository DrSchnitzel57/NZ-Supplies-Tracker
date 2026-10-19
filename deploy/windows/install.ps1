<#
.SYNOPSIS
    Installs the MTC Supplies Tracker on this Windows PC (no Docker needed).

.DESCRIPTION
    Double-click install.cmd instead of running this directly; it asks for
    administrator rights and starts this script.

    Creates <Root> (default C:\MTCSupplies), downloads pinned and checksum-
    verified copies of uv (Python manager) and Caddy (HTTPS), installs
    Python 3.13 inside <Root>\tools, installs this release with its own
    Python environment, writes settings.env (random secret key), prepares the
    database, creates the first administrator, sets up HTTPS for the LAN,
    registers three scheduled tasks (app, HTTPS, daily backup), opens the
    firewall port on private/domain networks and checks that everything
    answers.

    Re-running with -Repair redoes the Windows set-up for an existing install
    (tools, address, Caddyfile, tasks, firewall, certificate) without
    replacing the release or the data. Use it after the PC's address changes:
        install.ps1 -Repair -HostAddress 192.168.1.20

.PARAMETER Root
    Install folder. Default C:\MTCSupplies.
.PARAMETER Port
    HTTPS port staff connect to. Default 8443.
.PARAMETER HostAddress
    IP address or computer name staff use. Default: detected, then confirmed.
.PARAMETER ReleaseZip
    Release zip or unzipped folder to install. Default: the release this
    script belongs to.
.PARAMETER AdminUsername
    Name for the first administrator (you are asked for a password).
.PARAMETER Repair
    Redo the Windows set-up of an existing install; keeps release and data.
.PARAMETER Yes
    Accept defaults instead of asking (the administrator account is then
    not created; use "mtc create-admin NAME" later).
.PARAMETER UseSystemCertificates
    Let uv trust the Windows certificate store (networks that inspect HTTPS).
#>
param(
    [string]$Root = 'C:\MTCSupplies',
    [int]$Port = 8443,
    [string]$HostAddress = '',
    [string]$ReleaseZip = '',
    [string]$AdminUsername = '',
    [switch]$Repair,
    [switch]$Yes,
    [switch]$UseSystemCertificates
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 1.0
. (Join-Path $PSScriptRoot 'common.ps1')

# ------------------------------------------------------- install-only helpers
function Protect-MtcRoot {
    # Administrators + SYSTEM only. The scheduled tasks run code from here as
    # SYSTEM, and data\ holds the database, backups and the secret key, so
    # ordinary users must not be able to read or change anything inside.
    # (The backup code skips POSIX 0600 checks on Windows; this ACL is the
    # equivalent control.) Children inherit it.
    param([string]$Path)
    $result = Invoke-MtcNative -FilePath 'icacls.exe' -ArgumentList @($Path, '/inheritance:r',
        '/grant:r', '*S-1-5-32-544:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F', '/Q') -AllowFailure -Quiet
    if ($result.ExitCode -ne 0) { throw "Could not restrict access to $Path (icacls exit code $($result.ExitCode))." }
}

function Get-MtcLanCandidates {
    $routes = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue)
    $list = @()
    $addresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { [string]$_.AddressState -eq 'Preferred' })
    foreach ($ip in $addresses) {
        $metric = $null
        $route = $routes | Where-Object { $_.InterfaceIndex -eq $ip.InterfaceIndex } |
            Sort-Object -Property RouteMetric | Select-Object -First 1
        if ($route) {
            $metric = [int]$route.RouteMetric
            try {
                $iface = Get-NetIPInterface -InterfaceIndex $ip.InterfaceIndex -AddressFamily IPv4 -ErrorAction Stop |
                    Select-Object -First 1
                $metric += [int]$iface.InterfaceMetric
            } catch {
                # Route metric alone is good enough to rank.
            }
        }
        $list += [pscustomobject]@{
            IPAddress = [string]$ip.IPAddress; InterfaceIndex = $ip.InterfaceIndex
            PrefixOrigin = [string]$ip.PrefixOrigin; RouteMetric = $metric; InterfaceAlias = [string]$ip.InterfaceAlias
        }
    }
    return $list
}

function Confirm-MtcHostAddress {
    param([string]$Suggested, [int]$Port, [switch]$Assume)
    if ($Assume -and $Suggested) { return $Suggested }
    if ($Assume) { throw 'Could not detect this PC''s LAN address; run again with -HostAddress <IP address>.' }
    while ($true) {
        if ($Suggested) {
            Write-Host ''
            Write-Host ('    Staff will open this address:  https://' + $Suggested + ':' + $Port) -ForegroundColor White
            $answer = Read-Host '    Press Enter if that is right, or type the IP address / computer name staff should use'
        } else {
            $answer = Read-Host '    Type the IP address (or computer name) staff should use to reach this PC'
        }
        if ($null -eq $answer) { $answer = '' }
        $answer = $answer.Trim()
        if (-not $answer -and $Suggested) { return $Suggested }
        if (Test-MtcHostAddress $answer) { return $answer }
        Write-MtcWarn 'That is not a valid IP address or computer name; please try again.'
    }
}

function New-MtcKeepAliveTrigger {
    # Every minute, forever: starts the task again if it has stopped (the
    # task's "run only one instance" setting ignores the run otherwise).
    # "Restart on failure" alone does not catch a program that exits.
    $start = (Get-Date).AddMinutes(1)
    try {
        return (New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes 1))
    } catch {
        return (New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes 1) `
                -RepetitionDuration (New-TimeSpan -Days 3650))
    }
}

function Register-MtcTasks {
    param([string]$Root)
    $current = Join-Path $Root 'current'
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    # Long-running services: no time limit, normal priority (Task Scheduler
    # defaults to below-normal), one instance, restart on failure.
    $serviceSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
        -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 999 -Priority 4

    $python = Join-Path $current '.venv\Scripts\python.exe'
    $serve = Join-Path $current 'deploy\windows\serve.py'
    $appArgs = '"{0}" --env-file "{1}" --log-file "{2}"' -f $serve, (Join-Path $Root 'settings.env'),
        (Join-Path $Root 'data\logs\app.log')
    $appAction = New-ScheduledTaskAction -Execute $python -Argument $appArgs -WorkingDirectory $current
    Register-ScheduledTask -TaskName $MtcAppTaskName -Action $appAction `
        -Trigger @((New-ScheduledTaskTrigger -AtStartup), (New-MtcKeepAliveTrigger)) `
        -Principal $principal -Settings $serviceSettings -Force `
        -Description 'MTC Supplies Tracker web app (Waitress on 127.0.0.1:8000). Managed by install/update scripts.' | Out-Null

    $cmd = Join-Path $env:SystemRoot 'System32\cmd.exe'
    $wrapper = Join-Path $current 'deploy\windows\run-caddy.cmd'
    $caddyAction = New-ScheduledTaskAction -Execute $cmd -Argument ('/d /c ""{0}" "{1}""' -f $wrapper, $Root) -WorkingDirectory $Root
    Register-ScheduledTask -TaskName $MtcHttpsTaskName -Action $caddyAction `
        -Trigger @((New-ScheduledTaskTrigger -AtStartup), (New-MtcKeepAliveTrigger)) `
        -Principal $principal -Settings $serviceSettings -Force `
        -Description 'MTC Supplies Tracker LAN HTTPS (Caddy). Managed by install/update scripts.' | Out-Null

    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $backupScript = Join-Path $current 'deploy\windows\backup.ps1'
    $backupArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Root "{1}"' -f $backupScript, $Root
    $backupAction = New-ScheduledTaskAction -Execute $powershell -Argument $backupArgs -WorkingDirectory $Root
    $backupSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew `
        -RestartInterval (New-TimeSpan -Minutes 15) -RestartCount 3
    Register-ScheduledTask -TaskName $MtcBackupTaskName -Action $backupAction `
        -Trigger (New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours(2))) `
        -Principal $principal -Settings $backupSettings -Force `
        -Description 'MTC Supplies Tracker daily verified backup + 30-day pruning (data\backups).' | Out-Null
}

function Set-MtcFirewallRule {
    param([string]$Root, [int]$Port)
    Get-NetFirewallRule -Name $MtcFirewallRuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -Name $MtcFirewallRuleName -DisplayName ("MTC Supplies Tracker (HTTPS $Port)") `
        -Description 'Lets staff PCs on private/domain networks reach the MTC Supplies Tracker.' `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Domain, Private `
        -Program (Join-Path $Root 'tools\caddy.exe') | Out-Null
}

function Test-MtcNetworkCategory {
    # The firewall rule covers private/domain networks only; warn (and offer
    # a fix) when the LAN adapter is marked Public.
    param([string]$HostAddress, [switch]$Assume)
    if ($HostAddress -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { return }
    $ip = Get-NetIPAddress -IPAddress $HostAddress -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $ip) {
        Write-MtcWarn "$HostAddress is not an address of this PC right now; staff cannot connect until it is."
        return
    }
    if ([string]$ip.PrefixOrigin -eq 'Dhcp') {
        Write-MtcWarn ("$HostAddress was handed out automatically (DHCP) and may change. Ask IT to reserve it " +
            'for this PC. If it ever changes, run: install.ps1 -Repair -HostAddress <new address>')
    }
    $connection = Get-NetConnectionProfile -InterfaceIndex $ip.InterfaceIndex -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $connection -and [string]$connection.NetworkCategory -eq 'Public') {
        Write-MtcWarn ("The network '" + $connection.Name + "' is set to Public, so staff PCs are blocked.")
        if (Read-MtcYesNo -Question 'Mark this network as Private (recommended for the office LAN)?' -Default $true -Assume:$Assume) {
            Set-NetConnectionProfile -InterfaceIndex $ip.InterfaceIndex -NetworkCategory Private
            Write-MtcOk 'Network set to Private'
        }
    }
}

function Test-MtcHttps {
    # Best-effort check that Caddy answers HTTPS on the staff address.
    param([string]$Root, [string]$HostAddress, [int]$Port)
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        if (Test-MtcPortOpen -HostName $HostAddress -Port $Port -TimeoutMs 2000) { break }
        Start-Sleep -Seconds 2
    }
    if (-not (Test-MtcPortOpen -HostName $HostAddress -Port $Port -TimeoutMs 2000)) {
        Write-MtcWarn "Nothing answers on $($HostAddress):$Port yet; see $Root\data\logs\caddy.log"
        return
    }
    $curl = Join-Path $env:SystemRoot 'System32\curl.exe'
    if (-not (Test-Path -LiteralPath $curl -PathType Leaf)) {
        Write-MtcOk "HTTPS port $Port is open"
        return
    }
    # -k: this PC may not trust the new certificate yet; this only checks
    # that Caddy forwards to the app.
    $url = 'https://' + $HostAddress + ':' + $Port + '/readyz/'
    for ($i = 0; $i -lt 15; $i++) {
        $probe = Invoke-MtcNative -FilePath $curl -ArgumentList @('-k', '-s', '-m', '5', $url) -AllowFailure -Quiet
        if ($probe.Output -match '"ok":\s*true') {
            Write-MtcOk "HTTPS answers at https://$($HostAddress):$Port"
            return
        }
        Start-Sleep -Seconds 2
    }
    Write-MtcWarn "HTTPS did not answer correctly yet; see $Root\data\logs\caddy.log"
}

# ------------------------------------------------------------------- main
$staging = $null
$exitCode = 0
try {
    Initialize-MtcConsole
    Assert-MtcAdmin
    if (-not [Environment]::Is64BitOperatingSystem) { throw '64-bit Windows is required.' }
    Write-Host ''
    Write-Host 'MTC Supplies Tracker - Windows installer' -ForegroundColor White

    $Root = ConvertTo-MtcFullPath $Root
    $problem = Get-MtcRootPathProblem $Root
    if ($problem) { throw "Install folder '$Root' cannot be used: $problem." }
    if ($Port -lt 1 -or $Port -gt 65535 -or $Port -eq $MtcAppPort) {
        throw "Port $Port cannot be used (choose 1-65535, other than $MtcAppPort)."
    }
    if ($HostAddress -and -not (Test-MtcHostAddress $HostAddress)) {
        throw "'$HostAddress' is not a valid IP address or computer name."
    }

    $settingsPath = Join-Path $Root 'settings.env'
    $installed = $null
    if (Test-Path -LiteralPath $Root) { $installed = Get-MtcCurrentRelease -Root $Root }
    if ($installed -and -not $Repair) {
        if (Get-MtcTask -Name $MtcAppTaskName) {
            throw ("MTC Supplies is already installed in $Root. To install a newer version run update " +
                "($Root\mtc.cmd update <release.zip>); to redo the Windows set-up run install.ps1 -Repair.")
        }
        # The release was copied but set-up stopped part-way: finish it.
        Write-MtcWarn 'A previous install did not finish; continuing it with the release it already copied.'
        $Repair = [switch]$true
    }
    if ($Repair -and -not $installed) { throw "There is no install in $Root to repair." }
    if (-not $Repair -and (Test-Path -LiteralPath $Root)) {
        # Access to the install folder is locked down below, so never take
        # over a folder that holds anything other than an earlier install.
        $ours = @('releases', 'tools', 'data', 'settings.env', 'Caddyfile', 'mtc.cmd', 'previous.txt', 'MTC-Supplies-CA.crt')
        $foreign = @(Get-ChildItem -LiteralPath $Root -Force -ErrorAction SilentlyContinue | Where-Object { $ours -notcontains $_.Name })
        if ($foreign.Count -gt 0) {
            throw ("$Root already contains other files (for example " + $foreign[0].Name +
                '). Choose a new, empty folder with -Root.')
        }
    }

    Write-MtcStep "Preparing $Root"
    New-Item -ItemType Directory -Path $Root -Force | Out-Null
    Protect-MtcRoot -Path $Root
    foreach ($sub in @('releases', 'tools', 'data', 'data\backups', 'data\caddy', 'data\caddy-config',
            'data\logs', 'data\imports')) {
        New-Item -ItemType Directory -Path (Join-Path $Root $sub) -Force | Out-Null
    }
    Set-MtcLog -Path (Join-Path $Root 'data\logs\install.log')
    Write-MtcLog ("install.ps1 started (repair=" + [bool]$Repair + ")")
    Write-MtcOk 'Folders ready (access limited to Administrators and SYSTEM)'

    $release = $null
    if (-not $Repair) {
        Write-MtcStep 'Checking the release'
        if (-not $ReleaseZip) { $ReleaseZip = Join-Path $PSScriptRoot '..\..' }
        $source = ConvertTo-MtcFullPath $ReleaseZip
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            $staging = Join-Path $Root ('releases\.staging-' + (New-MtcStamp))
            Write-MtcInfo "Unpacking $source"
            Expand-Archive -LiteralPath $source -DestinationPath $staging -Force
            $source = $staging
        } elseif (-not (Test-Path -LiteralPath $source -PathType Container)) {
            throw "Release not found: $ReleaseZip"
        }
        $release = Get-MtcReleaseInfo -Path (Find-MtcReleaseRoot -Dir $source)
        Write-MtcOk ("Release " + $release.Version)
    }

    Write-MtcStep 'Getting the tools (uv and Caddy, pinned and checksum-verified)'
    if ($Repair) { Stop-MtcHttpsTask -Root $Root }
    Install-MtcTool -Root $Root -Name 'uv' -Version $MtcUvVersion -Url $MtcUvUrl -ChecksumUrl $MtcUvChecksumUrl `
        -Asset $MtcUvAsset -Algorithm $MtcUvAlgorithm -PinnedHash $MtcUvHash -ExeName 'uv.exe' -VersionArgs @('--version') | Out-Null
    $caddy = Install-MtcTool -Root $Root -Name 'caddy' -Version $MtcCaddyVersion -Url $MtcCaddyUrl `
        -ChecksumUrl $MtcCaddyChecksumUrl -Asset $MtcCaddyAsset -Algorithm $MtcCaddyAlgorithm `
        -PinnedHash $MtcCaddyHash -ExeName 'caddy.exe' -VersionArgs @('version')

    Write-MtcStep 'Installing Python 3.13 (inside the install folder)'
    Install-MtcPython -Root $Root -SystemCertificates:$UseSystemCertificates
    Write-MtcOk 'Python ready'

    Write-MtcStep 'Choosing the address staff will use'
    $savedHost = ''
    $savedPort = 0
    if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
        $saved = Read-MtcSettings -Path $settingsPath
        $savedHost = Get-MtcSetting $saved 'APP_HOST'
        $portText = Get-MtcSetting $saved 'APP_PORT'
        if ($portText -match '^\d+$') { $savedPort = [int]$portText }
    }
    if (-not $PSBoundParameters.ContainsKey('Port') -and $savedPort -gt 0) { $Port = $savedPort }
    if (-not $HostAddress) {
        if ($savedHost) {
            $HostAddress = $savedHost
        } else {
            $best = Select-MtcLanAddress -Candidates (Get-MtcLanCandidates)
            $suggested = ''
            if ($null -ne $best) { $suggested = $best.IPAddress }
            $HostAddress = Confirm-MtcHostAddress -Suggested $suggested -Port $Port -Assume:$Yes
        }
    }
    Write-MtcOk ("Staff address: https://" + $HostAddress + ':' + $Port)

    if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
        if (($savedHost -and $savedHost -ne $HostAddress) -or ($savedPort -gt 0 -and $savedPort -ne $Port)) {
            Write-MtcWarn ("settings.env uses https://" + $savedHost + ':' + $savedPort + '.')
            if (-not (Read-MtcYesNo -Question ("Change it to https://" + $HostAddress + ':' + $Port + '?') -Default $true -Assume:$Yes)) {
                throw 'Stopped without changes. Run again with the address you want.'
            }
            Set-MtcSettingValues -Path $settingsPath -Values @{
                'APP_HOST' = $HostAddress; 'APP_PORT' = [string]$Port; 'DJANGO_ALLOWED_HOSTS' = $HostAddress
                'DJANGO_CSRF_TRUSTED_ORIGINS' = ('https://' + $HostAddress + ':' + $Port)
            }
            Write-MtcOk 'settings.env updated (old copy saved next to it)'
        } else {
            Write-MtcOk 'Keeping the existing settings.env (secret key unchanged)'
        }
    } else {
        Write-MtcTextFile -Path $settingsPath -Text (New-MtcSettingsText -Root $Root -HostAddress $HostAddress `
                -Port $Port -SecretKey (New-MtcSecretKey))
        Write-MtcOk 'settings.env created with a new random secret key'
    }
    $settings = Import-MtcSettings -Path $settingsPath
    Assert-MtcSettingsUsable $settings

    if ($Repair) {
        $current = $installed
    } else {
        Write-MtcStep ("Installing release " + $release.Version + ' (this takes a few minutes)')
        $current = Join-Path $Root ('releases\' + $release.Version + '-' + (New-MtcStamp))
        Copy-MtcReleaseFiles -Source $release.Path -Destination $current
        Initialize-MtcVenv -Root $Root -Release $current -SystemCertificates:$UseSystemCertificates
        Invoke-MtcManage -Release $current -Arguments @('check') -Quiet -FailMessage 'The app self-check failed' | Out-Null
        Invoke-MtcManage -Release $current -Arguments @('collectstatic', '--noinput') -Quiet `
            -FailMessage 'Collecting the web page files failed' | Out-Null
        Set-MtcCurrentRelease -Root $Root -Release $current
        Write-MtcOk ("Release installed in " + $current)
    }
    Copy-MtcLauncher -Root $Root -Release $current
    $version = Get-MtcReleaseVersion -Release $current

    Write-MtcStep 'Preparing the database'
    $dbPath = Get-MtcSetting $settings 'DJANGO_DB_PATH' (Join-Path $Root 'data\db.sqlite3')
    if (-not $Repair -and (Test-Path -LiteralPath $dbPath -PathType Leaf)) {
        $backupDir = Get-MtcBackupDir -Settings $settings -Root $Root
        $dest = Join-Path $backupDir ('pre-install-' + (New-MtcStamp) + '.sqlite3')
        Write-MtcInfo 'An existing database was found; saving a verified copy first.'
        Invoke-MtcManage -Release $current -Arguments @('backup_db', '--dest', $dest, '--quiesce') `
            -FailMessage 'Backing up the existing database failed (it may come from a newer version)' | Out-Null
    }
    Invoke-MtcManage -Release $current -Arguments @('migrate', '--noinput') -Quiet `
        -FailMessage 'Preparing the database (migrate) failed' | Out-Null
    Write-MtcOk 'Database ready'

    Write-MtcStep 'Administrator account'
    $checkAdmin = 'import sys; from django.contrib.auth import get_user_model as g; ' +
        'sys.exit(0 if g().objects.filter(is_superuser=True, is_active=True).exists() else 3)'
    $adminCheck = Invoke-MtcManage -Release $current -Arguments @('shell', '-c', $checkAdmin) -AllowFailure -Quiet
    if ($adminCheck.ExitCode -eq 0) {
        Write-MtcOk 'An administrator account already exists'
    } elseif ($Yes -and -not $AdminUsername) {
        Write-MtcWarn ("No administrator yet. Create one with: " + $Root + '\mtc.cmd create-admin <name>')
    } else {
        $name = $AdminUsername
        while (-not (Test-MtcUsername $name)) {
            if ($name) { Write-MtcWarn 'Use letters, digits and . @ + - _ only (no spaces).' }
            $name = Read-Host '    Choose a username for the first administrator (for example: admin)'
            if ($null -eq $name) { $name = '' }
            $name = $name.Trim()
        }
        Write-MtcInfo 'Type a temporary password twice (nothing shows while typing). It must be changed at first sign-in.'
        $created = $false
        for ($attempt = 1; $attempt -le 3 -and -not $created; $attempt++) {
            Invoke-MtcManageInteractive -Release $current -Arguments @('recover_admin', '--username', $name, '--create')
            $created = ($LASTEXITCODE -eq 0)
            if (-not $created) { Write-MtcWarn 'That did not work (passwords must match and not be empty). Try again.' }
        }
        if (-not $created) {
            Write-MtcWarn ("No administrator created. Later run: " + $Root + '\mtc.cmd create-admin ' + $name)
        } else {
            Write-MtcOk ("Administrator '" + $name + "' created")
        }
    }

    Write-MtcStep 'Setting up HTTPS (Caddy)'
    $caddyfile = Join-Path $Root 'Caddyfile'
    Write-MtcTextFile -Path $caddyfile -Text (New-MtcCaddyfileText -Root $Root -HostAddress $HostAddress -Port $Port)
    $env:XDG_DATA_HOME = Join-Path $Root 'data\caddy'
    $env:XDG_CONFIG_HOME = Join-Path $Root 'data\caddy-config'
    Invoke-MtcNative -FilePath $caddy -ArgumentList @('validate', '--config', $caddyfile, '--adapter', 'caddyfile') `
        -Quiet -FailMessage 'The generated Caddyfile was rejected by Caddy' | Out-Null
    Write-MtcOk 'Caddyfile written and validated'

    Write-MtcStep 'Registering the scheduled tasks (start automatically with Windows)'
    if ($Repair) {
        Stop-MtcAppTask -Root $Root
        Stop-MtcHttpsTask -Root $Root
    }
    Register-MtcTasks -Root $Root
    Write-MtcOk ("Tasks: '" + $MtcAppTaskName + "', '" + $MtcHttpsTaskName + "', '" + $MtcBackupTaskName + "' (02:00 daily)")

    Write-MtcStep 'Opening the firewall for staff PCs'
    Set-MtcFirewallRule -Root $Root -Port $Port
    Write-MtcOk ("Inbound TCP $Port allowed on private and domain networks (not public)")
    Test-MtcNetworkCategory -HostAddress $HostAddress -Assume:$Yes

    Write-MtcStep 'Starting the app'
    Start-MtcAppTask
    Start-MtcHttpsTask
    $ready = Wait-MtcReady -ExpectedVersion $version -TimeoutSeconds 180
    if (-not $ready.Ok) {
        throw ("The app did not become ready (" + $ready.Reason + "). Look at " + $Root + '\data\logs\app.log')
    }
    Write-MtcOk ("App ready, version " + $ready.Version)
    Test-MtcHttps -Root $Root -HostAddress $HostAddress -Port $Port

    Write-MtcStep 'Exporting the HTTPS certificate for staff PCs'
    $caSource = Get-MtcCaRootPath -Root $Root
    $caCopy = Join-Path $Root 'MTC-Supplies-CA.crt'
    $deadline = (Get-Date).AddSeconds(60)
    while (-not (Test-Path -LiteralPath $caSource -PathType Leaf) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
    $thumbprint = ''
    if (Test-Path -LiteralPath $caSource -PathType Leaf) {
        Copy-Item -LiteralPath $caSource -Destination $caCopy -Force
        $thumbprint = Get-MtcCertificateThumbprint -Path $caCopy
        Write-MtcOk ("Saved " + $caCopy)
        Write-MtcInfo ("Certificate thumbprint: " + $thumbprint)
        $trusted = @(Get-ChildItem -Path Cert:\LocalMachine\Root -ErrorAction SilentlyContinue |
                Where-Object { $_.Thumbprint -eq $thumbprint })
        if ($trusted.Count -gt 0) {
            Write-MtcOk 'This PC already trusts it'
        } elseif (Read-MtcYesNo -Question 'Trust this certificate on THIS PC too (no browser warning here)?' -Default $true -Assume:$Yes) {
            Import-Certificate -FilePath $caCopy -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
            Write-MtcOk 'Trusted on this PC'
        }
    } else {
        Write-MtcWarn ("Caddy has not created its certificate yet. Later copy " + $caSource + ' to ' + $caCopy)
    }

    $url = 'https://' + $HostAddress + ':' + $Port
    $backupDir = Get-MtcBackupDir -Settings $settings -Root $Root
    Write-MtcStep 'Adding MTC Supplies to the Start menu, the desktop and Settings > Apps'
    $shortcutsOk = $false
    try {
        Install-MtcShortcuts -Root $Root -HostAddress $HostAddress -Port ([string]$Port) -Version $version -BackupDir $backupDir | Out-Null
        $shortcutsOk = $true
        Write-MtcOk 'Start menu > MTC Supplies: Open, Status, Start, Stop and Connection details'
    } catch {
        Write-MtcWarn ('Could not add the shortcuts (' + $_.Exception.Message + '). The app itself is fine.')
    }
    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Green
    Write-Host ' MTC Supplies Tracker is installed and running.' -ForegroundColor Green
    Write-Host '============================================================' -ForegroundColor Green
    Write-Host (' Staff address:   ' + $url)
    Write-Host (' Certificate:     ' + $url + '/ca.crt  (also saved as ' + $caCopy + ')')
    if ($thumbprint) { Write-Host (' Thumbprint:      ' + $thumbprint) }
    Write-Host '                  On each staff PC: open the .crt, Install Certificate, Local Machine,'
    Write-Host '                  "Trusted Root Certification Authorities". (Or ask IT to push it by Group Policy.)'
    Write-Host (' Backups:         ' + $backupDir + '  (daily at 02:00, kept 30 days)')
    Write-Host (' Logs:            ' + $Root + '\data\logs')
    if ($shortcutsOk) {
        Write-Host ' Start menu:      MTC Supplies > Open MTC Supplies, Status, Start, Stop' -ForegroundColor Green
        Write-Host '                  These details are saved there too: MTC Supplies > Connection details.' -ForegroundColor Green
    }
    Write-Host (' Admin commands:  ' + $Root + '\mtc.cmd help   (run in an administrator window)')
    Write-Host ' Bring in the original tracker: sign in, then Administration > Full data export & import.'
    Write-Host ''
    if (-not $Yes) {
        # explorer.exe hands the address to the default browser as the signed-in
        # user, not with this window's administrator rights.
        try { Start-Process -FilePath 'explorer.exe' -ArgumentList $url; Write-Host (' Opening ' + $url + ' in your browser now.') } catch { }
        Write-Host ''
    }
    Write-MtcLog 'install.ps1 finished OK'
} catch {
    $exitCode = 1
    Write-Host ''
    Write-MtcFail $_.Exception.Message
    if ($MtcLogFile) { Write-Host ('    Details are in ' + $MtcLogFile) }
    Write-Host '    Fix the problem above and run the installer again; it continues where it stopped.'
} finally {
    if ($staging -and (Test-Path -LiteralPath $staging)) {
        try { Remove-MtcTree -Path $staging -Root $Root } catch { Write-MtcWarn ("Could not remove " + $staging) }
    }
}
exit $exitCode
