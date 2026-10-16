# MTC Supplies Tracker - shared helpers for the native Windows scripts.
#
# Dot-sourced by install.ps1, update.ps1, rollback.ps1, backup.ps1,
# manage.ps1 and uninstall.ps1. Only definitions live here: dot-sourcing has
# no side effects, so tests/windows_ps_checks.ps1 can unit-test the pure
# helpers under PowerShell 7 on Linux.
#
# Must run on Windows PowerShell 5.1: no PowerShell-7-only syntax (no ?:,
# ??, ?., && or || chains, -Parallel), and this file stays pure ASCII
# (5.1 reads BOM-less scripts as ANSI).

# ---------------------------------------------------------------- constants
$MtcAppTaskName = 'MTC Supplies - App'
$MtcHttpsTaskName = 'MTC Supplies - HTTPS'
$MtcBackupTaskName = 'MTC Supplies - Daily backup'
$MtcFirewallRuleName = 'MTCSupplies-HTTPS'
$MtcAppPort = 8000
$MtcPythonVersion = '3.13'
$MtcWaitressPin = 'waitress==3.0.2'
$MtcKeepReleases = 3
$MtcBackupRetentionDays = 30
$MtcPlaceholderSecrets = @('replace-with-a-long-random-secret', 'change-me-in-production',
    'dev-insecure-secret-key-change-me')

# Pinned tools. Each download is checked against BOTH the hash pinned here
# and the checksum list published with the release; any mismatch aborts.
$MtcUvVersion = '0.12.20'
$MtcUvAsset = 'uv-x86_64-pc-windows-msvc.zip'
$MtcUvUrl = 'https://github.com/astral-sh/uv/releases/download/0.12.20/uv-x86_64-pc-windows-msvc.zip'
$MtcUvChecksumUrl = 'https://github.com/astral-sh/uv/releases/download/0.12.20/uv-x86_64-pc-windows-msvc.zip.sha256'
$MtcUvAlgorithm = 'SHA256'
$MtcUvHash = '95f9bc30fbb3574d276e28ac4a6de932d25153645853d13da8c21eec3bc88d06'

# Same Caddy release as the Docker deployment (caddy:2.10.2-alpine). Caddy
# publishes SHA-512 checksums, so that is what is verified.
$MtcCaddyVersion = '2.10.2'
$MtcCaddyAsset = 'caddy_2.10.2_windows_amd64.zip'
$MtcCaddyUrl = 'https://github.com/caddyserver/caddy/releases/download/v2.10.2/caddy_2.10.2_windows_amd64.zip'
$MtcCaddyChecksumUrl = 'https://github.com/caddyserver/caddy/releases/download/v2.10.2/caddy_2.10.2_checksums.txt'
$MtcCaddyAlgorithm = 'SHA512'
$MtcCaddyHash = '88347875f0cd4b5e26bb39cd1f359613f932d54158d560e03244004d1ba6e61aae0cd625ba7c913bd46df096ef973fef2249396b0bb81143414378cb4447aeb8'

# ------------------------------------------------------------------ output
$MtcLogFile = $null

function Set-MtcLog {
    param([string]$Path, [long]$RotateBytes = 5242880)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    try {
        if ((Test-Path -LiteralPath $Path) -and ((Get-Item -LiteralPath $Path).Length -gt $RotateBytes)) {
            Move-Item -LiteralPath $Path -Destination ($Path + '.1') -Force
        }
    } catch {
        # A log that cannot rotate must never stop the operation.
    }
    $script:MtcLogFile = $Path
}

function Write-MtcLog {
    param([string]$Message)
    if (-not $script:MtcLogFile) { return }
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    try {
        [System.IO.File]::AppendAllText($script:MtcLogFile, $line + "`r`n",
            (New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false))
    } catch {
        # Logging is best effort.
    }
}

function Write-MtcStep {
    param([string]$Message)
    Write-Host ''
    Write-Host ('==> ' + $Message) -ForegroundColor Cyan
    Write-MtcLog ('==> ' + $Message)
}

function Write-MtcInfo {
    param([string]$Message)
    Write-Host ('    ' + $Message)
    Write-MtcLog $Message
}

function Write-MtcOk {
    param([string]$Message)
    Write-Host ('    OK: ' + $Message) -ForegroundColor Green
    Write-MtcLog ('OK: ' + $Message)
}

function Write-MtcWarn {
    param([string]$Message)
    Write-Host ('    WARNING: ' + $Message) -ForegroundColor Yellow
    Write-MtcLog ('WARNING: ' + $Message)
}

function Write-MtcFail {
    param([string]$Message)
    Write-Host ('    PROBLEM: ' + $Message) -ForegroundColor Red
    Write-MtcLog ('PROBLEM: ' + $Message)
}

function Read-MtcYesNo {
    param([string]$Question, [bool]$Default = $true, [switch]$Assume)
    if ($Assume) { return $Default }
    $hint = '[y/N]'
    if ($Default) { $hint = '[Y/n]' }
    while ($true) {
        $answer = Read-Host ('    ' + $Question + ' ' + $hint)
        if ($null -eq $answer) { $answer = '' }
        $answer = $answer.Trim().ToLowerInvariant()
        if ($answer -eq '') { return $Default }
        if ($answer -eq 'y' -or $answer -eq 'yes') { return $true }
        if ($answer -eq 'n' -or $answer -eq 'no') { return $false }
    }
}

# ---------------------------------------------------------- environment
function Test-MtcAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object -TypeName Security.Principal.WindowsPrincipal -ArgumentList $identity
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-MtcAdmin {
    if (-not (Test-MtcAdmin)) {
        throw ('This needs administrator rights. Right-click PowerShell (or Command Prompt), ' +
            'choose "Run as administrator", and run it again.')
    }
}

function Enable-MtcTls12 {
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor
            [Net.SecurityProtocolType]::Tls12
    } catch {
        # Already enabled or not applicable (PowerShell 7).
    }
}

function Initialize-MtcConsole {
    # Python prints UTF-8; make PowerShell decode it as UTF-8 too.
    try {
        [Console]::OutputEncoding = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
    } catch {
        # No console (for example under Task Scheduler): nothing to set.
    }
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONUNBUFFERED = '1'
}

