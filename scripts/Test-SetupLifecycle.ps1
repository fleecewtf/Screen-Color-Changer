#requires -Version 5.1
param(
    [string]$ReleaseRoot = (Join-Path $PSScriptRoot '..'),
    [string]$ReleaseArchive,
    [switch]$FullSetup
)

# Disposable, folder-local installer checks. Never runs an optimization,
# uninstaller, or native display change. All fixtures remain for diagnosis.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3
$source = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\')
$base = Join-Path ([IO.Path]::GetTempPath()) ('s' + [Guid]::NewGuid().ToString('N').Substring(0, 6))
[IO.Directory]::CreateDirectory($base) | Out-Null
$batch = Join-Path $base 'probe.bat'
$body = @'
@echo off
setlocal EnableExtensions DisableDelayedExpansion
call :Echo "%%ROOT%%"
exit /b %ERRORLEVEL%
:Echo
echo "%~1"
exit /b 0
'@
[IO.File]::WriteAllText($batch, $body.Replace("`n", "`r`n"), [Text.UTF8Encoding]::new($false))
$priorRoot = $env:ROOT
$priorMarker = $env:FLEECE_SETUP_PATH_PROBE
try {
    $env:FLEECE_SETUP_PATH_PROBE = 'must-not-expand'
    foreach ($name in @('a&b', 'a^b', 'a%FLEECE_SETUP_PATH_PROBE%b', 'a!b', 'a(b)', 'a b')) {
        $env:ROOT = Join-Path $base $name
        $output = (& $env:ComSpec /d /c ('"' + $batch + '"') 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $output -cne ('"' + $env:ROOT + '"')) {
            throw "Deferred CALL did not preserve literal path $name : $output"
        }
    }
} finally {
    if ($null -eq $priorRoot) { Remove-Item Env:ROOT -ErrorAction SilentlyContinue } else { $env:ROOT = $priorRoot }
    if ($null -eq $priorMarker) { Remove-Item Env:FLEECE_SETUP_PATH_PROBE -ErrorAction SilentlyContinue } else { $env:FLEECE_SETUP_PATH_PROBE = $priorMarker }
}
Write-Host "Literal CALL path regression passed; fixture: $base"

$installer = [IO.File]::ReadAllText((Join-Path $source 'Installer.bat'))
$appMatch = [regex]::Match($installer, '(?m)^set "APP_FILE=%ROOT%(?<name>[^"\r\n]+)"')
if (-not $appMatch.Success) { throw 'Installer app filename not found.' }
$appName = $appMatch.Groups['name'].Value
$appStem = [IO.Path]::GetFileNameWithoutExtension($appName)
$sources = if ($appStem -eq 'Screen Color Changer') { @($appName, 'color_math.py', 'screen_backend.py') } else { @($appName, 'optimizer_core.py', 'debloat_core.py') }
$files = $sources + @('Installer.bat', 'LICENSE', 'READ ME.txt', 'requirements-win-x64.txt', 'requirements-win-arm64.txt')
if ($appStem -eq 'Screen Color Changer') { $files += 'THIRD_PARTY_NOTICES.txt' }
$script:checks = 0
$releaseInputs = $source
if ($ReleaseArchive) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead([IO.Path]::GetFullPath($ReleaseArchive))
    try {
        $entries = @($archive.Entries | ForEach-Object FullName)
        if ($entries.Count -ne $files.Count -or (@($entries | Select-Object -Unique)).Count -ne $files.Count -or (Compare-Object $files $entries)) {
            throw 'Release archive must contain exactly the reviewed root-level app files.'
        }
        $releaseInputs = Join-Path $base 'archive-inputs'
        [IO.Directory]::CreateDirectory($releaseInputs) | Out-Null
        foreach ($entry in $archive.Entries) {
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $releaseInputs $entry.FullName))
        }
    } finally { $archive.Dispose() }
    foreach ($relative in $files) {
        $releaseHash = (Get-FileHash -LiteralPath (Join-Path $releaseInputs $relative) -Algorithm SHA256).Hash
        $sourceHash = (Get-FileHash -LiteralPath (Join-Path $source $relative) -Algorithm SHA256).Hash
        if ($releaseHash -cne $sourceHash) {
            if ($relative -ne 'Installer.bat' -or [IO.File]::ReadAllText((Join-Path $releaseInputs $relative)).Replace("`r`n", "`n") -cne $installer.Replace("`r`n", "`n")) {
                throw "Release archive does not match the reviewed source: $relative"
            }
        }
    }
    $script:checks += 1
    Write-Host 'Exact release ZIP inspected and extracted; all lifecycle fixtures use its verified files.'
}

