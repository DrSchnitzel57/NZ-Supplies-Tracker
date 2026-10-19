# Checks for deploy/windows/*.ps1, run under PowerShell 7 (any OS) by
# tests/windows_compat.py. Modes:
#   lint       parse every script; reject PowerShell-7-only syntax and APIs
#              that Windows PowerShell 5.1 / .NET Framework lack; non-ASCII;
#              calls to undefined *-Mtc* functions; never-assigned variables
#   unit       unit tests for the pure helpers in common.ps1
#   parse      print ConvertFrom-MtcSettingsText(-Path) as JSON (parity test)
#   readiness  print Get-MtcReadiness/Test-MtcPortOpen against -Port as JSON
#   caddyfile  print New-MtcCaddyfileText for -Root/-HostAddress/-Port
param(
    [string]$Mode = 'unit',
    [string]$Path = '',
    [int]$Port = 0,
    [string]$Root = '',
    [string]$HostAddress = ''
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$windowsDir = Join-Path (Join-Path $repo 'deploy') 'windows'

function Get-ScriptFiles {
    return @(Get-ChildItem -LiteralPath $windowsDir -Filter '*.ps1' -File | Sort-Object Name)
}

if ($Mode -eq 'lint') {
    $problems = New-Object System.Collections.Generic.List[string]
    $automatic = @('_', 'PSItem', 'args', 'true', 'false', 'null', 'PSScriptRoot', 'PSCommandPath',
        'PSBoundParameters', 'LASTEXITCODE', 'Matches', 'ErrorActionPreference', 'ProgressPreference',
        'PSVersionTable', 'Error', 'MyInvocation', 'this', 'input', 'HOME', 'PID', 'PWD', 'Host',
        'ConfirmPreference', 'WarningPreference', 'VerbosePreference', 'OFS')
    $ps7Params = @{
        '*' = @('Parallel', 'AsByteStream', 'SkipCertificateCheck', 'NoProxy', 'AdditionalChildPath', 'LeafBase',
            'SkipHttpErrorCheck', 'StatusCodeVariable', 'ResponseHeadersVariable', 'Resume', 'SslProtocol', 'AsHashtable')
        'ConvertFrom-Json' = @('Depth', 'NoEnumerate')
        'Split-Path' = @('Extension')
        'Start-Process' = @('Environment')
    }
    $netCoreOnly = @('GetRelativePath', 'ToHexString', 'HashData', 'ReplaceLineEndings', 'GetInt32', 'Fill',
        'IsNullOrEmpty(?=\s*\(\s*\))')
    $common = Join-Path $windowsDir 'common.ps1'
    $tokens = $null
    $errors = $null
    $commonAst = [System.Management.Automation.Language.Parser]::ParseFile($common, [ref]$tokens, [ref]$errors)
    $sharedFunctions = @($commonAst.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) |
            ForEach-Object { $_.Name })
    $sharedAssigned = @($commonAst.EndBlock.Statements | Where-Object { $_ -is [System.Management.Automation.Language.AssignmentStatementAst] } |
            ForEach-Object { $_.Left.VariablePath.UserPath })

    foreach ($file in Get-ScriptFiles) {
        $name = $file.Name
        $bytes = [System.IO.File]::ReadAllBytes($file.FullName)
        foreach ($b in $bytes) { if ($b -gt 127) { $problems.Add("${name}: contains non-ASCII bytes (5.1 reads it as ANSI)"); break } }
        $tokens = $null
        $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
        foreach ($e in $errors) { $problems.Add(("{0}:{1}: parse error: {2}" -f $name, $e.Extent.StartLineNumber, $e.Message)) }
        $all = $ast.FindAll({ param($n) $true }, $true)
        $defined = @($sharedFunctions) + @($all | Where-Object { $_ -is [System.Management.Automation.Language.FunctionDefinitionAst] } | ForEach-Object { $_.Name })
        $assigned = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        foreach ($v in ($sharedAssigned + $automatic)) { [void]$assigned.Add($v) }
        foreach ($node in $all) {
            $line = $node.Extent.StartLineNumber
            $typeName = $node.GetType().Name
            if ($typeName -eq 'TernaryExpressionAst') { $problems.Add("${name}:${line}: ternary ?: is PowerShell 7 only") }
            if ($typeName -eq 'PipelineChainAst') { $problems.Add("${name}:${line}: && / || pipeline chains are PowerShell 7 only") }
            if ($node -is [System.Management.Automation.Language.BinaryExpressionAst] -and [string]$node.Operator -eq 'QuestionQuestion') {
                $problems.Add("${name}:${line}: ?? is PowerShell 7 only")
            }
            if ($node -is [System.Management.Automation.Language.AssignmentStatementAst]) {
                if ([string]$node.Operator -eq 'QuestionQuestionEquals') { $problems.Add("${name}:${line}: ??= is PowerShell 7 only") }
                $left = $node.Left
                if ($left -is [System.Management.Automation.Language.ConvertExpressionAst]) { $left = $left.Child }
                if ($left -is [System.Management.Automation.Language.VariableExpressionAst]) { [void]$assigned.Add($left.VariablePath.UserPath.Split(':')[-1]) }
            }
            if (($node -is [System.Management.Automation.Language.MemberExpressionAst] -or
                    $node -is [System.Management.Automation.Language.IndexExpressionAst]) -and $node.NullConditional) {
                $problems.Add("${name}:${line}: ?. / ?[] null-conditional access is PowerShell 7 only")
            }
            if ($node -is [System.Management.Automation.Language.PipelineAst] -and $node.Background) {
                $problems.Add("${name}:${line}: trailing & (background job) is PowerShell 7 only")
            }
            if ($node -is [System.Management.Automation.Language.ScriptBlockAst] -and $null -ne $node.CleanBlock) {
                $problems.Add("${name}:${line}: clean {} blocks are PowerShell 7.3+ only")
            }
            if ($node -is [System.Management.Automation.Language.ParameterAst]) { [void]$assigned.Add($node.Name.VariablePath.UserPath) }
            if ($node -is [System.Management.Automation.Language.ForEachStatementAst]) { [void]$assigned.Add($node.Variable.VariablePath.UserPath) }
            if ($node -is [System.Management.Automation.Language.StringConstantExpressionAst] -and
                ($node.Value -ieq 'utf8NoBOM' -or $node.Value -ieq 'utf8BOM')) {
                $problems.Add("${name}:${line}: -Encoding $($node.Value) does not exist in 5.1")
            }
            if ($node -is [System.Management.Automation.Language.InvokeMemberExpressionAst] -and $node.Static) {
                $member = [string]$node.Member.Value
                foreach ($bad in $netCoreOnly) { if ($member -match ('^' + $bad + '$')) { $problems.Add("${name}:${line}: [type]::$member is not in .NET Framework 4.x") } }
                $typeText = $node.Expression.Extent.Text
                if ($typeText -match 'System\.IO\.Path\]$' -and $member -eq 'Join') { $problems.Add("${name}:${line}: [IO.Path]::Join is .NET Core only") }
            }
            if ($node -is [System.Management.Automation.Language.CommandAst]) {
                $cmd = $node.GetCommandName()
                if ($cmd) {
                    $params = @($node.CommandElements | Where-Object { $_ -is [System.Management.Automation.Language.CommandParameterAst] } | ForEach-Object { $_.ParameterName })
                    foreach ($p in $params) {
                        if ($ps7Params['*'] -contains $p) { $problems.Add("${name}:${line}: $cmd -$p is PowerShell 6/7 only") }
                        if ($ps7Params.ContainsKey($cmd) -and $ps7Params[$cmd] -contains $p) { $problems.Add("${name}:${line}: $cmd -$p is PowerShell 6/7 only") }
                    }
                    if ($cmd -in @('Invoke-WebRequest', 'Invoke-RestMethod', 'iwr', 'irm') -and $params -notcontains 'UseBasicParsing') {
                        $problems.Add("${name}:${line}: $cmd needs -UseBasicParsing on 5.1")
                    }
                    if ($cmd -eq 'Join-Path') {
                        $positional = @($node.CommandElements | Select-Object -Skip 1 | Where-Object { $_ -isnot [System.Management.Automation.Language.CommandParameterAst] })
                        if ($positional.Count -gt 2) { $problems.Add("${name}:${line}: Join-Path with more than two paths is PowerShell 6+ only") }
                    }
                    if ($cmd -in @('Remove-Item', 'rm', 'del', 'rd', 'rmdir') -and ($params -contains 'Recurse')) {
                        $problems.Add("${name}:${line}: Remove-Item -Recurse can follow junctions on 5.1; use Remove-MtcTree")
                    }
                    if ($cmd -in @('Get-Error', 'Test-Json', 'ConvertFrom-Markdown', 'Get-Uptime', 'Join-String')) {
                        $problems.Add("${name}:${line}: $cmd does not exist in 5.1")
                    }
                    if ($cmd -match '^[A-Za-z]+-Mtc' -and $defined -notcontains $cmd) {
                        $problems.Add("${name}:${line}: calls undefined function $cmd")
                    }
                }
            }
        }
        foreach ($node in $all) {
            if ($node -is [System.Management.Automation.Language.VariableExpressionAst]) {
                $vp = $node.VariablePath
                if ($vp.DriveName -and $vp.DriveName -ne 'script' -and $vp.DriveName -ne 'global') { continue }
                $vname = $vp.UserPath.Split(':')[-1]
                if ($vname -in @('IsWindows', 'IsLinux', 'IsMacOS', 'IsCoreCLR', 'PSStyle')) {
                    $problems.Add(("{0}:{1}: `${2} does not exist in 5.1" -f $name, $node.Extent.StartLineNumber, $vname))
                    continue
                }
                if (-not $assigned.Contains($vname)) {
                    $problems.Add(("{0}:{1}: `${2} is never assigned (typo?)" -f $name, $node.Extent.StartLineNumber, $vname))
                }
            }
        }
    }
    foreach ($cmdFile in @(Get-ChildItem -LiteralPath $windowsDir -Filter '*.cmd' -File)) {
        $bytes = [System.IO.File]::ReadAllBytes($cmdFile.FullName)
        foreach ($b in $bytes) { if ($b -gt 127) { $problems.Add("$($cmdFile.Name): contains non-ASCII bytes"); break } }
    }
    $attributes = [System.IO.File]::ReadAllText((Join-Path $windowsDir '.gitattributes'))
    if ($attributes -notmatch '\*\.cmd\s+text\s+eol=crlf') { $problems.Add('.gitattributes must keep *.cmd as CRLF') }
    if ($problems.Count -gt 0) {
        $problems | ForEach-Object { Write-Output $_ }
        exit 1
    }
    Write-Output ('lint ok: ' + (Get-ScriptFiles).Count + ' scripts')
    exit 0
}

