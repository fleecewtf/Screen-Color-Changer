#requires -Version 5.1
param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseRoot
)

# Negative installer fixtures only: no app launch or display-effect API calls.
# Every fixture must fail before downloading/installing the private runtime.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3
$source = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\')
$files = @('Screen Color Changer.pyw', 'color_math.py', 'screen_backend.py', 'Installer.bat', 'LICENSE', 'READ ME.txt', 'requirements-win-x64.txt', 'requirements-win-arm64.txt')
$base = Join-Path ([IO.Path]::GetTempPath()) ('f' + [Guid]::NewGuid().ToString('N').Substring(0, 6))
if ($base.Length -gt 60) { throw "The test fixture root is too long: $base" }
[IO.Directory]::CreateDirectory($base) | Out-Null
$script:passed = 0

function New-Fixture([string]$Name, [string[]]$Omit = @()) {
    $target = Join-Path $base $Name
    [IO.Directory]::CreateDirectory($target) | Out-Null
    foreach ($relative in $files) {
        if ($relative -in $Omit) { continue }
        $from = Join-Path $source $relative
        if (-not [IO.File]::Exists($from)) { throw "Missing release input: $relative" }
        [IO.File]::Copy($from, (Join-Path $target $relative))
    }
    return $target
}

function Assert-EarlyFailure([string]$Folder, [string]$Expected) {
    Push-Location -LiteralPath $Folder
    try {
        $output = (& $env:ComSpec /d /c 'call "Installer.bat" --yes --no-pause' 2>&1 | Out-String)
        $code = $LASTEXITCODE
    } finally { Pop-Location }
    if ($code -eq 0) { throw "Setup unexpectedly passed in $Folder" }
    if ($output -notmatch [regex]::Escape($Expected)) { throw "Missing expected error '$Expected' in $Folder`n$output" }
    if ($output -notmatch 'How to fix it:') { throw "Missing repair guidance in $Folder`n$output" }
    if ($output -match 'Downloading and preparing private Python') { throw "Setup attempted to download Python before rejecting $Folder" }
    $log = Join-Path $Folder 'setup.log'
    if ([IO.File]::Exists($log)) {
        $logText = [IO.File]::ReadAllText($log)
        if ($logText -notmatch 'HOW TO FIX:') { throw "Repair guidance was not logged in $Folder" }
        if ($logText -match 'Downloading:') { throw "Setup attempted a download before rejecting $Folder" }
        if ($logText -match 'CommandNotFoundException|not recognized as the name of a cmdlet') {
            throw "A broken PowerShell prerequisite cannot count as the intended negative test in $Folder"
        }
    }
    $runtime = Join-Path $Folder '.runtime\python'
    if ([IO.Directory]::Exists($runtime)) { throw "Setup prepared Python before rejecting $Folder" }
    $downloads = Join-Path $Folder '.runtime\downloads'
    if ([IO.Directory]::Exists($downloads) -and @([IO.Directory]::EnumerateFileSystemEntries($downloads)).Count -ne 0) {
        throw "Setup left downloaded files before rejecting $Folder"
    }
    $script:passed += 1
    Write-Host ('Passed negative preflight: ' + [IO.Path]::GetFileName($Folder))
}