function New-Fixture([string]$Name) {
    $target = Join-Path $base $Name
    [IO.Directory]::CreateDirectory($target) | Out-Null
    foreach ($relative in $files) { [IO.File]::Copy((Join-Path $releaseInputs $relative), (Join-Path $target $relative)) }
    return $target
}

function Invoke-Setup([string]$Folder, [int]$ExpectedCode = 0, [string]$ExpectedText = 'ALL SET, YOU ARE READY', [string]$Options = '--yes --no-pause') {
    Push-Location -LiteralPath $Folder
    try {
        $output = (& $env:ComSpec /d /c ('Installer.bat ' + $Options) 2>&1 | Out-String)
        $code = $LASTEXITCODE
    } finally { Pop-Location }
    if ($code -ne $ExpectedCode -or $output -notmatch [regex]::Escape($ExpectedText)) {
        throw "Installer check failed in $Folder (exit $code, expected $ExpectedCode):`n$output"
    }
    $script:checks += 1
    Write-Host "Passed installer lifecycle case: $Folder"
    return $output
}

function Invoke-ConsentProbe([string]$Folder, [string]$Answer, [string]$ExpectedText) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = Join-Path $env:SystemRoot 'System32\cmd.exe'
    $info.Arguments = '/d /c Installer.bat --no-pause'
    $info.WorkingDirectory = $Folder
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = [Diagnostics.Process]::Start($info)
    try {
        $process.StandardInput.WriteLine($Answer)
        $process.StandardInput.Close()
        if (-not $process.WaitForExit(15000)) { throw 'Interactive consent fixture timed out.' }
        $output = $process.StandardOutput.ReadToEnd() + $process.StandardError.ReadToEnd()
        if ($process.ExitCode -ne 1 -or $output -notmatch [regex]::Escape($ExpectedText) -or $output -notmatch 'Continue with install or repair\? \[Y/N\]:' -or $output -match 'Downloading and preparing') {
            throw "Interactive choice/stdin/stdout failed for $Answer : $output"
        }
        $script:checks += 1
    } finally {
        if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
        $process.Dispose()
    }
}

function Get-HelperRange([string]$Start, [string]$End) {
    $match = [regex]::Match($installer, '(?ms)^:' + [regex]::Escape($Start) + '\r?\n.*?(?=^:' + [regex]::Escape($End) + '\r?\n)')
    if (-not $match.Success) { throw "Cannot extract actual installer helper $Start" }
    return $match.Value.TrimEnd()
}

function Assert-SavedColorProfile([string]$Folder) {
    if ($appStem -ne 'Screen Color Changer') { return }
    $code = @'
import importlib.machinery, importlib.util, os, sys
from pathlib import Path
from types import SimpleNamespace
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
root = Path(sys.argv[1])
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
loader = importlib.machinery.SourceFileLoader('fixture_color_profile', str(root / 'Screen Color Changer.pyw'))
spec = importlib.util.spec_from_loader(loader.name, loader)
ui = importlib.util.module_from_spec(spec)
loader.exec_module(ui)
assert ui.SETTINGS_PATH == root / '.runtime' / 'settings.ini'
app = QApplication(['fixture-profile-readback'])
window = ui.ColorWindow(testing=True, effect=SimpleNamespace(active=False))
window._settings = QSettings(str(ui.SETTINGS_PATH), QSettings.IniFormat)
window._restore_values()
assert vars(window.values()) == dict(saturation=125, contrast=110, brightness=5, hue=7, gamma=103)
print('Actual app profile reload passed with non-neutral preferences; native effects were not initialized.')
'@
    & (Join-Path $Folder '.runtime\python\python.exe') -I -c $code $Folder
    if ($LASTEXITCODE -ne 0) { throw 'Actual app did not reload the preserved fixture profile.' }
}