function ConvertTo-MtcFullPath {
    param([string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    if ($full.Length -gt 3) { $full = $full.TrimEnd('\', '/') }
    return $full
}

function Test-MtcPathUnder {
    # True when $Path is $Parent itself or inside it (case-insensitive).
    param([string]$Path, [string]$Parent)
    if (-not $Path -or -not $Parent) { return $false }
    $p = (ConvertTo-MtcFullPath $Path).Replace('/', '\').ToLowerInvariant()
    $q = (ConvertTo-MtcFullPath $Parent).Replace('/', '\').ToLowerInvariant().TrimEnd('\')
    if ($p -eq $q) { return $true }
    return $p.StartsWith($q + '\')
}

function Get-MtcRootPathProblem {
    # Returns $null when $Root is acceptable, otherwise a plain-English reason.
    # Restrictive on purpose: the path is used in cmd.exe, Task Scheduler
    # arguments, the Caddyfile and SQLite URIs.
    param([string]$Root)
    if (-not $Root) { return 'the install folder is empty' }
    if ($Root -notmatch '^[A-Za-z]:\\') { return 'the install folder must be a full local path such as C:\MTCSupplies' }
    if ($Root -match '^[A-Za-z]:\\?$') { return 'the install folder cannot be the top of a drive' }
    if ($Root -notmatch '^[A-Za-z]:\\[A-Za-z0-9 _.\-\\]+$') {
        return 'use only letters, digits, spaces, dots, dashes, underscores and backslashes in the install folder'
    }
    if ($Root -match '\\\.\.?(\\|$)' -or $Root -match '\\\\') { return 'the install folder must not contain "." or ".." parts' }
    return $null
}

function Test-MtcHostAddress {
    # IPv4 address or DNS-style host name (no scheme, no port).
    param([string]$HostAddress)
    if (-not $HostAddress) { return $false }
    if ($HostAddress -match '^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$') {
        foreach ($i in 1..4) { if ([int]$Matches[$i] -gt 255) { return $false } }
        return $true
    }
    return ($HostAddress -match '^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$')
}

function Test-MtcUsername {
    # Django's username rule: letters, digits and @ . + - _ (max 150).
    param([string]$Name)
    return ([bool]$Name -and $Name -cmatch '^[A-Za-z0-9@.+_-]{1,150}$')
}

function Select-MtcLanAddress {
    # Picks the address staff should use from candidate IPv4 addresses.
    # Each candidate: IPAddress, PrefixOrigin, RouteMetric ($null when the
    # interface has no default route). Prefers interfaces with a default
    # route (lowest metric); skips loopback and link-local (169.254.x.x).
    param([object[]]$Candidates)
    $usable = @($Candidates | Where-Object {
            $_ -and $_.IPAddress -and ($_.IPAddress -notmatch '^127\.') -and ($_.IPAddress -notmatch '^169\.254\.')
        })
    if ($usable.Count -eq 0) { return $null }
    $routed = @($usable | Where-Object { $null -ne $_.RouteMetric } | Sort-Object -Property RouteMetric)
    if ($routed.Count -gt 0) { return $routed[0] }
    return $usable[0]
}

# ----------------------------------------------------------- settings.env
function ConvertFrom-MtcSettingsText {
    # Same rules as parse_env_text() in serve.py: KEY=VALUE per line, BOM,
    # blank and '#' lines ignored, spaces/tabs trimmed, one pair of matching
    # surrounding quotes removed, no escapes, last occurrence wins. Errors
    # name the line number only, never the value (it may be the secret key).
    param([string]$Text)
    $values = [ordered]@{}
    if ($null -eq $Text) { return $values }
    if ($Text.Length -gt 0 -and [int][char]$Text[0] -eq 0xFEFF) { $Text = $Text.Substring(1) }
    $blanks = [char[]]@([char]' ', [char]"`t")
    $lines = ($Text.Replace("`r`n", "`n").Replace("`r", "`n")).Split([char]"`n")
    $number = 0
    foreach ($raw in $lines) {
        $number++
        $line = $raw.Trim($blanks)
        if ($line.Length -eq 0 -or $line.StartsWith('#')) { continue }
        $eq = $line.IndexOf('=')
        if ($eq -lt 0) { throw "settings file line $number is not KEY=VALUE" }
        $key = $line.Substring(0, $eq).Trim($blanks)
        if ($key -cnotmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw "settings file line $number is not KEY=VALUE" }
        $value = $line.Substring($eq + 1).Trim($blanks)
        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last = $value.Substring($value.Length - 1, 1)
            if (($first -ceq '"' -or $first -ceq "'") -and $first -ceq $last) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        $values[$key] = $value
    }
    return $values
}

function Read-MtcSettings {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "settings file not found: $Path"
    }
    $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    return ConvertFrom-MtcSettingsText -Text $text
}

function Import-MtcSettings {
    # Loads settings.env into this process's environment for the Python
    # commands started from it. Variables already set by something else win
    # (same rule as serve.py) with a warning; values this loader set earlier
    # in the same PowerShell window are refreshed from the file.
    param([string]$Path)
    $values = Read-MtcSettings -Path $Path
    $ours = @()
    if ($env:MTC_LOADED_SETTINGS) { $ours = @($env:MTC_LOADED_SETTINGS.Split(',')) }
    foreach ($key in @($values.Keys)) {
        $existing = [Environment]::GetEnvironmentVariable($key, 'Process')
        if ($null -ne $existing -and ($ours -notcontains $key)) {
            if ($existing -ne $values[$key]) {
                Write-MtcWarn ("$key is already set in this window's environment; that value is used instead of settings.env.")
            }
            continue
        }
        [Environment]::SetEnvironmentVariable($key, [string]$values[$key], 'Process')
        if ($ours -notcontains $key) { $ours += $key }
    }
    $env:MTC_LOADED_SETTINGS = ($ours -join ',')
    return $values
}

function Get-MtcSetting {
    param($Settings, [string]$Key, [string]$Default = '')
    if ($Settings.Contains($Key) -and $Settings[$Key]) { return [string]$Settings[$Key] }
    return $Default
}

function New-MtcSecretKey {
    # 32 random bytes from the OS CSPRNG as 64 lowercase hex characters.
    $bytes = New-Object -TypeName byte[] -ArgumentList 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
}

function New-MtcSettingsText {
    param([string]$Root, [string]$HostAddress, [int]$Port, [string]$SecretKey)
    $data = $Root + '\data'
    $lines = @(
        '# MTC Supplies Tracker - settings for this computer (KEY=VALUE, one per line).',
        ('# Created by install.ps1 on ' + (Get-Date -Format 'yyyy-MM-dd HH:mm') + '.'),
        '# PRIVATE: holds the secret key. Never email or share this file.',
        ('# After editing, restart the app:  ' + $Root + '\mtc.cmd restart'),
        'DJANGO_DEBUG=0',
        ('DJANGO_SECRET_KEY=' + $SecretKey),
        ('DJANGO_ALLOWED_HOSTS=' + $HostAddress),
        ('DJANGO_CSRF_TRUSTED_ORIGINS=https://' + $HostAddress + ':' + $Port),
        ('DJANGO_DB_PATH=' + $data + '\db.sqlite3'),
        ('MTC_BACKUP_DIR=' + $data + '\backups'),
        ('APP_HOST=' + $HostAddress),
        ('APP_PORT=' + $Port),
        '# APP_VERSION is read from each release''s VERSION file; do not set it here.'
    )
    return (($lines -join "`r`n") + "`r`n")
}

function Write-MtcTextFile {
    # UTF-8 without BOM (Caddy and Python read it), written via a temp file.
    param([string]$Path, [string]$Text)
    $tmp = $Path + '.tmp'
    [System.IO.File]::WriteAllText($tmp, $Text, (New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false))
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Set-MtcSettingValues {
    # Rewrites only the given keys in settings.env (keeps the secret key and
    # every other line), after saving a dated copy of the old file.
    param([string]$Path, [hashtable]$Values)
    $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    Copy-Item -LiteralPath $Path -Destination ($Path + '.bak-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) -Force
    $done = @{}
    $out = New-Object -TypeName System.Collections.Generic.List[string]
    foreach ($line in $text.Replace("`r`n", "`n").Split([char]"`n")) {
        $replaced = $false
        foreach ($key in $Values.Keys) {
            if ($line -match ('^\s*' + [regex]::Escape($key) + '\s*=')) {
                $out.Add($key + '=' + $Values[$key])
                $done[$key] = $true
                $replaced = $true
                break
            }
        }
        if (-not $replaced) { $out.Add($line) }
    }
    while ($out.Count -gt 0 -and $out[$out.Count - 1] -eq '') { $out.RemoveAt($out.Count - 1) }
    foreach ($key in $Values.Keys) { if (-not $done.ContainsKey($key)) { $out.Add($key + '=' + $Values[$key]) } }
    Write-MtcTextFile -Path $Path -Text ((($out.ToArray()) -join "`r`n") + "`r`n")
}

function Assert-MtcSettingsUsable {
    param($Settings)
    $secret = Get-MtcSetting $Settings 'DJANGO_SECRET_KEY'
    if (-not $secret -or $MtcPlaceholderSecrets -contains $secret) {
        throw 'settings.env has no real DJANGO_SECRET_KEY (it is empty or still a placeholder).'
    }
    if ((Get-MtcSetting $Settings 'APP_VERSION') -or $env:APP_VERSION) {
        throw ('APP_VERSION is set (in settings.env or the machine environment). Remove it: ' +
            'each release reports its own VERSION file, which updates and rollbacks rely on.')
    }
}

# --------------------------------------------------------- release folders
function Test-MtcVersionString {
    param([string]$Version)
    return ($Version -match '^[0-9]+\.[0-9]+\.[0-9]+$')
}

function Compare-MtcVersion {
    # -1 / 0 / 1 like CompareTo, for pinned X.Y.Z versions.
    param([string]$A, [string]$B)
    return ([version]$A).CompareTo([version]$B)
}

function Find-MtcReleaseRoot {
    # The folder itself, or the single sub-folder (typical of zip files)
    # that holds manage.py.
    param([string]$Dir)
    if (Test-Path -LiteralPath (Join-Path $Dir 'manage.py') -PathType Leaf) { return (ConvertTo-MtcFullPath $Dir) }
    $children = @(Get-ChildItem -LiteralPath $Dir -Directory -ErrorAction SilentlyContinue |
            Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'manage.py') -PathType Leaf })
    if ($children.Count -eq 1) { return (ConvertTo-MtcFullPath $children[0].FullName) }
    throw "This does not look like an MTC Supplies release (no manage.py found in $Dir)."
}

function Get-MtcReleaseInfo {
    # Validates an unpacked release and returns Path + Version.
    param([string]$Path)
    $missing = @()
    foreach ($rel in @('manage.py', 'requirements.prod.txt', 'VERSION', 'config\settings.py', 'deploy\windows\serve.py')) {
        $parts = $rel.Split('\')
        $candidate = $Path
        foreach ($part in $parts) { $candidate = Join-Path $candidate $part }
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { $missing += $rel }
    }
    if ($missing.Count -gt 0) {
        throw ("This is not a complete MTC Supplies release; missing: " + ($missing -join ', '))
    }
    $version = ([System.IO.File]::ReadAllText((Join-Path $Path 'VERSION'))).Trim()
    if (-not (Test-MtcVersionString $version)) {
        throw "The release VERSION file must contain a version like 1.2.3 (found '$version')."
    }
    return [pscustomobject]@{ Path = $Path; Version = $version }
}

function Get-MtcReleaseStamp {
    # releases\<version>-<yyyyMMdd-HHmmss> -> 'yyyyMMdd-HHmmss' ('' if none).
    param([string]$Name)
    if ($Name -match '-(\d{8}-\d{6})$') { return $Matches[1] }
    return ''
}

function Get-MtcReleaseVersionFromName {
    param([string]$Name)
    if ($Name -match '^(\d+\.\d+\.\d+)-\d{8}-\d{6}$') { return $Matches[1] }
    return ''
}

function Select-MtcReleasesToPrune {
    # Returns the release folders to delete: everything except the newest
    # $KeepCount (by timestamp) and the pinned ones (current/previous).
    param([object[]]$Releases, [string[]]$Pinned, [int]$KeepCount = 3)
    $pinnedNorm = @($Pinned | Where-Object { $_ } | ForEach-Object { $_.TrimEnd('\').ToLowerInvariant() })
    $sorted = @($Releases | Sort-Object -Property @{ Expression = { Get-MtcReleaseStamp $_.Name } } -Descending)
    $result = @()
    for ($i = 0; $i -lt $sorted.Count; $i++) {
        $isPinned = $pinnedNorm -contains $sorted[$i].FullName.TrimEnd('\').ToLowerInvariant()
        if ($i -ge $KeepCount -and -not $isPinned) { $result += $sorted[$i] }
    }
    return $result
}

function Copy-MtcReleaseFiles {
    # Copies an unpacked release into its own releases\ folder, leaving out
    # anything that must never be deployed (private HTML, databases,
    # secrets, local virtualenvs, caches).
    param([string]$Source, [string]$Destination)
    $xd = @('.git', '.venv', '.local', 'staticfiles', '__pycache__', '.pytest_cache', 'node_modules')
    $xf = @('*.sqlite3', '*.sqlite3-*', '*.pyc', '.env', 'settings.env', '*.probe.html',
        'MTC Missionary Supplies Tracker.html')
    $robocopyArgs = @($Source, $Destination, '/E', '/R:2', '/W:2', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/XD') + $xd + @('/XF') + $xf
    $result = Invoke-MtcNative -FilePath 'robocopy.exe' -ArgumentList $robocopyArgs -AllowFailure -Quiet
    # robocopy: 0-7 mean success (files copied/skipped), 8 and above failure.
    if ($result.ExitCode -ge 8) { throw "Copying the release files failed (robocopy exit code $($result.ExitCode))." }
    Get-ChildItem -LiteralPath $Destination -Recurse -File -ErrorAction SilentlyContinue |
        Unblock-File -ErrorAction SilentlyContinue
}

function Remove-MtcTree {
    # Deletes a folder inside the install root. Uses rmdir, which removes
    # junctions without following them (Remove-Item -Recurse in Windows
    # PowerShell 5.1 can follow a junction and empty its target).
    param([string]$Path, [string]$Root)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    if (-not (Test-MtcPathUnder -Path $Path -Parent $Root)) { throw "Refusing to delete $Path (outside $Root)." }
    Invoke-MtcNative -FilePath 'cmd.exe' -ArgumentList @('/d', '/c', 'rmdir', '/s', '/q', $Path) -AllowFailure -Quiet | Out-Null
    if (Test-Path -LiteralPath $Path) { throw "Could not delete $Path (is a file in it open?)." }
}

# ---------------------------------------------------- current / previous
function Get-MtcCurrentRelease {
    param([string]$Root)
    $link = Join-Path $Root 'current'
    $item = Get-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) { return $null }
    if (-not ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        throw "$link should be a link to a release folder but is a normal folder."
    }
    $target = @($item.Target) | Select-Object -First 1
    if (-not $target) { throw "Cannot read where $link points." }
    $target = [string]$target
    if ($target.StartsWith('\??\')) { $target = $target.Substring(4) }
    return $target.TrimEnd('\')
}

function Set-MtcCurrentRelease {
    # Points <root>\current (a directory junction) at $Release.
    param([string]$Root, [string]$Release)
    $link = Join-Path $Root 'current'
    $item = Get-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue
    if ($null -ne $item) {
        if (-not ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw "$link is a normal folder, not a link; refusing to replace it."
        }
        # Removes only the junction itself, never the release it points to.
        [System.IO.Directory]::Delete($link, $false)
    }
    New-Item -ItemType Junction -Path $link -Value $Release | Out-Null
    $now = Get-MtcCurrentRelease -Root $Root
    if (-not $now -or $now.ToLowerInvariant() -ne $Release.TrimEnd('\').ToLowerInvariant()) {
        throw "Switching $link to $Release did not take effect."
    }
}

function Get-MtcPreviousRelease {
    param([string]$Root)
    $file = Join-Path $Root 'previous.txt'
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return $null }
    $value = ([System.IO.File]::ReadAllText($file)).Trim()
    if (-not $value) { return $null }
    return $value
}

function Set-MtcPreviousRelease {
    param([string]$Root, [string]$Release)
    $file = Join-Path $Root 'previous.txt'
    if (-not $Release) {
        if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file -Force }
        return
    }
    Write-MtcTextFile -Path $file -Text ($Release + "`r`n")
}

function Get-MtcReleaseVersion {
    param([string]$Release)
    $file = Join-Path $Release 'VERSION'
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return '' }
    return ([System.IO.File]::ReadAllText($file)).Trim()
}

function Resolve-MtcRoot {
    # Explicit -Root wins; otherwise the install this script belongs to
    # (<root>\current\deploy\windows or <root>\releases\<name>\deploy\windows);
    # otherwise the default C:\MTCSupplies.
    param([string]$Root, [string]$ScriptDir, [string]$Default = 'C:\MTCSupplies')
    if ($Root) { return (ConvertTo-MtcFullPath $Root) }
    $dir = $ScriptDir
    for ($i = 0; $i -lt 4 -and $dir; $i++) {
        $dir = Split-Path -Parent $dir
        if ($dir -and (Test-Path -LiteralPath (Join-Path $dir 'settings.env') -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $dir 'releases') -PathType Container)) {
            return (ConvertTo-MtcFullPath $dir)
        }
    }
    return $Default
}

function Assert-MtcInstalled {
    param([string]$Root)
    if (-not (Test-Path -LiteralPath (Join-Path $Root 'settings.env') -PathType Leaf)) {
        throw "No MTC Supplies install found in $Root (settings.env is missing). Use -Root if it lives elsewhere."
    }
    $current = Get-MtcCurrentRelease -Root $Root
    if (-not $current -or -not (Test-Path -LiteralPath $current -PathType Container)) {
        throw "No active release found in $Root\current."
    }
    return $current
}

# ------------------------------------------------------- native commands
function Invoke-MtcNative {
    # Runs a program, echoing and logging its output, and returns
    # ExitCode + Output. Throws on a non-zero exit unless -AllowFailure.
    # Standard error is merged without Windows PowerShell 5.1 turning it
    # into a terminating error.
    param([string]$FilePath, [string[]]$ArgumentList = @(), [switch]$AllowFailure,
        [switch]$Quiet, [string]$FailMessage = '')
    $lines = New-Object -TypeName System.Collections.Generic.List[string]
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $FilePath @ArgumentList 2>&1 | ForEach-Object {
            $text = [string]$_
            $lines.Add($text)
            if (-not $Quiet) { Write-Host ('      ' + $text) }
            Write-MtcLog ('      ' + $text)
        }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($null -eq $code) { $code = 0 }
    $result = [pscustomobject]@{ ExitCode = [int]$code; Output = ($lines.ToArray() -join "`n") }
    if ($result.ExitCode -ne 0 -and -not $AllowFailure) {
        $what = $FailMessage
        if (-not $what) { $what = (Split-Path -Leaf $FilePath) + ' failed' }
        throw ($what + ' (exit code ' + $result.ExitCode + ').')
    }
    return $result
}

function Get-MtcPython {
    param([string]$Release)
    return (Join-Path (Join-Path (Join-Path $Release '.venv') 'Scripts') 'python.exe')
}

function Invoke-MtcManage {
    # python manage.py <args> with a release's own virtualenv. -Interactive
    # leaves the console attached (password prompts) and returns the exit code.
    param([string]$Release, [string[]]$Arguments, [switch]$AllowFailure, [switch]$Quiet,
        [switch]$Interactive, [string]$FailMessage = '')
    $python = Get-MtcPython -Release $Release
    $manage = Join-Path $Release 'manage.py'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Python environment missing in $Release." }
    Push-Location -LiteralPath $Release
    try {
        if ($Interactive) {
            & $python $manage @Arguments
            $code = $LASTEXITCODE
            if ($code -ne 0 -and -not $AllowFailure) {
                $what = $FailMessage
                if (-not $what) { $what = 'manage.py ' + $Arguments[0] + ' failed' }
                throw ($what + ' (exit code ' + $code + ').')
            }
            return [pscustomobject]@{ ExitCode = [int]$code; Output = '' }
        }
        return (Invoke-MtcNative -FilePath $python -ArgumentList (@($manage) + $Arguments) `
                -AllowFailure:$AllowFailure -Quiet:$Quiet -FailMessage $FailMessage)
    } finally {
        Pop-Location
    }
}

function Get-MtcMaintenanceToken {
    # Token printed by "manage.py maintenance --enter" (owner=... token=<hex>).
    param([string]$Output)
    $found = $null
    foreach ($m in [regex]::Matches([string]$Output, '(?:^|\s)token=([0-9a-f]{32})(?=\s|$)')) {
        $found = $m.Groups[1].Value
    }
    return $found
}

function Get-MtcRestoreToken {
    # Token printed by "manage.py restore_db" (maintenance-token: <hex>).
    param([string]$Output)
    $found = $null
    foreach ($m in [regex]::Matches([string]$Output, 'maintenance-token:\s*([0-9a-f]{32})')) {
        $found = $m.Groups[1].Value
    }
    return $found
}

# --------------------------------------------------------------- uv / venv
function Set-MtcUvEnvironment {
    # Keeps uv, its cache and the Python it installs inside the install
    # root (never in the installing administrator's profile), ignores any
    # personal uv configuration and uses uv-managed Python only.
    param([string]$Root, [switch]$SystemCertificates)
    $env:UV_PYTHON_INSTALL_DIR = Join-Path (Join-Path $Root 'tools') 'python'
    $env:UV_CACHE_DIR = Join-Path (Join-Path $Root 'tools') 'uv-cache'
    $env:UV_NO_CONFIG = '1'
    $env:UV_NO_PROGRESS = '1'
    $env:UV_LINK_MODE = 'copy'
    if ($SystemCertificates) { $env:UV_SYSTEM_CERTS = '1' }
}

function Install-MtcPython {
    # uv-managed CPython 3.13 inside <root>\tools\python (no PATH shims, no
    # registry entries). Idempotent: already-installed versions are reused.
    param([string]$Root, [switch]$SystemCertificates)
    Set-MtcUvEnvironment -Root $Root -SystemCertificates:$SystemCertificates
    $uv = Join-Path (Join-Path $Root 'tools') 'uv.exe'
    Invoke-MtcNative -FilePath $uv -ArgumentList @('python', 'install', $MtcPythonVersion, '--no-bin', '--no-registry') `
        -FailMessage 'Installing Python 3.13 failed' | Out-Null
}

function Initialize-MtcVenv {
    # Creates <release>\.venv with uv-managed Python 3.13 and installs the
    # pinned production requirements (wheels only: no compilers needed).
    param([string]$Root, [string]$Release, [switch]$SystemCertificates)
    Set-MtcUvEnvironment -Root $Root -SystemCertificates:$SystemCertificates
    $uv = Join-Path (Join-Path $Root 'tools') 'uv.exe'
    $venv = Join-Path $Release '.venv'
    Invoke-MtcNative -FilePath $uv -ArgumentList @('venv', $venv, '--python', $MtcPythonVersion,
        '--managed-python', '--no-python-downloads', '--clear') -FailMessage 'Creating the Python environment failed' | Out-Null
    $python = Get-MtcPython -Release $Release
    $requirements = Join-Path $Release 'requirements.prod.txt'
    Invoke-MtcNative -FilePath $uv -ArgumentList @('pip', 'install', '--python', $python, '--only-binary', ':all:',
        '-r', $requirements) -FailMessage 'Installing the app''s Python packages failed' | Out-Null
    $probe = Invoke-MtcNative -FilePath $python -ArgumentList @('-c', 'import waitress, django') -AllowFailure -Quiet
    if ($probe.ExitCode -ne 0) {
        Invoke-MtcNative -FilePath $uv -ArgumentList @('pip', 'install', '--python', $python, '--only-binary', ':all:',
            $MtcWaitressPin) -FailMessage 'Installing waitress failed' | Out-Null
    }
}

# --------------------------------------------------- downloads / checksums
function Get-MtcChecksumFromList {
    # Finds the hash for $FileName in a published checksum list
    # ("<hash>  <name>" or "<hash> *<name>" per line). $null if absent.
    param([string]$Text, [string]$FileName)
    foreach ($line in ([string]$Text).Replace("`r", '').Split([char]"`n")) {
        $parts = @($line.Trim() -split '\s+', 2)
        if ($parts.Count -lt 2) { continue }
        $name = $parts[1].Trim().TrimStart('*')
        if ($name -ceq $FileName -and $parts[0] -match '^[0-9A-Fa-f]{64,128}$') {
            return $parts[0].ToLowerInvariant()
        }
    }
    return $null
}

function Test-MtcFileHash {
    # Throws unless the file matches the pinned hash AND the published one.
    param([string]$Path, [string]$Algorithm, [string]$PinnedHash, [string]$PublishedHash)
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm $Algorithm).Hash.ToLowerInvariant()
    if (-not $PublishedHash) { throw ("The published checksum list has no entry for " + (Split-Path -Leaf $Path) + '.') }
    if ($PublishedHash.ToLowerInvariant() -ne $PinnedHash.ToLowerInvariant()) {
        throw ('The published checksum for ' + (Split-Path -Leaf $Path) + ' does not match the pinned one; refusing to use it.')
    }
    if ($actual -ne $PinnedHash.ToLowerInvariant()) {
        throw ('Downloaded ' + (Split-Path -Leaf $Path) + ' failed its ' + $Algorithm + ' check; refusing to use it.')
    }
    return $true
}

function Test-MtcToolVersionOutput {
    # "uv 0.12.20 (abc 2026-09-28)" / "v2.10.2 h1:..." contain the version.
    param([string]$Output, [string]$Version)
    return ([string]$Output -match ('(^|\s)v?' + [regex]::Escape($Version) + '(\s|$)'))
}

function Invoke-MtcDownload {
    param([string]$Url, [string]$OutFile)
    Enable-MtcTls12
    $oldProgress = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'  # the 5.1 progress bar slows downloads badly
    try {
        Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing -TimeoutSec 600
    } finally {
        $ProgressPreference = $oldProgress
    }
}

function Install-MtcTool {
    # Downloads a pinned tool zip, verifies it and puts the exe in tools\.
    param([string]$Root, [string]$Name, [string]$Version, [string]$Url, [string]$ChecksumUrl,
        [string]$Asset, [string]$Algorithm, [string]$PinnedHash, [string]$ExeName, [string[]]$VersionArgs)
    $tools = Join-Path $Root 'tools'
    $exe = Join-Path $tools $ExeName
    if (Test-Path -LiteralPath $exe -PathType Leaf) {
        $check = Invoke-MtcNative -FilePath $exe -ArgumentList $VersionArgs -AllowFailure -Quiet
        if ($check.ExitCode -eq 0 -and (Test-MtcToolVersionOutput -Output $check.Output -Version $Version)) {
            Write-MtcOk "$Name $Version already present"
            return $exe
        }
    }
    $downloads = Join-Path $tools 'downloads'
    New-Item -ItemType Directory -Path $downloads -Force | Out-Null
    $zip = Join-Path $downloads $Asset
    $sums = $zip + '.checksums.txt'
    Write-MtcInfo "Downloading $Name $Version ..."
    Invoke-MtcDownload -Url $Url -OutFile $zip
    Invoke-MtcDownload -Url $ChecksumUrl -OutFile $sums
    $published = Get-MtcChecksumFromList -Text ([System.IO.File]::ReadAllText($sums)) -FileName $Asset
    Test-MtcFileHash -Path $zip -Algorithm $Algorithm -PinnedHash $PinnedHash -PublishedHash $published | Out-Null
    Write-MtcOk "$Name download verified ($Algorithm)"
    $unpack = Join-Path $downloads ($Name + '-unpack')
    if (Test-Path -LiteralPath $unpack) { Remove-MtcTree -Path $unpack -Root $Root }
    Expand-Archive -LiteralPath $zip -DestinationPath $unpack -Force
    $found = Get-ChildItem -LiteralPath $unpack -Recurse -File -Filter $ExeName | Select-Object -First 1
    if ($null -eq $found) { throw "$ExeName not found inside $Asset." }
    Copy-Item -LiteralPath $found.FullName -Destination $exe -Force
    Remove-MtcTree -Path $unpack -Root $Root
    return $exe
}

# ----------------------------------------------------------- HTTP / ports
function Test-MtcPortOpen {
    param([string]$HostName = '127.0.0.1', [int]$Port = 8000, [int]$TimeoutMs = 1000)
    $client = New-Object -TypeName System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync($HostName, $Port)
        if (-not $task.Wait($TimeoutMs)) { return $false }
        return [bool]$client.Connected
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function ConvertFrom-MtcReadiness {
    # Interprets a /readyz/ response: Ok only for HTTP 200 with "ok": true.
    param([int]$StatusCode, [string]$Body)
    $json = $null
    try { $json = $Body | ConvertFrom-Json } catch { $json = $null }
    $ok = $false
    $version = ''
    $maintenance = $null
    $reason = ''
    if ($null -ne $json) {
        $names = @($json.PSObject.Properties | ForEach-Object { $_.Name })
        if ($names -contains 'ok') { $ok = ($json.ok -eq $true) }
        if ($names -contains 'version') { $version = [string]$json.version }
        if ($names -contains 'maintenance') { $maintenance = [bool]$json.maintenance }
        if ($names -contains 'reason') { $reason = [string]$json.reason }
    }
    return [pscustomobject]@{
        Reachable = $true; StatusCode = $StatusCode; Ok = ($ok -and $StatusCode -eq 200)
        Version = $version; Maintenance = $maintenance; Reason = $reason
    }
}

function Get-MtcReadiness {
    # GET http://127.0.0.1:8000/readyz/ the way Caddy would forward it.
    param([int]$Port = 8000, [int]$TimeoutMs = 5000)
    $request = [System.Net.HttpWebRequest]::Create(('http://127.0.0.1:{0}/readyz/' -f $Port))
    $request.Proxy = $null
    $request.Timeout = $TimeoutMs
    $request.ReadWriteTimeout = $TimeoutMs
    $request.AllowAutoRedirect = $false
    $request.Headers.Add('X-Forwarded-Proto', 'https')
    $response = $null
    try {
        $response = $request.GetResponse()
    } catch {
        $ex = $_.Exception
        while ($null -ne $ex -and -not ($ex -is [System.Net.WebException])) { $ex = $ex.InnerException }
        if ($null -ne $ex -and $null -ne $ex.Response) { $response = $ex.Response }
    }
    if ($null -eq $response) {
        return [pscustomobject]@{ Reachable = $false; StatusCode = 0; Ok = $false; Version = ''; Maintenance = $null; Reason = 'unreachable' }
    }
    try {
        $reader = New-Object -TypeName System.IO.StreamReader -ArgumentList $response.GetResponseStream()
        $body = $reader.ReadToEnd()
        $reader.Close()
        return (ConvertFrom-MtcReadiness -StatusCode ([int]$response.StatusCode) -Body $body)
    } finally {
        $response.Close()
    }
}

function Wait-MtcReady {
    # Polls readiness until ok (and, if given, the expected version serves).
    param([string]$ExpectedVersion = '', [int]$TimeoutSeconds = 120)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $last = $null
    while ((Get-Date) -lt $deadline) {
        $last = Get-MtcReadiness
        if ($last.Ok -and (-not $ExpectedVersion -or $last.Version -eq $ExpectedVersion)) { return $last }
        Start-Sleep -Seconds 2
    }
    if ($null -eq $last) { $last = Get-MtcReadiness }
    return $last
}

# -------------------------------------------------- scheduled tasks (Windows)
function Get-MtcTask {
    param([string]$Name)
    return (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue)
}

function Stop-MtcStrayProcesses {
    # Kills app/caddy processes that belong to this install (by path) and are
    # still alive after their task stopped (cmd.exe wrappers do not always
    # take their child down). Never touches programs outside $Root.
    param([string]$Root, [int]$Port = 0, [string]$ImageName = '')
    $ids = @()
    if ($Port -gt 0) {
        $ids += @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
                ForEach-Object { $_.OwningProcess })
    }
    if ($ImageName) {
        $ids += @(Get-Process -Name $ImageName -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
    }
    foreach ($id in ($ids | Sort-Object -Unique)) {
        $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
        if ($null -eq $proc) { continue }
        $path = $null
        try { $path = $proc.Path } catch { $path = $null }
        if ($path -and (Test-MtcPathUnder -Path $path -Parent $Root)) {
            Write-MtcInfo ("Stopping leftover process " + $proc.ProcessName + " (pid " + $id + ")")
            Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        } elseif ($Port -gt 0) {
            Write-MtcWarn ("Port $Port is held by " + $proc.ProcessName + " (pid $id), which is not part of this install.")
        }
    }
}

function Stop-MtcAppTask {
    # Disables (so its keep-alive trigger cannot restart it) and stops the
    # App task, then waits until nothing listens on 127.0.0.1:8000.
    param([string]$Root, [int]$TimeoutSeconds = 60)
    if (Get-MtcTask -Name $MtcAppTaskName) {
        Disable-ScheduledTask -TaskName $MtcAppTaskName | Out-Null
        Stop-ScheduledTask -TaskName $MtcAppTaskName -ErrorAction SilentlyContinue
    }
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $killedAt = (Get-Date).AddSeconds(10)
    $killed = $false
    while (Test-MtcPortOpen -HostName '127.0.0.1' -Port $MtcAppPort) {
        if (-not $killed -and (Get-Date) -gt $killedAt) {
            Stop-MtcStrayProcesses -Root $Root -Port $MtcAppPort
            $killed = $true
        }
        if ((Get-Date) -gt $deadline) {
            throw "The app did not stop: something still listens on 127.0.0.1:$MtcAppPort."
        }
        Start-Sleep -Seconds 1
    }
}

function Start-MtcAppTask {
    Enable-ScheduledTask -TaskName $MtcAppTaskName | Out-Null
    Start-ScheduledTask -TaskName $MtcAppTaskName
}

function Stop-MtcHttpsTask {
    param([string]$Root)
    if (Get-MtcTask -Name $MtcHttpsTaskName) {
        Stop-ScheduledTask -TaskName $MtcHttpsTaskName -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
    Stop-MtcStrayProcesses -Root $Root -ImageName 'caddy'
}

function Format-MtcTaskResult {
    param([long]$Code)
    switch ($Code) {
        0 { return 'success' }
        1 { return 'failed (exit code 1)' }
        267008 { return 'ready' }
        267009 { return 'running' }
        267011 { return 'has not run yet' }
        267014 { return 'stopped by request' }
        2147750687 { return 'already running' }
        3221225786 { return 'stopped (closed)' }
    }
    return ('result 0x{0:X}' -f $Code)
}

# --------------------------------------------------------------- Caddyfile
function New-MtcCaddyfileText {
    # Same behaviour as deploy/Caddyfile (Docker) with this host's values:
    # LAN HTTPS with Caddy's internal CA, IP clients without SNI, everything
    # forwarded to Waitress on loopback marked as HTTPS, plus /ca.crt so staff
    # PCs can download the root certificate to trust.
    param([string]$Root, [string]$HostAddress, [int]$Port)
    $r = $Root.Replace('\', '/')
    $lines = @(
        '# Generated by install.ps1 - do not edit; re-run install.ps1 -Repair instead.',
        '{',
        "`tadmin off",
        "`tpersist_config off",
        "`t# install.ps1 installs the root certificate itself (when asked).",
        "`tskip_install_trust",
        "`t# Staff browsers address the host by IP and send no SNI.",
        ("`tdefault_sni " + $HostAddress),
        "`tlog {",
        ("`t`toutput file `"" + $r + "/data/logs/caddy.log`" {"),
        "`t`t`troll_size 10MiB",
        "`t`t`troll_keep 5",
        "`t`t}",
        "`t}",
        '}',
        '',
        ('https://' + $HostAddress + ':' + $Port + ' {'),
        "`ttls internal",
        "`t# Public root certificate only (never the key) for staff PCs to trust.",
        "`thandle /ca.crt {",
        ("`t`troot * `"" + $r + "/data/caddy/caddy/pki/authorities/local`""),
        "`t`trewrite * /root.crt",
        "`t`theader Content-Type application/x-x509-ca-cert",
        "`t`theader Content-Disposition `"attachment; filename=MTC-Supplies-CA.crt`"",
        "`t`tfile_server",
        "`t}",
        "`treverse_proxy 127.0.0.1:8000 {",
        "`t`theader_up X-Forwarded-Proto https",
        "`t}",
        '}'
    )
    return (($lines -join "`n") + "`n")
}

function Get-MtcCaRootPath {
    param([string]$Root)
    return (Join-Path $Root 'data\caddy\caddy\pki\authorities\local\root.crt')
}

function Get-MtcCertificateThumbprint {
    param([string]$Path)
    $cert = New-Object -TypeName System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList $Path
    try { return $cert.Thumbprint } finally { $cert.Dispose() }
}

# ------------------------------------------------------------------ backups
function Get-MtcBackupDir {
    param($Settings, [string]$Root)
    return (Get-MtcSetting $Settings 'MTC_BACKUP_DIR' (Join-Path (Join-Path $Root 'data') 'backups'))
}

function Read-MtcManifest {
    param([string]$BackupPath)
    $manifest = $BackupPath + '.manifest.json'
    if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { return $null }
    try { return ([System.IO.File]::ReadAllText($manifest) | ConvertFrom-Json) } catch { return $null }
}

function Select-MtcRollbackBackup {
    # Newest pre-update-* backup whose manifest says it completed and was
    # taken by $TargetVersion (the release being rolled back to). Each
    # candidate: Name, FullName, Manifest (object or $null).
    param([object[]]$Candidates, [string]$TargetVersion)
    $ordered = @($Candidates | Where-Object { $_.Name -like 'pre-update-*.sqlite3' } |
            Sort-Object -Property Name -Descending)
    foreach ($c in $ordered) {
        $m = $c.Manifest
        if ($null -eq $m) { continue }
        $names = @($m.PSObject.Properties | ForEach-Object { $_.Name })
        if (($names -notcontains 'completed') -or ($m.completed -ne $true)) { continue }
        if (($names -notcontains 'app_version') -or ([string]$m.app_version -ne $TargetVersion)) { continue }
        return $c
    }
    return $null
}

function Test-MtcBackupChecksum {
    param([string]$BackupPath, $Manifest)
    if ($null -eq $Manifest) { return $false }
    $actual = (Get-FileHash -LiteralPath $BackupPath -Algorithm SHA256).Hash.ToLowerInvariant()
    return ($actual -eq ([string]$Manifest.sha256).ToLowerInvariant())
}

function Invoke-MtcRestoreFlow {
    # Puts a verified backup back, optionally together with another release
    # (rollback). Same protocol as scripts/restore.sh:
    #   verify -> owned maintenance window -> safety backup of today's data
    #   -> stop app -> (switch release) -> restore_db nested under the window
    #   (restore_db re-acquires a fresh window and prints its token) -> start
    #   -> readiness incl. version -> release the window with that token.
    # Returns Code 0 (done), 1 (nothing changed / undone safely) or
    # 2 (manual recovery needed) plus a Message.
    param([string]$Root, [string]$CurrentRelease, [string]$TargetRelease, [string]$BackupFile,
        [string]$Owner, [string]$SafetyPrefix, [string]$BackupDir, [switch]$Takeover, [switch]$Yes)
    $currentVersion = Get-MtcReleaseVersion -Release $CurrentRelease
    $targetVersion = Get-MtcReleaseVersion -Release $TargetRelease
    $switching = ($CurrentRelease.TrimEnd('\').ToLowerInvariant() -ne $TargetRelease.TrimEnd('\').ToLowerInvariant())

    Write-MtcStep 'Checking the backup'
    if (-not (Test-Path -LiteralPath $BackupFile -PathType Leaf)) { return [pscustomobject]@{ Code = 1; Message = "Backup not found: $BackupFile" } }
    $manifest = Read-MtcManifest -BackupPath $BackupFile
    if (-not (Test-MtcBackupChecksum -BackupPath $BackupFile -Manifest $manifest)) {
        return [pscustomobject]@{ Code = 1; Message = 'The backup has no matching manifest checksum (incomplete or altered); refusing to use it.' }
    }
    $verify = Invoke-MtcManage -Release $TargetRelease -Arguments @('verify_backup', $BackupFile) -AllowFailure -Quiet
    if ($verify.ExitCode -ne 0) {
        return [pscustomobject]@{ Code = 1; Message = ('The backup failed verification: ' + $verify.Output.Trim()) }
    }
    $taken = (Get-Item -LiteralPath $BackupFile).LastWriteTime.ToString('dddd d MMMM yyyy, h:mm tt')
    Write-MtcOk ('Backup verified: ' + (Split-Path -Leaf $BackupFile) + ' (saved ' + $taken + ')')
    Write-Host ''
    Write-Host ('    This puts the data back to how it was on ' + $taken + '.') -ForegroundColor Yellow
    Write-Host '    Anything staff entered after that will no longer be in the app (a safety copy of' -ForegroundColor Yellow
    Write-Host '    the current data is saved first, so it is not destroyed).' -ForegroundColor Yellow
    if ($switching) { Write-Host ('    The app goes back from version ' + $currentVersion + ' to ' + $targetVersion + '.') -ForegroundColor Yellow }
    if (-not $Yes) {
        $typed = Read-Host '    Type RESTORE to continue (anything else cancels)'
        if ($typed -cne 'RESTORE') { return [pscustomobject]@{ Code = 1; Message = 'Cancelled; nothing was changed.' } }
    }

    Write-MtcStep 'Pausing saving and saving a safety copy of the current data'
    $enterArgs = @('maintenance', '--enter', '--owner', $Owner, '--reason', ('restore ' + (Split-Path -Leaf $BackupFile)))
    if ($Takeover) { $enterArgs += '--force' }
    $enter = Invoke-MtcManage -Release $CurrentRelease -Arguments $enterArgs -AllowFailure -Quiet
    $token = Get-MtcMaintenanceToken $enter.Output
    if ($enter.ExitCode -ne 0 -or -not $token) {
        return [pscustomobject]@{ Code = 1; Message = ('Could not pause saving: ' + $enter.Output.Trim() +
                ' If an earlier update or restore crashed, run this again with -Takeover.') }
    }
    $safety = Join-Path $BackupDir ($SafetyPrefix + (New-MtcStamp) + '.sqlite3')
    $copy = Invoke-MtcManage -Release $CurrentRelease -Arguments @('backup_db', '--dest', $safety, '--quiesce',
        '--owner', $Owner, '--token', $token) -AllowFailure -Quiet
    if ($copy.ExitCode -ne 0) {
        $null = Invoke-MtcManage -Release $CurrentRelease -Arguments @('maintenance', '--exit', '--token', $token) -AllowFailure -Quiet
        return [pscustomobject]@{ Code = 1; Message = ('The safety copy failed, so nothing was changed: ' + $copy.Output.Trim()) }
    }
    Write-MtcOk ('Safety copy: ' + $safety)

    $help = ('Saving stays paused. Safety copy of the data before this restore: ' + $safety +
        '. Logs: ' + $Root + '\data\logs. Ask the developer for help if unsure.')
    $switched = $false
    $restoreToken = $null
    try {
        Write-MtcStep 'Stopping the app'
        Stop-MtcAppTask -Root $Root
        if ($switching) {
            Set-MtcCurrentRelease -Root $Root -Release $TargetRelease
            $switched = $true
            Write-MtcOk ('Switched to ' + $TargetRelease)
        }
        Write-MtcStep 'Restoring the backup'
        $base = @('restore_db', $BackupFile, '--expected-version', $targetVersion, '--allow-newer-data-loss')
        $restore = Invoke-MtcManage -Release $TargetRelease -Arguments ($base + @('--owner', $Owner, '--token', $token)) -AllowFailure
        $restoreToken = Get-MtcRestoreToken $restore.Output
        if ($restore.ExitCode -ne 0 -and -not $restoreToken) {
            Write-MtcInfo 'Retrying with an explicit takeover of the maintenance window (audited; saving stays paused) ...'
            $restore = Invoke-MtcManage -Release $TargetRelease -Arguments ($base + @('--force-takeover')) -AllowFailure
            $restoreToken = Get-MtcRestoreToken $restore.Output
        }
        if (-not $restoreToken) {
            # restore_db refused before swapping anything: undo our own steps.
            Write-MtcFail 'The restore was refused; putting things back as they were.'
            if ($switched) { Set-MtcCurrentRelease -Root $Root -Release $CurrentRelease }
            Start-MtcAppTask
            $back = Wait-MtcReady -ExpectedVersion $currentVersion -TimeoutSeconds 180
            $exit = Invoke-MtcManage -Release $CurrentRelease -Arguments @('maintenance', '--exit', '--token', $token) -AllowFailure -Quiet
            if ($back.Ok -and $exit.ExitCode -eq 0) {
                return [pscustomobject]@{ Code = 1; Message = ('Nothing was restored; version ' + $currentVersion + ' is running with its data unchanged.') }
            }
            return [pscustomobject]@{ Code = 2; Message = ('The restore was refused and the app did not come back cleanly. ' +
                    'Maintenance token: ' + $token + '. ' + $help) }
        }
        if ($restore.ExitCode -ne 0) {
            return [pscustomobject]@{ Code = 2; Message = ('The backup was put back but its checks failed; the app stays stopped. ' +
                    'Maintenance token: ' + $restoreToken + '. ' + $help) }
        }
        Write-MtcOk 'Backup restored'
        Write-MtcStep 'Starting the app'
        Start-MtcAppTask
        $ready = Wait-MtcReady -ExpectedVersion $targetVersion -TimeoutSeconds 180
        if (-not $ready.Ok) {
            return [pscustomobject]@{ Code = 2; Message = ('Version ' + $targetVersion + ' did not become ready (' + $ready.Reason +
                    '). Maintenance token: ' + $restoreToken + '. ' + $help) }
        }
        $exit = Invoke-MtcManage -Release $TargetRelease -Arguments @('maintenance', '--exit', '--token', $restoreToken) -AllowFailure -Quiet
        if ($exit.ExitCode -ne 0) {
            return [pscustomobject]@{ Code = 2; Message = ('The app is running but saving is still paused. Release it with: ' +
                    $Root + '\mtc.cmd maintenance --exit --token ' + $restoreToken) }
        }
        Write-MtcOk ('Version ' + $targetVersion + ' is serving and saving is enabled again')
        return [pscustomobject]@{ Code = 0; Message = ('Restored ' + (Split-Path -Leaf $BackupFile) + '. Safety copy of the replaced data: ' + $safety) }
    } catch {
        $held = $token
        if ($restoreToken) { $held = $restoreToken }
        return [pscustomobject]@{ Code = 2; Message = ($_.Exception.Message + ' Maintenance token: ' + $held + '. ' + $help) }
    }
}

function Copy-MtcLauncher {
    # <root>\mtc.cmd: the short command administrators type (mtc status, ...).
    param([string]$Root, [string]$Release)
    $source = Join-Path (Join-Path (Join-Path $Release 'deploy') 'windows') 'mtc.cmd'
    if (Test-Path -LiteralPath $source -PathType Leaf) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $Root 'mtc.cmd') -Force
    }
}

function Split-MtcArguments {
    # Separates our own options (-Name value / -Switch, case-insensitive)
    # from everything else, which is kept in order for manage.py.
    param([object[]]$Raw, [string[]]$ValueOptions = @(), [string[]]$SwitchOptions = @())
    $options = @{}
    $rest = New-Object -TypeName System.Collections.Generic.List[string]
    $i = 0
    while ($i -lt $Raw.Count) {
        $arg = [string]$Raw[$i]
        $matched = $false
        foreach ($name in $ValueOptions) {
            if ($arg -ieq ('-' + $name)) {
                if ($i + 1 -ge $Raw.Count) { throw "-$name needs a value." }
                $options[$name] = [string]$Raw[$i + 1]
                $i++
                $matched = $true
                break
            }
        }
        if (-not $matched) {
            foreach ($name in $SwitchOptions) {
                if ($arg -ieq ('-' + $name)) {
                    $options[$name] = $true
                    $matched = $true
                    break
                }
            }
        }
        if (-not $matched) { $rest.Add($arg) }
        $i++
    }
    return [pscustomobject]@{ Options = $options; Rest = $rest.ToArray() }
}

function New-MtcStamp {
    return (Get-Date -Format 'yyyyMMdd-HHmmss')
}