. (Join-Path $windowsDir 'common.ps1')

if ($Mode -eq 'parse') {
    $values = ConvertFrom-MtcSettingsText -Text ([System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8))
    $plain = @{}
    foreach ($k in $values.Keys) { $plain[$k] = $values[$k] }
    Write-Output ($plain | ConvertTo-Json -Compress)
    exit 0
}

if ($Mode -eq 'readiness') {
    $r = Get-MtcReadiness -Port $Port -TimeoutMs 3000
    $result = @{
        Reachable = $r.Reachable; StatusCode = $r.StatusCode; Ok = $r.Ok; Version = $r.Version
        Maintenance = $r.Maintenance; Reason = $r.Reason
        PortOpen = (Test-MtcPortOpen -HostName '127.0.0.1' -Port $Port)
    }
    Write-Output ($result | ConvertTo-Json -Compress)
    exit 0
}

if ($Mode -eq 'caddyfile') {
    Write-Output (New-MtcCaddyfileText -Root $Root -HostAddress $HostAddress -Port $Port)
    exit 0
}

# ----------------------------------------------------------------- unit
$failures = New-Object System.Collections.Generic.List[string]
function Check {
    param([bool]$Condition, [string]$Label)
    if (-not $Condition) { $failures.Add($Label) }
}
function Throws {
    param([scriptblock]$Block)
    try { & $Block | Out-Null; return '' } catch { return [string]$_.Exception.Message }
}