function Invoke-ActualHelper([string]$Fixture, [string]$Entry, [string]$Helpers, [int]$ExpectedCode = 0) {
    $wrapper = Join-Path $Fixture 'helper.bat'
    $prefix = @"
@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "ROOT=$Fixture\"
set "RUNTIME=$Fixture\.runtime"
set "PYTHON_DIR=$Fixture\.runtime\python"
set "CANDIDATE=$Fixture\.runtime\python"
set "POWERSHELL_EXE=$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
set "ROBOCOPY_EXE=$env:SystemRoot\System32\robocopy.exe"
set "LOG=$Fixture\helper.log"
set "DIAGNOSTIC_LOG=$Fixture\helper.log"
set "PATHS_VALIDATED=1"
set "ENV_MODE=embedded"
call :$Entry
exit /b %ERRORLEVEL%
:RuntimeTree
call :ValidateEmbeddedPythonAt "%%CANDIDATE%%"
exit /b %ERRORLEVEL%
"@
    [IO.File]::WriteAllText($wrapper, ($prefix + "`n" + $Helpers + "`n").Replace("`r`n", "`n").Replace("`n", "`r`n"), [Text.UTF8Encoding]::new($false))
    Push-Location -LiteralPath $Fixture
    try {
        $output = (& $env:ComSpec /d /c 'helper.bat' 2>&1 | Out-String)
        if ($LASTEXITCODE -ne $ExpectedCode) { throw "Actual helper $Entry exited $LASTEXITCODE, expected $ExpectedCode : $output" }
    } finally { Pop-Location }
}

# A private runtime must be a safe tree BEFORE any runtime executable is invoked.
$linked = New-Fixture 'linked'
$linkedRuntime = Join-Path $linked '.runtime\python'
[IO.Directory]::CreateDirectory($linkedRuntime) | Out-Null
$outside = Join-Path $base 'outside'
[IO.Directory]::CreateDirectory($outside) | Out-Null
$guard = Join-Path $outside 'guard.txt'
[IO.File]::WriteAllText($guard, 'outside fixture must remain untouched')
New-Item -ItemType Junction -Path (Join-Path $linkedRuntime 'nested-link') -Target $outside | Out-Null
$treeHelpers = (Get-HelperRange 'ValidateEmbeddedPythonAt' 'InstallEmbedPy') + "`n" + (Get-HelperRange 'ValidatePrivateTree' 'ReplaceDirectory')
Invoke-ActualHelper $linked 'RuntimeTree' $treeHelpers 1
$linkedLog = [IO.File]::ReadAllText((Join-Path $linked 'helper.log'))
if ($linkedLog -notmatch 'Unsafe private reparse point' -or [IO.File]::ReadAllText($guard) -cne 'outside fixture must remain untouched') {
    throw 'Runtime did not reject the nested link before proceeding, or changed outside data.'
}
$script:checks += 1
Write-Host 'Nested runtime junction rejected before execution; outside fixture unchanged.'

# Exercise the REAL rollback helpers using tiny, inert runtime fixture files.
# No Python executable from this fixture is ever started.
$transaction = New-Fixture 'rollback'
$target = Join-Path $transaction '.runtime\python'
[IO.Directory]::CreateDirectory($target) | Out-Null
[IO.File]::WriteAllBytes((Join-Path $target 'python.exe'), [byte[]](0x4D, 0x5A))
$payload = Join-Path $target 'package.txt'
[IO.File]::WriteAllText($payload, 'original package fixture')
$transactionHelpers = (Get-HelperRange 'RecoverPackageTransaction' 'SelectPipLockDigest') + "`n" + (Get-HelperRange 'RemoveDirectoryRobust' 'DownloadAndVerify') + "`n" + (Get-HelperRange 'LogCurrent' 'PauseIfNeeded')
Invoke-ActualHelper $transaction 'BeginPackageTransaction' $transactionHelpers
[IO.File]::WriteAllText($payload, 'partially repaired package fixture')
Invoke-ActualHelper $transaction 'RecoverPackageTransaction' $transactionHelpers
if ([IO.File]::ReadAllText($payload) -cne 'original package fixture' -or [IO.Directory]::Exists((Join-Path $transaction '.runtime\b'))) {
    throw 'Interrupted package transaction did not restore original data and consume its backup.'
}
$script:checks += 1
Write-Host 'Interrupted package transaction restored original fixture bytes and removed its consumed backup.'

