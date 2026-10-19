<#
.SYNOPSIS
    MTC Supplies icon in the Windows notification area (by the clock).

.DESCRIPTION
    Runs as the signed-in user, with no administrator rights. It shows whether
    MTC Supplies is running and offers Open, Start, Stop and Update. Start,
    Stop and Update only ask Windows to run one of three fixed jobs that the
    installer set up (they run as SYSTEM; users may start them, not change
    them), the same way a VPN's tray app asks its background service.

    Installed to %ProgramData%\MTC Supplies\tray.ps1 and started from the
    Start menu ("MTC Supplies tray icon") and at sign-in. Exit closes only the
    icon; MTC Supplies keeps running for everyone.
#>
param(
    [string]$Url = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 1.0

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$createdNew = $false
$script:mutex = New-Object -TypeName System.Threading.Mutex -ArgumentList $true, 'Local\MTCSuppliesTray', ([ref]$createdNew)
if (-not $createdNew) { exit 0 }

$script:here = $PSScriptRoot
$script:url = $Url
if (-not $script:url) { $script:url = 'https://localhost:8443/' }
if (-not $script:url.EndsWith('/')) { $script:url = $script:url + '/' }
$script:tasks = @{
    'start' = 'MTC Supplies - Start (tray)'
    'stop' = 'MTC Supplies - Stop (tray)'
    'update' = 'MTC Supplies - Update (tray)'
}
$script:versionUrl = 'https://raw.githubusercontent.com/DrSchnitzel57/NZ-Supplies-Tracker/main/VERSION'
$script:state = 'checking'
$script:version = ''
$script:latest = ''
$script:lastUpdateState = ''
$script:lastUpdateCheck = [datetime]::MinValue
$script:busy = ''

function Get-MtcTrayIcon {
    param([string]$Name)
    $path = Join-Path $script:here $Name
    if (Test-Path -LiteralPath $path -PathType Leaf) { return (New-Object -TypeName System.Drawing.Icon -ArgumentList $path) }
    return [System.Drawing.SystemIcons]::Application
}

function Get-MtcTrayReadiness {
    # The app answers on the local loopback without HTTPS; no sign-in needed.
    try {
        $request = [System.Net.HttpWebRequest]::Create('http://127.0.0.1:8000/readyz/')
        $request.Timeout = 1500
        $request.Proxy = $null
        $response = $request.GetResponse()
        try {
            $reader = New-Object -TypeName System.IO.StreamReader -ArgumentList $response.GetResponseStream()
            $body = $reader.ReadToEnd()
            $reader.Dispose()
        } finally {
            $response.Close()
        }
        $version = ''
        if ($body -match '"version"\s*:\s*"([^"]*)"') { $version = $Matches[1] }
        $paused = $body -match '"maintenance"\s*:\s*true'
        return @{ Up = $true; Version = $version; Paused = $paused }
    } catch {
        return @{ Up = $false; Version = ''; Paused = $false }
    }
}

function Get-MtcTrayUpdateStatus {
    $path = Join-Path $script:here 'update-status.txt'
    $result = @{ state = ''; version = ''; message = '' }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $result }
    try {
        foreach ($line in [System.IO.File]::ReadAllLines($path)) {
            $at = $line.IndexOf('=')
            if ($at -gt 0) { $result[$line.Substring(0, $at)] = $line.Substring($at + 1) }
        }
    } catch { }
    return $result
}