# settings.env parsing
$parsed = ConvertFrom-MtcSettingsText -Text ("# c`r`nA=1`r`n  B = 'two words'  `nC=`"x=y`"`nD=`nE=C:\MTCSupplies\data\db.sqlite3`nA=last`r`tF`t=`tg`t")
Check ($parsed['A'] -eq 'last') 'last occurrence wins'
Check ($parsed['B'] -eq 'two words') 'single quotes stripped'
Check ($parsed['C'] -eq 'x=y') 'double quotes stripped, = kept'
Check ($parsed['D'] -eq '') 'empty value'
Check ($parsed['E'] -eq 'C:\MTCSupplies\data\db.sqlite3') 'backslashes kept'
Check ($parsed['F'] -eq 'g') 'tabs trimmed'
Check ((ConvertFrom-MtcSettingsText -Text ([string][char]0xFEFF + 'K=v')).Contains('K')) 'BOM ignored'
$msg = Throws { ConvertFrom-MtcSettingsText -Text "OK=1`nDJANGO_SECRET_KEY supersecretvalue" }
Check ($msg -match 'line 2') 'malformed line reports its number'
Check ($msg -notmatch 'supersecretvalue') 'malformed line never echoes its value'
Check ((Throws { ConvertFrom-MtcSettingsText -Text '1BAD=x' }) -ne '') 'invalid key rejected'

# secret key + settings text
$secret = New-MtcSecretKey
Check ($secret -cmatch '^[0-9a-f]{64}$') 'secret is 64 lowercase hex'
Check ($secret -ne (New-MtcSecretKey)) 'secrets differ'
$settingsText = New-MtcSettingsText -Root 'C:\MTCSupplies' -HostAddress '192.168.1.10' -Port 8443 -SecretKey $secret
$s = ConvertFrom-MtcSettingsText -Text $settingsText
Check ($s['DJANGO_DEBUG'] -eq '0') 'DEBUG off'
Check ($s['DJANGO_SECRET_KEY'] -eq $secret) 'secret written'
Check ($s['DJANGO_ALLOWED_HOSTS'] -eq '192.168.1.10') 'allowed hosts'
Check ($s['DJANGO_CSRF_TRUSTED_ORIGINS'] -eq 'https://192.168.1.10:8443') 'csrf origin'
Check ($s['DJANGO_DB_PATH'] -eq 'C:\MTCSupplies\data\db.sqlite3') 'db path'
Check ($s['MTC_BACKUP_DIR'] -eq 'C:\MTCSupplies\data\backups') 'backup dir'
Check (-not $s.Contains('APP_VERSION')) 'APP_VERSION left to the VERSION file'
Check ((Throws { Assert-MtcSettingsUsable $s }) -eq '') 'generated settings usable'
$bad = ConvertFrom-MtcSettingsText -Text "DJANGO_SECRET_KEY=replace-with-a-long-random-secret"
Check ((Throws { Assert-MtcSettingsUsable $bad }) -ne '') 'placeholder secret refused'
$withVersion = ConvertFrom-MtcSettingsText -Text ("DJANGO_SECRET_KEY=$secret`nAPP_VERSION=1.0.0")
Check ((Throws { Assert-MtcSettingsUsable $withVersion }) -match 'APP_VERSION') 'APP_VERSION in settings refused'

# rewriting selected keys keeps the rest
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('mtc-unit-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    $envFile = Join-Path $tmp 'settings.env'
    Write-MtcTextFile -Path $envFile -Text $settingsText
    Set-MtcSettingValues -Path $envFile -Values @{ 'APP_HOST' = '10.0.0.5'; 'NEW_KEY' = 'x' }
    $after = Read-MtcSettings -Path $envFile
    Check ($after['APP_HOST'] -eq '10.0.0.5') 'key replaced'
    Check ($after['NEW_KEY'] -eq 'x') 'missing key appended'
    Check ($after['DJANGO_SECRET_KEY'] -eq $secret) 'secret preserved'
    Check (@(Get-ChildItem -LiteralPath $tmp -Filter 'settings.env.bak-*').Count -eq 1) 'old copy saved'
    Check ([System.IO.File]::ReadAllBytes($envFile)[0] -ne 0xEF) 'no BOM written'

    # Import: set variables win, our own earlier values refresh
    $env:MTC_UNIT_PRESET = 'from-environment'
    Write-MtcTextFile -Path $envFile -Text "MTC_UNIT_PRESET=from-file`nMTC_UNIT_NEW=one`n"
    Remove-Item Env:\MTC_LOADED_SETTINGS -ErrorAction SilentlyContinue
    $null = Import-MtcSettings -Path $envFile 6>$null
    Check ($env:MTC_UNIT_PRESET -eq 'from-environment') 'pre-set variable wins'
    Check ($env:MTC_UNIT_NEW -eq 'one') 'new variable applied'
    Write-MtcTextFile -Path $envFile -Text "MTC_UNIT_PRESET=from-file`nMTC_UNIT_NEW=two`n"
    $null = Import-MtcSettings -Path $envFile 6>$null
    Check ($env:MTC_UNIT_NEW -eq 'two') 'own earlier value refreshed from the file'
    Remove-Item Env:\MTC_UNIT_PRESET, Env:\MTC_UNIT_NEW, Env:\MTC_LOADED_SETTINGS -ErrorAction SilentlyContinue

    # release discovery and validation
    $zipRoot = Join-Path $tmp 'unzipped'
    $rel = Join-Path $zipRoot 'mtc-supplies-1.2.3'
    foreach ($d in @('config', 'deploy', (Join-Path 'deploy' 'windows'))) { New-Item -ItemType Directory -Path (Join-Path $rel $d) -Force | Out-Null }
    foreach ($f in @('manage.py', 'requirements.prod.txt', (Join-Path 'config' 'settings.py'), (Join-Path (Join-Path 'deploy' 'windows') 'serve.py'))) {
        Set-Content -LiteralPath (Join-Path $rel $f) -Value 'x'
    }
    Set-Content -LiteralPath (Join-Path $rel 'VERSION') -Value '1.2.3'
    $found = Find-MtcReleaseRoot -Dir $zipRoot
    Check ($found -eq (ConvertTo-MtcFullPath $rel)) 'release root found inside zip folder'
    $info = Get-MtcReleaseInfo -Path $found
    Check ($info.Version -eq '1.2.3') 'release version read'
    Set-Content -LiteralPath (Join-Path $rel 'VERSION') -Value 'latest'
    Check ((Throws { Get-MtcReleaseInfo -Path $found }) -match 'VERSION') 'unpinned version refused'
    Remove-Item -LiteralPath (Join-Path $rel 'manage.py')
    Check ((Throws { Find-MtcReleaseRoot -Dir $zipRoot }) -match 'manage.py') 'non-release refused'

    # install root discovery from a script inside the install
    $installRoot = Join-Path $tmp 'install'
    $scriptDir = Join-Path (Join-Path (Join-Path $installRoot 'current') 'deploy') 'windows'
    New-Item -ItemType Directory -Path $scriptDir -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $installRoot 'releases') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $installRoot 'settings.env') -Value 'A=1'
    Check ((Resolve-MtcRoot -Root '' -ScriptDir $scriptDir) -eq (ConvertTo-MtcFullPath $installRoot)) 'root found from script folder'
    Check ((Resolve-MtcRoot -Root $tmp -ScriptDir $scriptDir) -eq (ConvertTo-MtcFullPath $tmp)) 'explicit root wins'
    Check ((Resolve-MtcRoot -Root '' -ScriptDir $tmp -Default 'C:\MTCSupplies') -eq 'C:\MTCSupplies') 'default root'

    # checksum verification
    $file = Join-Path $tmp 'tool.zip'
    [System.IO.File]::WriteAllBytes($file, [byte[]](1, 2, 3, 4))
    $sha = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    Check ((Throws { Test-MtcFileHash -Path $file -Algorithm SHA256 -PinnedHash $sha -PublishedHash $sha }) -eq '') 'matching hashes accepted'
    Check ((Throws { Test-MtcFileHash -Path $file -Algorithm SHA256 -PinnedHash $sha -PublishedHash ('0' * 64) }) -match 'published') 'published mismatch refused'
    Check ((Throws { Test-MtcFileHash -Path $file -Algorithm SHA256 -PinnedHash ('0' * 64) -PublishedHash ('0' * 64) }) -match 'failed') 'download mismatch refused'
    Check ((Throws { Test-MtcFileHash -Path $file -Algorithm SHA256 -PinnedHash $sha -PublishedHash $null }) -match 'no entry') 'missing published entry refused'

    # Invoke-MtcNative merges stderr without terminating and reports exit codes
    if (Test-Path -LiteralPath '/bin/sh') {
        $r = Invoke-MtcNative -FilePath '/bin/sh' -ArgumentList @('-c', 'echo out; echo err 1>&2; exit 3') -AllowFailure -Quiet
        Check ($r.ExitCode -eq 3) 'native exit code captured'
        Check (($r.Output -match 'out') -and ($r.Output -match 'err')) 'stdout and stderr captured'
        Check ((Throws { Invoke-MtcNative -FilePath '/bin/sh' -ArgumentList @('-c', 'exit 4') -Quiet -FailMessage 'demo' }) -match 'demo \(exit code 4\)') 'non-zero exit throws'
    }
} finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