# Recovery must run BEFORE stage one decides a missing live Python needs a
# download. Model interruptions between directory moves using inert bytes.
$recoveryHelpers = (Get-HelperRange 'RecoverInterruptedRuntime' 'InstallPythonPackages') + "`n" + $transactionHelpers
foreach ($kind in @('runtime-old', 'package-between-moves')) {
    $recoveryFixture = New-Fixture $kind
    $oldRuntime = Join-Path $recoveryFixture '.runtime\python.old'
    [IO.Directory]::CreateDirectory($oldRuntime) | Out-Null
    [IO.File]::WriteAllBytes((Join-Path $oldRuntime 'python.exe'), [byte[]](0x4D, 0x5A))
    [IO.File]::WriteAllText((Join-Path $oldRuntime 'package.txt'), 'previous runtime fixture')
    $expected = 'previous runtime fixture'
    if ($kind -eq 'package-between-moves') {
        $rollback = Join-Path $recoveryFixture '.runtime\b'
        [IO.Directory]::CreateDirectory($rollback) | Out-Null
        [IO.File]::WriteAllBytes((Join-Path $rollback 'python.exe'), [byte[]](0x4D, 0x5A))
        [IO.File]::WriteAllText((Join-Path $rollback 'package.txt'), 'original package rollback fixture')
        $expected = 'original package rollback fixture'
    }
    Invoke-ActualHelper $recoveryFixture 'RecoverInterruptedRuntime' $recoveryHelpers
    $restored = Join-Path $recoveryFixture '.runtime\python\package.txt'
    if ([IO.File]::ReadAllText($restored) -cne $expected -or [IO.Directory]::Exists($oldRuntime) -or [IO.Directory]::Exists((Join-Path $recoveryFixture '.runtime\b'))) {
        throw "Early local recovery failed for $kind."
    }
    $script:checks += 1
}
$stepOne = $installer.IndexOf('[ STEP 1 / 3 ]')
$recoverAt = $installer.IndexOf('call :RecoverInterruptedRuntime', $stepOne)
$validateAt = $installer.IndexOf('call :ValidateEmbeddedPython', $stepOne)
$downloadAt = $installer.IndexOf('call :InstallEmbedPy', $stepOne)
if ($recoverAt -lt 0 -or $recoverAt -ge $validateAt -or $validateAt -ge $downloadAt) {
    throw 'Recovery, recovered-runtime identity validation, and downloads are not ordered safely.'
}
$script:checks += 1
Write-Host 'Missing-live-runtime and interrupted-directory recovery passed locally before runtime validation/downloads.'

# Exercise the ACTUAL setup guard against inert child commands and unique
# named mutexes. This does not start the app or mutate its working runtime.
$guardMatch = [regex]::Match($installer, '(?ms)^# FLEECE_SETUP_GATE_BEGIN\r?\n(?<body>.*?)^# FLEECE_SETUP_GATE_END\r?$')
if (-not $guardMatch.Success) { throw 'Installer startup/repair guard payload is missing.' }
$suffix = [Guid]::NewGuid().ToString('N')
$testGate = 'Local\FleeceScreenColorChangerSetupGate_Test_' + $suffix
$testApp = 'Local\FleeceScreenColorChangerApp_Test_' + $suffix
$guardBody = $guardMatch.Groups['body'].Value.Replace('Local\FleeceScreenColorChangerSetupGate', $testGate).Replace('Local\FleeceScreenColorChangerApp', $testApp).Replace('Global\FleeceScreenColorChangerApp', ('Global\FleeceScreenColorChangerApp_Test_' + $suffix))
$guardScript = Join-Path $base 'guard.ps1'
[IO.File]::WriteAllText($guardScript, $guardBody, [Text.UTF8Encoding]::new($false))
$guardChild = Join-Path $base 'guard-child.bat'
$guardChildBody = @'
@echo off
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -Command "$self=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $PID);[IO.File]::WriteAllText($env:FLEECE_GATE_PID_FILE,[string]$self.ParentProcessId);Start-Sleep -Seconds ([int]$env:FLEECE_GATE_DELAY)"
exit /b 0
'@
[IO.File]::WriteAllText($guardChild, $guardChildBody.Replace("`n", "`r`n"), [Text.UTF8Encoding]::new($false))