function Test-MtcTrayNewer {
    param([string]$Candidate, [string]$Running)
    if ($Candidate -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or $Running -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') { return $false }
    return (([version]$Candidate).CompareTo([version]$Running) -gt 0)
}

function Update-MtcTrayLatest {
    # Once at start, then every 6 hours: is there a newer version on GitHub?
    if (((Get-Date) - $script:lastUpdateCheck).TotalHours -lt 6) { return }
    $script:lastUpdateCheck = Get-Date
    try {
        $client = New-Object -TypeName System.Net.WebClient
        $client.Headers.Add('User-Agent', 'MTC-Supplies-tray')
        $text = $client.DownloadString($script:versionUrl).Trim()
        $client.Dispose()
        if ($text -match '^[0-9]+\.[0-9]+\.[0-9]+$') { $script:latest = $text }
    } catch { }
}

function Invoke-MtcTrayJob {
    param([string]$Job)
    try {
        $service = New-Object -ComObject Schedule.Service
        $service.Connect()
        $task = $service.GetFolder('\').GetTask($script:tasks[$Job])
        [void]$task.Run($null)
        return $true
    } catch {
        [void][System.Windows.Forms.MessageBox]::Show(
            ('Windows would not run "' + $script:tasks[$Job] + '".' + [Environment]::NewLine + [Environment]::NewLine +
                'An administrator can fix this by running "mtc repair" in the MTC Supplies admin console.' +
                [Environment]::NewLine + [Environment]::NewLine + $_.Exception.Message),
            'MTC Supplies', 'OK', 'Warning')
        return $false
    }
}

function Show-MtcTrayTip {
    param([string]$Text, [string]$Kind = 'Info')
    $script:notify.BalloonTipTitle = 'MTC Supplies'
    $script:notify.BalloonTipText = $Text
    $script:notify.BalloonTipIcon = $Kind
    $script:notify.ShowBalloonTip(5000)
}

function Update-MtcTray {
    $ready = Get-MtcTrayReadiness
    $update = Get-MtcTrayUpdateStatus
    Update-MtcTrayLatest
    if ($ready.Version) { $script:version = $ready.Version }

    # Report how an update ended (once), even if this icon started later.
    if ($update.state -ne $script:lastUpdateState) {
        if ($script:lastUpdateState -and $update.state -eq 'done') { Show-MtcTrayTip ($update.message + '.') }
        if ($script:lastUpdateState -and $update.state -eq 'failed') { Show-MtcTrayTip ($update.message) 'Warning' }
        if ($script:lastUpdateState -and $update.state -eq 'current') { Show-MtcTrayTip ($update.message + '.') }
        $script:lastUpdateState = $update.state
        if ($update.state -ne 'running' -and $update.state -ne 'checking' -and $script:busy -eq 'update') { $script:busy = '' }
    }

    if ($update.state -eq 'running' -or $update.state -eq 'checking') {
        $script:state = 'updating'
    } elseif ($ready.Up -and $ready.Paused) {
        $script:state = 'paused'
    } elseif ($ready.Up) {
        $script:state = 'running'
        if ($script:busy -eq 'start') { $script:busy = ''; Show-MtcTrayTip 'MTC Supplies is running.' }
    } else {
        $script:state = 'stopped'
        if ($script:busy -eq 'stop') { $script:busy = ''; Show-MtcTrayTip 'MTC Supplies is stopped for everyone.' }
    }

    $label = 'MTC Supplies'
    if ($script:version) { $label = $label + ' ' + $script:version }
    switch ($script:state) {
        'running' { $label = $label + ' - running' }
        'paused' { $label = $label + ' - saving paused (backup or update)' }
        'updating' { $label = 'MTC Supplies - updating (' + $update.version + ')...' }
        default {
            if ($script:busy -eq 'start') { $label = 'MTC Supplies - starting...' } else { $label = 'MTC Supplies - stopped' }
        }
    }
    if ($script:busy -eq 'stop' -and $script:state -ne 'stopped') { $label = 'MTC Supplies - stopping...' }
    $script:statusItem.Text = $label
    $tip = $label
    if ($tip.Length -gt 63) { $tip = $tip.Substring(0, 63) }
    $script:notify.Text = $tip
    if ($script:state -eq 'stopped') { $script:notify.Icon = $script:iconOff } else { $script:notify.Icon = $script:iconOn }

    $idle = (-not $script:busy) -and $script:state -ne 'updating'
    $script:startItem.Enabled = $idle -and $script:state -eq 'stopped'
    $script:stopItem.Enabled = $idle -and ($script:state -eq 'running' -or $script:state -eq 'paused')
    $script:openItem.Enabled = $script:state -ne 'stopped'
    $script:statusPageItem.Enabled = $script:state -ne 'stopped'
    if ($script:latest -and (Test-MtcTrayNewer -Candidate $script:latest -Running $script:version)) {
        $script:updateItem.Text = 'Update to ' + $script:latest + '...'
        $script:updateItem.Enabled = $idle -and $script:state -eq 'running'
    } else {
        $script:updateItem.Text = 'Check for updates'
        $script:updateItem.Enabled = $idle
    }
}

$script:iconOn = Get-MtcTrayIcon 'mtc-supplies.ico'
$script:iconOff = Get-MtcTrayIcon 'mtc-supplies-off.ico'

$script:notify = New-Object -TypeName System.Windows.Forms.NotifyIcon
$script:notify.Icon = $script:iconOn
$script:notify.Text = 'MTC Supplies'
$menu = New-Object -TypeName System.Windows.Forms.ContextMenuStrip

$script:statusItem = $menu.Items.Add('MTC Supplies - checking...')
$script:statusItem.Enabled = $false
[void]$menu.Items.Add('-')
$script:openItem = $menu.Items.Add('Open MTC Supplies')
$script:openItem.Font = New-Object -TypeName System.Drawing.Font -ArgumentList $script:openItem.Font, ([System.Drawing.FontStyle]::Bold)
$script:openItem.Add_Click({ Start-Process $script:url })
$script:statusPageItem = $menu.Items.Add('System status (administrators)')
$script:statusPageItem.Add_Click({ Start-Process ($script:url + 'admin-area/status/') })
[void]$menu.Items.Add('-')
$script:startItem = $menu.Items.Add('Start')
$script:startItem.Add_Click({
        if (Invoke-MtcTrayJob 'start') { $script:busy = 'start'; Update-MtcTray }
    })
$script:stopItem = $menu.Items.Add('Stop for everyone...')
$script:stopItem.Add_Click({
        $answer = [System.Windows.Forms.MessageBox]::Show(
            ('Stop MTC Supplies for everyone?' + [Environment]::NewLine + [Environment]::NewLine +
                'Nobody can use it until someone chooses Start here. It stays stopped after this PC restarts.'),
            'MTC Supplies', 'YesNo', 'Question')
        if ($answer -eq 'Yes' -and (Invoke-MtcTrayJob 'stop')) { $script:busy = 'stop'; Update-MtcTray }
    })
$script:updateItem = $menu.Items.Add('Check for updates')
$script:updateItem.Add_Click({
        if (-not (Test-MtcTrayNewer -Candidate $script:latest -Running $script:version)) {
            $script:lastUpdateCheck = [datetime]::MinValue
            Update-MtcTrayLatest
            if (Test-MtcTrayNewer -Candidate $script:latest -Running $script:version) {
                Update-MtcTray
                Show-MtcTrayTip ('Version ' + $script:latest + ' is available. Choose "Update to ' + $script:latest + '" to install it.')
            } else {
                Show-MtcTrayTip ('You have the newest version (' + $script:version + ').')
            }
            return
        }
        $answer = [System.Windows.Forms.MessageBox]::Show(
            ('Update MTC Supplies to ' + $script:latest + ' now?' + [Environment]::NewLine + [Environment]::NewLine +
                'It takes a few minutes. Staff can still look things up, but saving pauses until it finishes. ' +
                'A backup is made first, and the old version comes back by itself if anything goes wrong.'),
            'MTC Supplies', 'YesNo', 'Question')
        if ($answer -eq 'Yes' -and (Invoke-MtcTrayJob 'update')) { $script:busy = 'update'; Update-MtcTray }
    })
[void]$menu.Items.Add('-')
$exitItem = $menu.Items.Add('Close this icon (MTC Supplies keeps running)')
$exitItem.Add_Click({
        $script:timer.Stop()
        $script:notify.Visible = $false
        $script:notify.Dispose()
        [System.Windows.Forms.Application]::Exit()
    })

$script:notify.ContextMenuStrip = $menu
$script:notify.Add_MouseClick({
        param($sender, $e)
        if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left) { Start-Process $script:url }
    })
$script:notify.Visible = $true

$script:timer = New-Object -TypeName System.Windows.Forms.Timer
$script:timer.Interval = 5000
$script:timer.Add_Tick({ Update-MtcTray })
Update-MtcTray
$script:timer.Start()

try {
    [System.Windows.Forms.Application]::Run()
} finally {
    $script:notify.Visible = $false
    $script:mutex.ReleaseMutex()
}