$nativeArch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
$selectedArch = switch ($nativeArch) {
    'AMD64' { 'x64' }
    'ARM64' { 'arm64' }
    default { throw 'Dependency-lock preflight fixtures require a supported x64 or ARM64 Windows host.' }
}
$selectedLock = "requirements-win-$selectedArch.txt"
# Prove a good lock passes the ACTUAL early helper first. Otherwise a missing
# PowerShell dependency could reject all inputs and fake negative-test success.
$installerText = [IO.File]::ReadAllText((Join-Path $source 'Installer.bat'))
$helper = [regex]::Match($installerText, '(?ms)^:CheckDependencyLock\r?\n(?<body>.*?)(?=^:)')
$commandMatch = [regex]::Match($helper.Groups['body'].Value, '(?m)^"%POWERSHELL_EXE%" .*? -Command "(?<command>.*)" >>"%LOG%" 2>&1\r?$')
$digestMatch = [regex]::Match($installerText, '(?m)^if /I "%ARCH%"=="' + [regex]::Escape($selectedArch) + '" set "PIP_REQUIREMENTS_SHA256=(?<hash>[0-9a-fA-F]{64})"\r?$')
if (-not $helper.Success -or -not $commandMatch.Success -or -not $digestMatch.Success) {
    throw 'Could not extract the actual installer early lock check and selected expected digest.'
}
$trustedPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$priorLock = $env:PIP_REQUIREMENTS
$priorDigest = $env:PIP_REQUIREMENTS_SHA256
try {
    # Native stderr is represented as ErrorRecord in Windows PowerShell 5.1.
    # Capture an expected negative result instead of aborting before checking it.
    $priorNativePreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $env:PIP_REQUIREMENTS_SHA256 = $digestMatch.Groups['hash'].Value
    $positiveLock = New-Fixture 'positive lock'
    $env:PIP_REQUIREMENTS = Join-Path $positiveLock $selectedLock
    $output = (& $trustedPowerShell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command $commandMatch.Groups['command'].Value 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "The official dependency lock was incorrectly rejected by the actual early hash check.`n$output" }
    $alteredLock = New-Fixture 'probe altered'
    $env:PIP_REQUIREMENTS = Join-Path $alteredLock $selectedLock
    [IO.File]::AppendAllText($env:PIP_REQUIREMENTS, "# altered`n")
    $output = (& $trustedPowerShell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command $commandMatch.Groups['command'].Value 2>&1 | Out-String)
    if ($LASTEXITCODE -eq 0 -or $output -match 'CommandNotFoundException|not recognized as the name of a cmdlet') {
        throw "The actual early hash check failed to reject altered lock content correctly.`n$output"
    }
} finally {
    $ErrorActionPreference = $priorNativePreference
    if ($null -eq $priorLock) { Remove-Item Env:PIP_REQUIREMENTS -ErrorAction SilentlyContinue } else { $env:PIP_REQUIREMENTS = $priorLock }
    if ($null -eq $priorDigest) { Remove-Item Env:PIP_REQUIREMENTS_SHA256 -ErrorAction SilentlyContinue } else { $env:PIP_REQUIREMENTS_SHA256 = $priorDigest }
}
Write-Host 'Actual early dependency-lock helper accepted official content and rejected altered content under Windows PowerShell before any downloads.'

$sources = @('Screen Color Changer.pyw', 'color_math.py', 'screen_backend.py')
for ($index = 0; $index -lt $sources.Count; $index++) {
    $relative = $sources[$index]
    $missing = New-Fixture ("missing $index") @($relative)
    Assert-EarlyFailure $missing 'One or more bundled app source files are missing'
    $invalid = New-Fixture ("invalid $index")
    $bytes = if ($index -eq 1) { [byte[]](0x00, 0x01) } else { [byte[]](0xFF, 0xFE) }
    [IO.File]::WriteAllBytes((Join-Path $invalid $relative), $bytes)
    Assert-EarlyFailure $invalid 'A bundled app source file is unreadable, linked, empty, or invalid'
}
$unsafeShortcut = New-Fixture 'unsafe shortcut'
[IO.Directory]::CreateDirectory((Join-Path $unsafeShortcut 'Screen Color Changer.lnk')) | Out-Null
Assert-EarlyFailure $unsafeShortcut 'shortcut is unsafe'
$longFixture = New-Fixture ('x' * 70)
if ($longFixture.Length -le 72) { throw 'The long-path fixture did not exceed the limit.' }
Assert-EarlyFailure $longFixture '72 characters or fewer'
$missingLicense = New-Fixture 'missing license' @('LICENSE')
Assert-EarlyFailure $missingLicense 'bundled Tool License is missing'

$missingLock = New-Fixture 'missing lock' @($selectedLock)
Assert-EarlyFailure $missingLock 'reviewed dependency lock is missing, unsafe, or changed'
$changedLock = New-Fixture 'altered lock'
[IO.File]::AppendAllText((Join-Path $changedLock $selectedLock), "# altered`n")
Assert-EarlyFailure $changedLock 'reviewed dependency lock is missing, unsafe, or changed'
$directoryLock = New-Fixture 'directory lock' @($selectedLock)
[IO.Directory]::CreateDirectory((Join-Path $directoryLock $selectedLock)) | Out-Null
Assert-EarlyFailure $directoryLock 'reviewed dependency lock is missing, unsafe, or changed'

Write-Host "Screen Color Changer: $script:passed negative preflight cases passed before Python download ($selectedArch host); fixtures: $base"
exit 0