function Start-GuardProbe([string]$PidFile, [int]$Delay) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $info.Arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $guardScript + '"'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.EnvironmentVariables['INSTALLER_SELF'] = $guardChild
    $info.EnvironmentVariables['SETUP_CHILD_ARGS'] = '--inert-fixture'
    $info.EnvironmentVariables['NO_PAUSE'] = '1'
    $info.EnvironmentVariables['FLEECE_GATE_PID_FILE'] = $PidFile
    $info.EnvironmentVariables['FLEECE_GATE_DELAY'] = [string]$Delay
    return [Diagnostics.Process]::Start($info)
}

function Wait-GuardChild([Diagnostics.Process]$Wrapper, [string]$PidFile) {
    $deadline = [DateTime]::UtcNow.AddSeconds(12)
    while (-not [IO.File]::Exists($PidFile)) {
        if ($Wrapper.HasExited -or [DateTime]::UtcNow -gt $deadline) {
            throw ('Guard child did not start: ' + $Wrapper.StandardOutput.ReadToEnd() + $Wrapper.StandardError.ReadToEnd())
        }
        Start-Sleep -Milliseconds 50
    }
    return [int][IO.File]::ReadAllText($PidFile)
}

$mockApp = New-Object Threading.Mutex($false, $testApp)
$wrapper = $null
try {
    $blockedPid = Join-Path $base 'guard-blocked.pid'
    $wrapper = Start-GuardProbe $blockedPid 1
    if (-not $wrapper.WaitForExit(12000) -or $wrapper.ExitCode -ne 1 -or [IO.File]::Exists($blockedPid)) { throw 'Setup guard did not reject an already-open app before starting work.' }
    $guardOutput = $wrapper.StandardOutput.ReadToEnd() + $wrapper.StandardError.ReadToEnd()
    if ($guardOutput -notmatch 'Screen Color Changer is open') { throw "Unexpected app-open guard result: $guardOutput" }
    $script:checks += 1
} finally {
    if ($wrapper) { if (-not $wrapper.HasExited) { $wrapper.Kill(); $wrapper.WaitForExit() }; $wrapper.Dispose() }
    $mockApp.Dispose()
}

$wrapper = $null
$gateProbe = $null
try {
    $normalPid = Join-Path $base 'guard-normal.pid'
    $wrapper = Start-GuardProbe $normalPid 2
    $normalChild = Wait-GuardChild $wrapper $normalPid
    $gateProbe = [Threading.Mutex]::OpenExisting($testGate)
    if ($gateProbe.WaitOne(0)) { $gateProbe.ReleaseMutex(); throw 'Setup did not retain exclusive startup/repair ownership while its child ran.' }
    $script:checks += 1
    if (-not $wrapper.WaitForExit(12000) -or $wrapper.ExitCode -ne 0) { throw 'Inert setup child did not finish normally.' }
    if (-not $gateProbe.WaitOne(0)) { throw 'Setup did not release its startup/repair gate after completion.' }
    $gateProbe.ReleaseMutex()
    $script:checks += 1
} finally {
    if ($wrapper) { if (-not $wrapper.HasExited) { $wrapper.Kill(); $wrapper.WaitForExit() }; $wrapper.Dispose() }
    if ($gateProbe) { $gateProbe.Dispose() }
}