# published checksum lists (fixtures copied from the real release files)
$uvSums = "b6bd9218855591742ffd3b00fcf2044b4d2fb6446b6a3545e0dfee447e950388 *uv-aarch64-pc-windows-msvc.zip`n" +
"95f9bc30fbb3574d276e28ac4a6de932d25153645853d13da8c21eec3bc88d06 *uv-x86_64-pc-windows-msvc.zip`n"
$uvSingle = "95f9bc30fbb3574d276e28ac4a6de932d25153645853d13da8c21eec3bc88d06  uv-x86_64-pc-windows-msvc.zip`n"
$caddySums = "5e65ffc4b247f52e2f03b38c17626b7ee3f9d41fa0334c69d320903569905df7468648cfc19ee9607be1d7f5b119229fb7667fdef3da046a3622133a9bcb6905  caddy_2.10.2_windows_amd64.sbom`n" +
"88347875f0cd4b5e26bb39cd1f359613f932d54158d560e03244004d1ba6e61aae0cd625ba7c913bd46df096ef973fef2249396b0bb81143414378cb4447aeb8  caddy_2.10.2_windows_amd64.zip`n" +
"51e5e0d4f159222a4d0b70a43fbb009fefa4050a4fa269894768bbb7d147ed81e3f14449f87b843f8c4147dda1dabe24e67b34863ee666c6692b9008f9517396  caddy_2.10.2_windows_arm64.zip`n"
Check ((Get-MtcChecksumFromList -Text $uvSums -FileName $MtcUvAsset) -eq $MtcUvHash) 'uv pin matches sha256.sum'
Check ((Get-MtcChecksumFromList -Text $uvSingle -FileName $MtcUvAsset) -eq $MtcUvHash) 'uv pin matches .sha256 file'
Check ((Get-MtcChecksumFromList -Text $caddySums -FileName $MtcCaddyAsset) -eq $MtcCaddyHash) 'caddy pin matches checksums.txt'
Check ($null -eq (Get-MtcChecksumFromList -Text $caddySums -FileName 'caddy_2.10.2_windows_amd64')) 'no partial name match'
Check ($MtcUvUrl.EndsWith('/' + $MtcUvVersion + '/' + $MtcUvAsset)) 'uv url matches pin'
Check ($MtcCaddyUrl.EndsWith('/v' + $MtcCaddyVersion + '/' + $MtcCaddyAsset)) 'caddy url matches pin'
Check ($MtcCaddyHash.Length -eq 128 -and $MtcUvHash.Length -eq 64) 'hash lengths (sha512 / sha256)'