$wrapper = $null
$gateProbe = $null
try {
    $crashPid = Join-Path $base 'guard-crash.pid'
    $wrapper = Start-GuardProbe $crashPid 60
    $crashChild = Wait-GuardChild $wrapper $crashPid
    $gateProbe = [Threading.Mutex]::OpenExisting($testGate)
    $wrapper.Kill()
    if (-not $wrapper.WaitForExit(5000)) { throw 'Disposable guard process did not terminate.' }
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    while ((Get-Process -Id $crashChild -ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }
    if (Get-Process -Id $crashChild -ErrorAction SilentlyContinue) { throw 'A terminated guard left its installer child running without exclusion.' }
    $script:checks += 1
    $reacquired = $false
    try { $reacquired = $gateProbe.WaitOne(0) } catch [Threading.AbandonedMutexException] { $reacquired = $true }
    if (-not $reacquired) { throw 'A terminated setup guard left startup/repair locked.' }
    $gateProbe.ReleaseMutex()
    $script:checks += 1
} finally {
    if ($wrapper) { if (-not $wrapper.HasExited) { $wrapper.Kill(); $wrapper.WaitForExit() }; $wrapper.Dispose() }
    if ($gateProbe) { $gateProbe.Dispose() }
}
Write-Host 'Actual guard rejected an open app, held/released exclusive ownership, killed its child on termination, and allowed abandoned-gate recovery.'

# Real early failures exercise the dedicated child with literal shell characters,
# not just an isolated helper. No network activity is permitted in these cases.
$priorProbe = $env:P
try {
    $env:P = 'must-not-expand'
    foreach ($name in @('p%P%', 'c^d', 'a&b', 'b!d', 'a(b)')) {
        $target = New-Fixture $name
        [IO.File]::AppendAllText((Join-Path $target 'requirements-win-x64.txt'), "# changed`n")
        [IO.File]::AppendAllText((Join-Path $target 'requirements-win-arm64.txt'), "# changed`n")
        $expected = if ($name.Contains('%')) { 'folder path cannot contain percent signs' } else { 'reviewed dependency lock is missing, unsafe, or changed' }
        $output = Invoke-Setup $target 1 $expected
        if ($output -match 'Downloading and preparing') { throw 'An invalid lock reached downloads.' }
    }
} finally {
    if ($null -eq $priorProbe) { Remove-Item Env:P -ErrorAction SilentlyContinue } else { $env:P = $priorProbe }
}
$unknown = New-Fixture 'unknown'
$null = Invoke-Setup $unknown 2 'Unknown setup option' '--unexpected --no-pause'
if ([IO.Directory]::Exists((Join-Path $unknown '.runtime'))) { throw 'Unknown arguments created runtime files.' }

# Normal setup (without --yes) must still receive keyboard input and display
# its prompt through the guarded job, including literal ^/! in its folder.
$declined = New-Fixture 'no^!'
Invoke-ConsentProbe $declined 'N' 'SETUP CANCELLED'
if ([IO.Directory]::Exists((Join-Path $declined '.runtime')) -or [IO.File]::Exists((Join-Path $declined 'setup.log'))) { throw 'Declining consent created private runtime/log files.' }
$accepted = New-Fixture 'yes^!'
foreach ($lock in @('requirements-win-x64.txt', 'requirements-win-arm64.txt')) { [IO.File]::AppendAllText((Join-Path $accepted $lock), "# altered fixture`n") }
Invoke-ConsentProbe $accepted 'Y' 'reviewed dependency lock is missing, unsafe, or changed'
Write-Host 'Actual guarded setup preserved interactive N/Y choice, prompt output, and literal caret/exclamation folders before any downloads.'

# Extract the ACTUAL runtime identity expression; reject the wrong architecture
# before retaining a runtime copied from a different Windows machine.
$identity = [regex]::Match($installer, '(?m)^"%~1\\python.exe" -I -c "(?<code>.*)" >>"%LOG%" 2>&1\r?$')
if (-not $identity.Success) { throw 'Runtime identity check not found.' }
$nativeArch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
$arch = if ($nativeArch -eq 'ARM64') { 'arm64' } else { 'x64' }
$priorArch = $env:ARCH
try {
    $env:ARCH = $arch
    & (Join-Path $source '.runtime\python\python.exe') -I -c $identity.Groups['code'].Value
    if ($LASTEXITCODE -ne 0) { throw 'Installed private runtime failed its native architecture identity.' }
    $env:ARCH = if ($arch -eq 'x64') { 'arm64' } else { 'x64' }
    & (Join-Path $source '.runtime\python\python.exe') -I -c $identity.Groups['code'].Value
    if ($LASTEXITCODE -eq 0) { throw 'A wrong-architecture runtime was accepted.' }
    $script:checks += 2
} finally {
    if ($null -eq $priorArch) { Remove-Item Env:ARCH -ErrorAction SilentlyContinue } else { $env:ARCH = $priorArch }
}

# Release development helpers must also work with the bundled Windows
# PowerShell 5.1. Test actual committed-blob reading and actual ZIP verification
# without requiring a clean working checkout or building a public release.
$buildText = [IO.File]::ReadAllText((Join-Path $source 'scripts\Build-ReleaseZip.ps1'))
$blobHelper = [regex]::Match($buildText, '(?ms)^function Read-TrackedBlob\(.*?(?=^\$outputDirectory =)')
$zipVerifier = [regex]::Match($buildText, '(?ms)^    \$check = \[IO\.Compression\.ZipFile\]::OpenRead\(\$output\)\r?\n.*?^    } finally \{ \$check.Dispose\(\) \}\r?$')
if (-not $blobHelper.Success -or -not $zipVerifier.Success) { throw 'Release compatibility helpers could not be extracted.' }
$root = $source
. ([scriptblock]::Create($blobHelper.Value))
$blob = [byte[]](Read-TrackedBlob 'Screen Color Changer.pyw')
if ($blob.Length -lt 1) { throw 'Release helper returned an empty committed source.' }
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$paths = @('Screen Color Changer.pyw')
$committed = @{ 'Screen Color Changer.pyw' = $blob }
$output = Join-Path $base 'compatibility.zip'
$stream = [IO.File]::Open($output, [IO.FileMode]::CreateNew)
try {
    $archive = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Create, $true)
    try {
        $entry = $archive.CreateEntry($paths[0])
        $entryStream = $entry.Open()
        try { $entryStream.Write($blob, 0, $blob.Length) } finally { $entryStream.Dispose() }
    } finally { $archive.Dispose() }
} finally { $stream.Dispose() }
& ([scriptblock]::Create($zipVerifier.Value))
$script:checks += 1
$archive = [IO.Compression.ZipFile]::Open($output, [IO.Compression.ZipArchiveMode]::Update)
try {
    $archive.GetEntry($paths[0]).Delete()
    $entry = $archive.CreateEntry($paths[0])
    $entryStream = $entry.Open()
    try { $entryStream.WriteByte(42) } finally { $entryStream.Dispose() }
} finally { $archive.Dispose() }
$rejected = $false
try { & ([scriptblock]::Create($zipVerifier.Value)) } catch {
    if ($_.Exception.Message -notmatch 'Archive content mismatch') { throw }
    $rejected = $true
}
if (-not $rejected) { throw 'Actual release ZIP verification accepted modified entry bytes.' }
$script:checks += 1
Write-Host 'Actual committed-blob helper and ZIP hash verifier passed; corrupted archive rejected with compatible APIs.'

if ($FullSetup) {
    $priorProbe = $env:P
    try {
        $env:P = 'must-not-expand'
        $clean = New-Fixture 'ok^!'
        $null = Invoke-Setup $clean
        # Installer repair/move must preserve fixture preferences/history exactly.
        $dataRelative = if ($appStem -eq 'Screen Color Changer') { '.runtime\settings.ini' } else { 'optimizer-snapshots\fixture-do-not-touch.txt' }
        $data = Join-Path $clean $dataRelative
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($data)) | Out-Null
        $contents = if ($appStem -eq 'Screen Color Changer') { "[General]\nsaturation=125\ncontrast=110\nbrightness=5\nhue=7\ngamma=103\n".Replace('\n', "`n") } else { 'disposable user-data sentinel' }
        $sentinel = [Text.Encoding]::UTF8.GetBytes($contents)
        [IO.File]::WriteAllBytes($data, $sentinel)
        Assert-SavedColorProfile $clean
        $null = Invoke-Setup $clean
        if ([BitConverter]::ToString([IO.File]::ReadAllBytes($data)) -cne [BitConverter]::ToString($sentinel)) { throw 'Repair changed fixture user data.' }
        Assert-SavedColorProfile $clean
        $moved = Join-Path $base 'moved & space'
        [IO.Directory]::Move($clean, $moved)
        $null = Invoke-Setup $moved
        $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $moved ($appStem + '.lnk')))
        $wantedTarget = Join-Path $moved '.runtime\python\pythonw.exe'
        if ($shortcut.TargetPath -ine $wantedTarget -or $shortcut.Arguments -cne ('-I "' + (Join-Path $moved $appName) + '"') -or $shortcut.WorkingDirectory.TrimEnd('\') -ine $moved) {
            throw 'Moved-folder repair produced an incorrect isolated launcher.'
        }
        if ([BitConverter]::ToString([IO.File]::ReadAllBytes((Join-Path $moved $dataRelative))) -cne [BitConverter]::ToString($sentinel)) { throw 'Moved repair changed fixture user data.' }
        Assert-SavedColorProfile $moved
        Write-Host 'Clean setup, healthy repair, moved-folder repair, isolated shortcut readback, and fixture user-data preservation passed.'
    } finally {
        if ($null -eq $priorProbe) { Remove-Item Env:P -ErrorAction SilentlyContinue } else { $env:P = $priorProbe }
    }
}
Write-Host "$appStem : $script:checks lifecycle checks passed; fixtures retained at $base"