# tool version detection
Check (Test-MtcToolVersionOutput -Output 'uv 0.12.20 (0e9c1f2 2026-09-28)' -Version '0.12.20') 'uv version detected'
Check (-not (Test-MtcToolVersionOutput -Output 'uv 0.12.200 (x)' -Version '0.12.20')) 'uv longer version rejected'
Check (Test-MtcToolVersionOutput -Output 'v2.10.2 h1:g/gTYjGMD0dec' -Version '2.10.2') 'caddy version detected'
Check (-not (Test-MtcToolVersionOutput -Output 'v2.10.21 h1:x' -Version '2.10.2')) 'caddy longer version rejected'

# versions, release folders
Check (Test-MtcVersionString '1.0.0') 'version ok'
Check (-not (Test-MtcVersionString '1.0')) 'short version rejected'
Check (-not (Test-MtcVersionString '1.0.0-rc1')) 'suffix rejected'
Check ((Compare-MtcVersion '1.10.0' '1.9.9') -gt 0) 'numeric version compare'
Check ((Compare-MtcVersion '1.0.0' '1.0.0') -eq 0) 'equal versions'
Check ((Get-MtcReleaseStamp '1.0.1-20261016-101500') -eq '20261016-101500') 'release stamp'
Check ((Get-MtcReleaseVersionFromName '1.0.1-20261016-101500') -eq '1.0.1') 'release version from name'
$releases = @('1.0.0-20260101-000000', '1.0.1-20260201-000000', '1.0.2-20260301-000000', '1.0.3-20260401-000000', '1.0.4-20260501-000000') |
    ForEach-Object { [pscustomobject]@{ Name = $_; FullName = ('C:\MTCSupplies\releases\' + $_) } }
$prune = @(Select-MtcReleasesToPrune -Releases $releases -Pinned @('C:\MTCSupplies\releases\1.0.0-20260101-000000') -KeepCount 3)
Check ($prune.Count -eq 1 -and $prune[0].Name -eq '1.0.1-20260201-000000') 'prune keeps newest three plus pinned'
Check (@(Select-MtcReleasesToPrune -Releases @($releases[0]) -Pinned @() -KeepCount 3).Count -eq 0) 'nothing to prune'

# rollback backup choice
$m = { param($v, $c) [pscustomobject]@{ completed = $c; app_version = $v; sha256 = 'x' } }
$candidates = @(
    [pscustomobject]@{ Name = 'pre-update-20260101-000000.sqlite3'; FullName = 'a'; Manifest = (& $m '1.0.0' $true) },
    [pscustomobject]@{ Name = 'pre-update-20260301-000000.sqlite3'; FullName = 'b'; Manifest = (& $m '1.0.0' $true) },
    [pscustomobject]@{ Name = 'pre-update-20260401-000000.sqlite3'; FullName = 'c'; Manifest = (& $m '1.0.1' $true) },
    [pscustomobject]@{ Name = 'pre-update-20260501-000000.sqlite3'; FullName = 'd'; Manifest = (& $m '1.0.0' $false) },
    [pscustomobject]@{ Name = 'pre-update-20260601-000000.sqlite3'; FullName = 'e'; Manifest = $null },
    [pscustomobject]@{ Name = 'daily-20260701-000000.sqlite3'; FullName = 'f'; Manifest = (& $m '1.0.0' $true) }
)
Check ((Select-MtcRollbackBackup -Candidates $candidates -TargetVersion '1.0.0').FullName -eq 'b') 'newest completed backup by the target version'
Check ($null -eq (Select-MtcRollbackBackup -Candidates $candidates -TargetVersion '2.0.0')) 'no backup for unknown version'

# maintenance tokens
$hex = 'a5bf4201d9b4bbe34d13dff9d270eb6f'
Check ((Get-MtcMaintenanceToken ("maintenance on owner=update-1.0.1 token=" + $hex)) -eq $hex) 'enter token parsed'
Check ($null -eq (Get-MtcMaintenanceToken 'CommandError: maintenance entry refused: held by x')) 'no token on refusal'
$restoreOut = "Operations to perform:`n  No migrations to apply.`nrestore ok: 278528 bytes sha256=33b6a4a423a13258" + [char]0x2026 + "`nmaintenance-token: " + $hex + "`n"
Check ((Get-MtcRestoreToken $restoreOut) -eq $hex) 'restore token parsed'
Check ($null -eq (Get-MtcRestoreToken 'restore refused (backup invalid)')) 'no restore token on refusal'

# readiness parsing
$okReady = ConvertFrom-MtcReadiness -StatusCode 200 -Body '{"ok": true, "version": "1.0.1", "maintenance": true, "maintenance_owner": "update-1.0.1"}'
Check ($okReady.Ok -and $okReady.Version -eq '1.0.1' -and $okReady.Maintenance) 'ready 200 parsed'
$pending = ConvertFrom-MtcReadiness -StatusCode 503 -Body '{"ok": false, "reason": "migrations-pending", "version": "1.0.1"}'
Check ((-not $pending.Ok) -and $pending.Reason -eq 'migrations-pending') 'ready 503 parsed'
Check (-not (ConvertFrom-MtcReadiness -StatusCode 200 -Body '<html>').Ok) 'non-JSON is not ready'
Check (-not (ConvertFrom-MtcReadiness -StatusCode 301 -Body '{"ok": true}').Ok) 'redirect is not ready'

# paths and addresses
Check ($null -eq (Get-MtcRootPathProblem 'C:\MTCSupplies')) 'default root ok'
Check ($null -eq (Get-MtcRootPathProblem 'D:\Apps\MTC Supplies')) 'root with space ok'
foreach ($badRoot in @('C:\', 'C:', 'MTCSupplies', '\\server\share\mtc', 'C:\a&b', 'C:\x\..\y', "C:\it's", 'C:\a#b', 'C:\a%b', 'C:\a"b', 'C:\a\\b')) {
    Check ($null -ne (Get-MtcRootPathProblem $badRoot)) ("bad root refused: " + $badRoot)
}
foreach ($goodHost in @('192.168.1.10', '10.0.0.1', 'mtc-supplies', 'pc123.mtc.local')) { Check (Test-MtcHostAddress $goodHost) ("host ok: " + $goodHost) }
foreach ($badHost in @('300.1.1.1', 'https://x', 'a b', 'x:8443', '', '-bad')) { Check (-not (Test-MtcHostAddress $badHost)) ("host refused: " + $badHost) }
$lan = @(
    [pscustomobject]@{ IPAddress = '127.0.0.1'; RouteMetric = 1 },
    [pscustomobject]@{ IPAddress = '169.254.10.1'; RouteMetric = $null },
    [pscustomobject]@{ IPAddress = '172.20.0.1'; RouteMetric = $null },
    [pscustomobject]@{ IPAddress = '192.168.1.10'; RouteMetric = 35 },
    [pscustomobject]@{ IPAddress = '10.8.0.2'; RouteMetric = 60 }
)
Check ((Select-MtcLanAddress -Candidates $lan).IPAddress -eq '192.168.1.10') 'lowest-metric routed address chosen'
Check ((Select-MtcLanAddress -Candidates @($lan[1], $lan[2])).IPAddress -eq '172.20.0.1') 'unrouted fallback skips link-local'
Check ($null -eq (Select-MtcLanAddress -Candidates @($lan[0]))) 'loopback only gives nothing'
$sep = [System.IO.Path]::DirectorySeparatorChar
$base = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd($sep) + $sep + 'mtcroot'
Check (Test-MtcPathUnder -Path ($base + $sep + 'releases' + $sep + 'x') -Parent $base) 'path under root'
Check (Test-MtcPathUnder -Path $base -Parent $base) 'root itself is under root'
Check (-not (Test-MtcPathUnder -Path ($base + 'other') -Parent $base)) 'sibling with same prefix is not under root'

foreach ($goodUser in @('admin', 'boss.smith', 'a_b-c+d@e')) { Check (Test-MtcUsername $goodUser) ("username ok: " + $goodUser) }
foreach ($badUser in @('', 'John Smith', 'x;y', ('a' * 151))) { Check (-not (Test-MtcUsername $badUser)) ("username refused: " + $badUser) }

# arguments, task results, Caddyfile
$split = Split-MtcArguments -Raw @('import-original', 'C:\a b\x.html', '-username', 'boss', '-Yes', '--check', '-v', '2') `
    -ValueOptions @('Root', 'Username') -SwitchOptions @('Yes')
Check ($split.Options['Username'] -eq 'boss' -and $split.Options['Yes'] -eq $true) 'options extracted case-insensitively'
Check ((($split.Rest) -join '|') -eq 'import-original|C:\a b\x.html|--check|-v|2') 'other arguments kept in order'
Check ((Throws { Split-MtcArguments -Raw @('-Root') -ValueOptions @('Root') }) -match 'needs a value') 'missing option value refused'
Check ((Format-MtcTaskResult 0) -eq 'success') 'task result 0'
Check ((Format-MtcTaskResult 267009) -eq 'running') 'task result running'
Check ((Format-MtcTaskResult 5) -eq 'result 0x5') 'unknown task result shown in hex'
$caddyText = New-MtcCaddyfileText -Root 'C:\MTCSupplies' -HostAddress '192.168.1.10' -Port 8443
foreach ($needle in @('admin off', 'default_sni 192.168.1.10', 'https://192.168.1.10:8443 {', 'tls internal',
        'reverse_proxy 127.0.0.1:8000', 'header_up X-Forwarded-Proto https', 'handle /ca.crt',
        'root * "C:/MTCSupplies/data/caddy/caddy/pki/authorities/local"', 'rewrite * /root.crt', 'skip_install_trust')) {
    Check ($caddyText.Contains($needle)) ("Caddyfile contains: " + $needle)
}
Check (-not $caddyText.Contains('root.key')) 'Caddyfile never exposes the CA key'

# Start menu shortcuts and saved connection details
Check ((Get-MtcStaffUrl -HostAddress '192.168.1.10' -Port '8443') -eq 'https://192.168.1.10:8443/') 'staff url'
$elevated = New-MtcElevatedArguments -Root "C:\My Apps\O'Brien MTC" -Verb 'status'
Check ($elevated.StartsWith('-NoProfile -WindowStyle Hidden -EncodedCommand ')) 'elevated launcher is an encoded command'
$decoded = [System.Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($elevated.Split(' ')[-1]))
Check ($decoded.Contains('-Verb RunAs')) 'launcher asks for administrator rights'
Check ($decoded.Contains("""C:\My Apps\O''Brien MTC\mtc.cmd"" status & echo. & pause")) 'launcher runs mtc <verb>, quoted, then waits'
$tokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseInput($decoded, [ref]$tokens, [ref]$parseErrors) | Out-Null
Check ($parseErrors.Count -eq 0) 'launcher script parses'
$details = New-MtcConnectionText -Root 'C:\MTCSupplies' -Url 'https://192.168.1.10:8443/' -Version '1.0.1' `
    -Thumbprint 'ABC123' -BackupDir 'C:\MTCSupplies\data\backups'
foreach ($needle in @('https://192.168.1.10:8443/', 'https://192.168.1.10:8443/ca.crt', 'ABC123', 'MTC Supplies admin console', 'Administration > System status',
        'C:\MTCSupplies\data\backups', 'C:\MTCSupplies\mtc.cmd help', 'Version 1.0.1')) {
    Check ($details.Contains($needle)) ("connection details contain: " + $needle)
}
Check (-not ($details.ToCharArray() | Where-Object { [int]$_ -gt 127 })) 'connection details are plain ASCII'
$plain = New-MtcUninstallCommand -Powershell 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -Root 'C:\MTCSupplies'
Check ($plain -eq '"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "Start-Process -FilePath $env:ComSpec -ArgumentList ''/c C:\MTCSupplies\mtc.cmd uninstall & echo. & pause'' -Verb RunAs"') 'plain uninstall command for the usual folder'
$spaced = New-MtcUninstallCommand -Powershell 'C:\ps.exe' -Root 'D:\My Apps\MTC'
Check ($spaced.Contains('-EncodedCommand ') -and -not $spaced.Contains('My Apps')) 'folders needing quotes use the encoded form'
$lnkDir = Join-Path ([System.IO.Path]::GetTempPath()) ('mtc-lnk-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $lnkDir | Out-Null
try {
    $fake = Join-Path $lnkDir 'x.lnk'
    $header = New-Object byte[] 0x4C
    $header[0] = 0x4C
    $header[0x14] = 0x9B
    $header[0x15] = 0x00
    [System.IO.File]::WriteAllBytes($fake, $header)
    Set-MtcShortcutRunAsAdmin -Path $fake
    $after = [System.IO.File]::ReadAllBytes($fake)
    Check ($after[0x15] -eq 0x20 -and $after[0x14] -eq 0x9B -and $after.Length -eq 0x4C) 'run-as-administrator flag set, nothing else changed'
    Set-MtcShortcutRunAsAdmin -Path $fake
    Check ([System.IO.File]::ReadAllBytes($fake)[0x15] -eq 0x20) 'setting the flag twice is harmless'
    $notLink = Join-Path $lnkDir 'y.lnk'
    [System.IO.File]::WriteAllBytes($notLink, [byte[]](1..100))
    Check ((Throws { Set-MtcShortcutRunAsAdmin -Path $notLink }) -match 'not a Windows shortcut') 'non-shortcut file refused'
} finally {
    Remove-Item -LiteralPath $lnkDir -Recurse -Force
}
# Tray icon controls, online update, time zone
Check ((Add-MtcSddlAce -Sddl 'D:(A;;FA;;;BA)(A;;FA;;;SY)' -Ace '(A;;GRGX;;;BU)') -eq 'D:(A;;FA;;;BA)(A;;FA;;;SY)(A;;GRGX;;;BU)') 'users may run the tray jobs'
Check ((Add-MtcSddlAce -Sddl 'O:BAD:(A;;FA;;;BA)S:(AU;FA;FA;;;WD)' -Ace '(A;;GRGX;;;BU)') -eq 'O:BAD:(A;;FA;;;BA)(A;;GRGX;;;BU)S:(AU;FA;FA;;;WD)') 'rule goes into the DACL, not the SACL'
Check ((Add-MtcSddlAce -Sddl 'D:(A;;GRGX;;;BU)' -Ace '(A;;GRGX;;;BU)') -eq 'D:(A;;GRGX;;;BU)') 'rule added once'
Check (((@($MtcControlTasks.Keys)) -join ',') -eq 'start,stop,update-online') 'three fixed tray jobs'
$updateUrls = Get-MtcUpdateUrls -Repo 'Org/Repo' -Branch 'main'
Check ($updateUrls.Version -eq 'https://raw.githubusercontent.com/Org/Repo/main/VERSION') 'version check url'
Check ($updateUrls.Zip -eq 'https://github.com/Org/Repo/archive/refs/heads/main.zip') 'release zip url'
Check ((ConvertTo-MtcIanaTimeZone 'Mountain Standard Time') -eq 'America/Denver') 'Utah time zone'
Check ((ConvertTo-MtcIanaTimeZone 'New Zealand Standard Time') -eq 'Pacific/Auckland') 'NZ time zone'
Check ((ConvertTo-MtcIanaTimeZone 'Unknown Zone') -eq 'Pacific/Auckland') 'unknown zone falls back to NZ'
$tzSettings = ConvertFrom-MtcSettingsText -Text (New-MtcSettingsText -Root 'C:\MTCSupplies' -HostAddress 'h' -Port 1 -SecretKey $secret -TimeZone 'America/Denver')
Check ($tzSettings['DJANGO_TIME_ZONE'] -eq 'America/Denver') 'time zone written to settings.env'
# Pure helpers inside tray.ps1 (the rest needs Windows Forms)
$trayTokens = $null
$trayErrors = $null
$trayAst = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $windowsDir 'tray.ps1'), [ref]$trayTokens, [ref]$trayErrors)
foreach ($fn in $trayAst.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
    if ($fn.Name -in @('Test-MtcTrayNewer', 'Get-MtcTrayUpdateStatus')) { . ([scriptblock]::Create($fn.Extent.Text)) }
}
Check (Test-MtcTrayNewer -Candidate '1.0.10' -Running '1.0.9') 'tray compares versions numerically'
Check (-not (Test-MtcTrayNewer -Candidate '1.0.3' -Running '1.0.3')) 'same version is not an update'
Check (-not (Test-MtcTrayNewer -Candidate '<html>' -Running '1.0.3')) 'junk is not an update'
$script:here = Join-Path ([System.IO.Path]::GetTempPath()) ('mtc-tray-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $script:here | Out-Null
try {
    Check ((Get-MtcTrayUpdateStatus).state -eq '') 'no status file yet'
    [System.IO.File]::WriteAllText((Join-Path $script:here 'update-status.txt'), "state=running`r`nversion=1.0.4`r`nmessage=Updating to 1.0.4 = soon`r`n")
    $trayStatus = Get-MtcTrayUpdateStatus
    Check ($trayStatus.state -eq 'running' -and $trayStatus.version -eq '1.0.4' -and $trayStatus.message -eq 'Updating to 1.0.4 = soon') 'tray reads the update status'
} finally {
    Remove-Item -LiteralPath $script:here -Recurse -Force
}

Check ((New-MtcConnectionText -Root 'C:\x' -Url 'https://h:1/' -Version '1' -Thumbprint '' -BackupDir 'b').Contains('not created yet')) 'missing thumbprint explained'

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Output ('FAIL: ' + $_) }
    exit 1
}
Write-Output 'unit ok'
exit 0
