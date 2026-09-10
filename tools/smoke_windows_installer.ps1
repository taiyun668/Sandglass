param(
    [Parameter(Mandatory = $true)]
    [string]$Installer
)

$ErrorActionPreference = "Stop"
$script:SmokeCleanupBlocked = $false
$installerPath = [System.IO.Path]::GetFullPath($Installer)
if (-not (Test-Path -LiteralPath $installerPath)) {
    throw "Installer not found: $installerPath"
}
function Assert-MutexAvailable([string]$MutexName, [string]$HeldMessage,
    [string]$UnavailablePrefix) {
    $existingMutex = $null
    try {
        $mutexHeld = [System.Threading.Mutex]::TryOpenExisting(
            $MutexName, [ref]$existingMutex)
    } catch {
        throw ($UnavailablePrefix + $_.Exception.Message +
            ". Refusing to continue.")
    }
    if ($mutexHeld) {
        if ($null -ne $existingMutex) { $existingMutex.Dispose() }
        throw $HeldMessage
    }
}

function Wait-DirectProcessExit([System.Diagnostics.Process]$Process,
    [int]$TimeoutSeconds, [string]$Label,
    [IntPtr]$PinnedHandle = [IntPtr]::Zero) {
    if ($null -eq $Process) {
        $script:SmokeCleanupBlocked = $true
        throw "$Label did not return a process object. Refusing to continue."
    }
    # Pin the native process handle before waiting.  Start-Process can return
    # an object whose later Refresh has lost the exit-code observation; the
    # direct handle and the pre-refresh read are the evidence we retain.
    $processHandle = $PinnedHandle
    if ($processHandle -eq [IntPtr]::Zero) {
        try {
            $processHandle = $Process.Handle
        } catch {
            $script:SmokeCleanupBlocked = $true
            throw "$Label did not expose a process handle: $($_.Exception.Message)"
        }
    }
    if ($processHandle -eq [IntPtr]::Zero) {
        $script:SmokeCleanupBlocked = $true
        throw "$Label returned a null process handle. Refusing to continue."
    }
    try {
        $exited = $Process.WaitForExit($TimeoutSeconds * 1000)
    } catch {
        $script:SmokeCleanupBlocked = $true
        throw "$Label wait failed; exact process state is unknown: $($_.Exception.Message)"
    }
    if (-not $exited) {
        $reaped = $false
        try {
            if (-not $Process.HasExited) {
                $Process.Kill()
            }
            $reaped = $Process.WaitForExit(5000)
        } catch {
            $reaped = $false
        }
        if (-not $reaped) {
            $script:SmokeCleanupBlocked = $true
            throw "$Label timed out and could not be terminated/reaped. REVIEW REQUIRED."
        }
        throw "$Label did not exit within $TimeoutSeconds seconds; exact process was terminated and reaped."
    }
    try {
        $ownExitCode = $Process.ExitCode
    } catch {
        $script:SmokeCleanupBlocked = $true
        throw "$Label exit code could not be read: $($_.Exception.Message)"
    }
    if ($null -eq $ownExitCode) {
        $script:SmokeCleanupBlocked = $true
        throw "$Label returned a null exit code. Refusing to continue."
    }
    try {
        $Process.Refresh()
    } catch {
        $script:SmokeCleanupBlocked = $true
        throw "$Label refresh failed after exit-code capture: $($_.Exception.Message)"
    }
    return [int]$ownExitCode
}

function Get-OptionalRegistryValue([string]$SubKey, [string]$ValueName) {
    $key = $null
    try {
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($SubKey, $false)
        if ($null -eq $key) { return $null }
        if (-not ($key.GetValueNames() -contains $ValueName)) { return $null }
        $kind = $key.GetValueKind($ValueName)
        $value = $key.GetValue(
            $ValueName, $null,
            [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
        )
        return [pscustomobject]@{
            Exists = $true
            Value = $value
            Kind = $kind
        }
    } catch {
        throw "Could not read HKCU\\$SubKey value '$ValueName': $($_.Exception.Message)"
    } finally {
        if ($null -ne $key) { $key.Dispose() }
    }
}

# The silent path now starts Sandglass when it finishes, and this script
# asserts that it did. Those assertions only mean something when no existing
# Sandglass process can answer in place of the installed copy. The desktop
# mutex protects the panel launch; the observer mutex protects --stop, which
# otherwise would signal the production observer from inside this smoke.
$instanceMutexName = "Local\Sandglass.Desktop.SingleInstance"
Assert-MutexAvailable $instanceMutexName (
    "A Sandglass desktop instance is already running; it holds " +
    "$instanceMutexName. Quit it before running this smoke -- the " +
    "post-install launch cannot be observed while another build owns it.") `
    "Cannot tell whether a Sandglass instance is running: "
$observerMutexName = "Local\Sandglass.Observer.SingleInstance"
Assert-MutexAvailable $observerMutexName (
    "A Sandglass observer is already running; it holds " +
    "$observerMutexName. Stop it before running this smoke -- otherwise " +
    "the installed copy would reuse the live observer and a later --stop " +
    "would stop the production observer.") `
    "Cannot tell whether the Sandglass observer is running: "

# Refuse a stopped real installation too: the installer consults InstallDir,
# and the registered uninstall action deletes these exact product keys. The
# Sandglass Run value is owner-controlled startup state; do not overwrite one
# that existed before this smoke.
$runKeyPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$runKeySubKey = 'Software\Microsoft\Windows\CurrentVersion\Run'
$runValueName = 'sandglass'
$runKeyExisted = Test-Path -LiteralPath $runKeyPath -PathType Container
foreach ($key in @('HKCU:\Software\Sandglass',
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass')) {
    if (Test-Path -LiteralPath $key) {
        throw "Installer smoke requires no existing Sandglass registration: $key"
    }
}
$existingSandglassRun = Get-OptionalRegistryValue $runKeySubKey $runValueName
if ($null -ne $existingSandglassRun -and $existingSandglassRun.Exists) {
    throw "Installer smoke requires no pre-existing Sandglass Run value."
}

$tempBase = [System.IO.Path]::GetFullPath($env:TEMP)
$smokeRoot = Join-Path $tempBase ("SandglassInstallerSmoke-" + [guid]::NewGuid().ToString("N"))
$installDir = Join-Path $smokeRoot "installed"
$providerRoot = Join-Path $smokeRoot "providers"
$sandglassHome = Join-Path $smokeRoot "sandglass-home"
$unrelatedRunName = "SandglassInstallerSmoke-Unrelated-" + [guid]::NewGuid().ToString("N")
$unrelatedRunValue = 'sandglass-installer-smoke-unrelated'
$fixtureRunValue = $null
$unrelatedRunCreated = $false
$fixtureRunCreated = $false
$runKeyCreated = $false
$desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Sandglass.lnk"
$desktopShortcutBackup = Join-Path $smokeRoot "original-desktop-Sandglass.lnk"
$desktopShortcutExisted = Test-Path -LiteralPath $desktopShortcut -PathType Leaf
$startDirectory = Join-Path ([Environment]::GetFolderPath('Programs')) 'Sandglass'
$startShortcut = Join-Path $startDirectory 'Sandglass.lnk'
$startShortcutBackup = Join-Path $smokeRoot 'original-start-Sandglass.lnk'
$startShortcutExisted = Test-Path -LiteralPath $startShortcut -PathType Leaf
$startDirectoryExisted = Test-Path -LiteralPath $startDirectory -PathType Container

function Assert-UnderSmokeRoot([string]$Path) {
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $prefix = $smokeRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if ($resolved -ne $smokeRoot -and
        -not $resolved.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing installer-smoke mutation outside $smokeRoot`: $resolved"
    }
}

function Get-TreeSnapshot([string]$Root) {
    $result = [ordered]@{}
    $rootPrefix = [System.IO.Path]::GetFullPath($Root).TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    Get-ChildItem -LiteralPath $Root -Recurse -File | Sort-Object FullName | ForEach-Object {
        $relative = $_.FullName.Substring($rootPrefix.Length)
        $result[$relative] = [ordered]@{
            Length = $_.Length
            SHA256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        }
    }
    return ($result | ConvertTo-Json -Depth 4 -Compress)
}

function Get-InstalledSandglassProcesses {
    $prefix = [System.IO.Path]::GetFullPath($installDir).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    return @(Get-CimInstance Win32_Process -Filter "Name='Sandglass.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ExecutablePath -and
            $_.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
        })
}

function Test-MutexHeld([string]$MutexName) {
    $existingMutex = $null
    try {
        $held = [System.Threading.Mutex]::TryOpenExisting(
            $MutexName, [ref]$existingMutex)
    } catch {
        throw "Cannot inspect $MutexName while waiting for installed Sandglass: $($_.Exception.Message)"
    }
    if ($null -ne $existingMutex) { $existingMutex.Dispose() }
    return $held
}

function Wait-ForInstalledSandglass([int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $running = Get-InstalledSandglassProcesses
        if ($running.Count -gt 0 -and
            (Test-MutexHeld $instanceMutexName) -and
            (Test-MutexHeld $observerMutexName)) {
            return $running
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return @()
}

function Wait-Until([scriptblock]$Condition, [int]$TimeoutSeconds,
    [string]$FailureMessage) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (& $Condition) { return $true }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    throw $FailureMessage
}

function Wait-UninstallReceipt([string]$Receipt, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (Test-Path -LiteralPath $Receipt -PathType Leaf) {
            $result = Get-Content -LiteralPath $Receipt -Raw | ConvertFrom-Json
            if ($result.status -eq "success" -and
                -not (Test-MutexHeld 'Local\Sandglass.Installation.Mutation') -and
                -not (Test-MutexHeld $instanceMutexName) -and
                -not (Test-MutexHeld $observerMutexName)) { return $result }
            if ($result.status -eq "failed") {
                throw "Uninstall helper failed in $($result.phase): $($result.detail)"
            }
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    throw "Uninstall helper did not report completion within $TimeoutSeconds seconds."
}

function Stop-InstalledSandglass {
    # --stop reaches the observer only; the panel is quit from the tray, which a
    # smoke cannot click. Ask for the observer, then end whatever still holds the
    # program files -- otherwise the uninstaller correctly refuses to run.
    $exe = Join-Path $installDir "Sandglass.exe"
    $stopFailure = $null
    if (Test-Path -LiteralPath $exe) {
        $stopProcess = Start-Process -FilePath $exe -ArgumentList "--stop" `
            -PassThru -WindowStyle Hidden
        $stopExitCode = Wait-DirectProcessExit $stopProcess 120 "Observer stop"
        if ($stopExitCode -ne 0) {
            $stopFailure = "Observer stop returned exit code $stopExitCode; force termination is cleanup only."
        }
    }
    foreach ($process in Get-InstalledSandglassProcesses) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {
        if ((Get-InstalledSandglassProcesses).Count -eq 0) {
            if ($null -ne $stopFailure) { throw $stopFailure }
            return
        }
        Start-Sleep -Milliseconds 500
    }
    if ($null -ne $stopFailure) {
        throw "$stopFailure Sandglass processes from $installDir did not stop."
    }
    throw "Sandglass processes from $installDir did not stop."
}

function Remove-SmokeRoot {
    if (-not (Test-Path -LiteralPath $smokeRoot)) { return }
    if ($script:SmokeCleanupBlocked) {
        throw "REVIEW REQUIRED: exact smoke process could not be reaped; fixture cleanup and registry recovery are suppressed for $smokeRoot."
    }

    # Process exit and release of its mapped image are not atomic on Windows.
    # In particular, ClrLoader.dll can remain undeletable for a short time
    # after the packaged process disappears from Win32_Process.  Cleanup is
    # part of the smoke contract, so wait for the real tree deletion instead
    # of treating process disappearance as equivalent evidence.
    $deadline = (Get-Date).AddSeconds(15)
    $lastError = $null
    do {
        try {
            Remove-Item -LiteralPath $smokeRoot -Recurse -Force
            return
        } catch [System.IO.IOException], [System.UnauthorizedAccessException] {
            $lastError = $_
            Start-Sleep -Milliseconds 250
        }
    } while ((Get-Date) -lt $deadline)

    throw "Installer smoke could not remove $smokeRoot after process exit: $($lastError.Exception.Message)"
}

Assert-UnderSmokeRoot $installDir
Assert-UnderSmokeRoot $providerRoot
Assert-UnderSmokeRoot $sandglassHome
New-Item -ItemType Directory -Force -Path $installDir, $providerRoot, $sandglassHome | Out-Null
if ($desktopShortcutExisted) {
    Copy-Item -LiteralPath $desktopShortcut -Destination $desktopShortcutBackup -Force
}
if ($startShortcutExisted) {
    Copy-Item -LiteralPath $startShortcut -Destination $startShortcutBackup -Force
}

$claude = Join-Path $providerRoot "claude"
$codex = Join-Path $providerRoot "codex"
$grok = Join-Path $providerRoot "grok"
New-Item -ItemType Directory -Force -Path $claude, $codex, $grok | Out-Null
[System.IO.File]::WriteAllText((Join-Path $claude ".credentials.json"), '{"fixture":"claude"}')
[System.IO.File]::WriteAllText((Join-Path $codex "auth.json"), '{"fixture":"codex"}')
[System.IO.File]::WriteAllText((Join-Path $grok "auth.json"), '{"fixture":"grok"}')
$before = Get-TreeSnapshot $providerRoot

$oldEnvironment = @{
    CLAUDE_CONFIG_DIR = $env:CLAUDE_CONFIG_DIR
    CODEX_HOME = $env:CODEX_HOME
    GROK_HOME = $env:GROK_HOME
    SANDGLASS_HOME = $env:SANDGLASS_HOME
}
try {
    $env:CLAUDE_CONFIG_DIR = $claude
    $env:CODEX_HOME = $codex
    $env:GROK_HOME = $grok
    $env:SANDGLASS_HOME = $sandglassHome

    if (-not $runKeyExisted) {
        New-Item -Path $runKeyPath -Force | Out-Null
        $runKeyCreated = $true
    }
    $priorUnrelatedRun = Get-OptionalRegistryValue $runKeySubKey $unrelatedRunName
    if ($null -ne $priorUnrelatedRun -and $priorUnrelatedRun.Exists) {
        throw "Unexpected collision with smoke Run value: $unrelatedRunName"
    }
    New-ItemProperty -LiteralPath $runKeyPath -Name $unrelatedRunName `
        -Value $unrelatedRunValue -PropertyType String | Out-Null
    $unrelatedRunCreated = $true

    $mutationMutexName = 'Local\Sandglass.Installation.Mutation'
    $reservationCreated = $false
    $reservation = [System.Threading.Mutex]::new($false, $mutationMutexName, [ref]$reservationCreated)
    try {
        if (-not $reservationCreated) { throw 'Another installation operation is active before the smoke.' }
        $blocked = Start-Process -FilePath $installerPath -ArgumentList @(
            '/S', "/D=$installDir"
        ) -PassThru -WindowStyle Hidden
        $blockedExitCode = Wait-DirectProcessExit $blocked 120 "Reserved installer"
        if ($blockedExitCode -ne 2) {
            throw "Installer ignored the uninstall reservation: exit $blockedExitCode, expected 2."
        }
        if (Test-Path -LiteralPath (Join-Path $installDir 'Sandglass.exe')) {
            throw 'Blocked installer entered its file-mutation section.'
        }
        if (Test-Path -LiteralPath 'HKCU:\Software\Sandglass') {
            throw 'Blocked installer changed product registration.'
        }
    } finally {
        $reservation.Dispose()
    }

    $process = Start-Process -FilePath $installerPath -ArgumentList @(
        "/S", "/D=$installDir"
    ) -PassThru -WindowStyle Hidden
    $processHandle = [IntPtr]::Zero
    try {
        $processHandle = $process.Handle
    } catch {
        $script:SmokeCleanupBlocked = $true
        throw "Installer did not expose a process handle: $($_.Exception.Message)"
    }
    if ($processHandle -eq [IntPtr]::Zero) {
        $script:SmokeCleanupBlocked = $true
        throw "Installer returned a null process handle. Refusing to continue."
    }
    $sawMutationReservation = $false
    $processDeadline = (Get-Date).AddSeconds(120)
    while ($true) {
        try {
            $processExited = $process.HasExited
        } catch {
            $script:SmokeCleanupBlocked = $true
            throw "Installer process state became unreadable: $($_.Exception.Message)"
        }
        if ($processExited) { break }
        try {
            if (Test-MutexHeld $mutationMutexName) { $sawMutationReservation = $true }
        } catch {
            $script:SmokeCleanupBlocked = $true
            throw "Installer mutation reservation state became unreadable: $($_.Exception.Message)"
        }
        if ((Get-Date) -ge $processDeadline) {
            # Reuse the exact-process timeout path so a timed-out installer is
            # terminated/reaped before any fixture cleanup is considered.
            $null = Wait-DirectProcessExit $process 0 "Installer" -PinnedHandle $processHandle
            throw "Installer did not exit within 120 seconds."
        }
        Start-Sleep -Milliseconds 20
    }
    $processExitCode = Wait-DirectProcessExit $process 120 "Installer" -PinnedHandle $processHandle
    if ($processExitCode -ne 0) {
        throw "Installer returned exit code $processExitCode."
    }
    if (-not $sawMutationReservation) {
        throw 'The running installer never held the shared installation reservation.'
    }

    $installedExe = Join-Path $installDir "Sandglass.exe"
    if (-not (Test-Path -LiteralPath $installedExe)) {
        throw "Installed executable is missing."
    }
    if (-not (Test-Path -LiteralPath $desktopShortcut -PathType Leaf)) {
        throw "Installer did not create the default desktop shortcut."
    }
    $shortcutShell = New-Object -ComObject WScript.Shell
    $installedShortcut = $shortcutShell.CreateShortcut($desktopShortcut)
    if (-not ([IO.Path]::GetFullPath($installedShortcut.TargetPath)).Equals(
            [IO.Path]::GetFullPath($installedExe),
            [StringComparison]::OrdinalIgnoreCase)) {
        throw "Desktop shortcut does not target the installed Sandglass.exe."
    }

    # MUI_FINISHPAGE_RUN is a checkbox on a page /S never draws, so a silent
    # install used to end with nothing running at all: the panel asked for the
    # update, quit, and never came back. The installer now starts it itself.
    $launched = Wait-ForInstalledSandglass 30
    if ($launched.Count -eq 0) {
        throw "Silent install finished without leaving Sandglass running."
    }

    # The same invariant on the path that gives up. A running panel's program
    # file cannot be renamed, and --stop reaches only the observer, so this is
    # the guard's real trigger rather than a simulated lock. It must abort
    # before File /r, say so in its exit code, and leave a working program.
    $again = Start-Process -FilePath $installerPath -ArgumentList @(
        "/S", "/D=$installDir"
    ) -PassThru -WindowStyle Hidden
    $againExitCode = Wait-DirectProcessExit $again 120 "Running-copy installer"
    if ($againExitCode -ne 2) {
        throw "Installing over a running copy returned $againExitCode, expected 2."
    }
    if (-not (Test-Path -LiteralPath $installedExe)) {
        throw "The aborted install removed the program it refused to replace."
    }
    if (Test-Path -LiteralPath "$installedExe.replacing") {
        throw "The aborted install left Sandglass.exe.replacing behind."
    }
    if ((Get-InstalledSandglassProcesses).Count -eq 0) {
        throw "Installing over a running copy ended with no Sandglass running."
    }
    if (-not (Test-Path -LiteralPath $desktopShortcut -PathType Leaf)) {
        throw "The aborted install removed the desktop shortcut."
    }

    $uninstaller = $installedExe
    $uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass"
    $registeredUninstall = (Get-ItemProperty -LiteralPath $uninstallKey).UninstallString
    if ($registeredUninstall -notmatch [regex]::Escape($installedExe) -or
        $registeredUninstall -notmatch "--uninstall") {
        throw "Registered uninstall entry does not target Sandglass.exe --uninstall."
    }
    # Only now, after validating the installed executable and uninstall target,
    # create the fixture-owned startup value.  The unrelated value was written
    # before install and must survive every installer/uninstaller path.
    $fixtureRunValue = '"' + $installedExe + '" --background'
    $existingInstalledRun = Get-OptionalRegistryValue $runKeySubKey $runValueName
    if ($null -ne $existingInstalledRun -and $existingInstalledRun.Exists) {
        throw "Installer unexpectedly created a Sandglass Run value before the fixture was set."
    }
    Set-ItemProperty -LiteralPath $runKeyPath -Name $runValueName `
        -Value $fixtureRunValue
    $fixtureRunCreated = $true
    $installedUnrelatedRun = Get-OptionalRegistryValue $runKeySubKey $unrelatedRunName
    if ($null -eq $installedUnrelatedRun -or
        $installedUnrelatedRun.Value -cne $unrelatedRunValue) {
        throw "Installer changed the unrelated Run value."
    }

    $ownedNames = @((Get-Content -LiteralPath (Join-Path $installDir 'Sandglass-owned-paths.json') -Raw | ConvertFrom-Json).paths)
    $ownerExtra = Join-Path $installDir 'owner-extra.txt'
    $stateMarker = Join-Path $sandglassHome 'uninstall-preserved-state.txt'
    [IO.File]::WriteAllText($ownerExtra, 'owner file must survive uninstall')
    [IO.File]::WriteAllText($stateMarker, 'state must survive uninstall')
    $ownerHash = (Get-FileHash -LiteralPath $ownerExtra -Algorithm SHA256).Hash
    $stateHash = (Get-FileHash -LiteralPath $stateMarker -Algorithm SHA256).Hash

    # The installed executable can request a responsive desktop to close. The
    # helper then waits for this process and removes only manifest-owned paths.
    $uninstallReceipt = Join-Path $sandglassHome "uninstall-result.json"
    Remove-Item -LiteralPath $uninstallReceipt -Force -ErrorAction SilentlyContinue
    $uninstallRunning = Start-Process -FilePath $uninstaller -ArgumentList "--uninstall --quiet" `
        -PassThru -WindowStyle Hidden
    $uninstallRunningExitCode = Wait-DirectProcessExit $uninstallRunning 120 "Running uninstaller"
    if ($uninstallRunningExitCode -ne 0) {
        throw "Uninstalling while the desktop is running returned $uninstallRunningExitCode, expected 0."
    }
    $null = Wait-UninstallReceipt $uninstallReceipt 120
    $null = Wait-Until { -not (Test-Path -LiteralPath $installedExe) } 15 `
        "Responsive uninstall left the installed executable behind."
    if (Test-Path -LiteralPath $uninstallKey) { throw "Responsive uninstall left registration behind." }
    if (Test-Path -LiteralPath $desktopShortcut) { throw "Responsive uninstall left desktop shortcut behind." }
    $remainingResponsiveRun = Get-OptionalRegistryValue $runKeySubKey $runValueName
    if ($null -ne $remainingResponsiveRun -and $remainingResponsiveRun.Exists) {
        throw "Responsive uninstall left the fixture Sandglass Run value behind."
    }
    $responsiveUnrelatedRun = Get-OptionalRegistryValue $runKeySubKey $unrelatedRunName
    if ($null -eq $responsiveUnrelatedRun -or
        $responsiveUnrelatedRun.Value -cne $unrelatedRunValue) {
        throw "Responsive uninstall changed the unrelated Run value."
    }
    if (-not (Test-Path -LiteralPath $sandglassHome -PathType Container)) { throw "Uninstall removed SANDGLASS_HOME." }
    foreach ($name in $ownedNames) {
        if (Test-Path -LiteralPath (Join-Path $installDir $name)) {
            throw "Responsive uninstall left owned path behind: $name"
        }
    }
    if ((Get-InstalledSandglassProcesses).Count -ne 0) { throw 'Responsive uninstall left a product process running.' }
    if (Test-Path -LiteralPath 'HKCU:\Software\Sandglass') { throw 'Responsive uninstall left InstallDir registration behind.' }
    if ((Get-FileHash -LiteralPath $ownerExtra -Algorithm SHA256).Hash -ne $ownerHash -or
        (Get-FileHash -LiteralPath $stateMarker -Algorithm SHA256).Hash -ne $stateHash) {
        throw 'Responsive uninstall changed owner files or state.'
    }

    # Reinstall once so the stopped-copy path is also exercised below.
    $reinstall = Start-Process -FilePath $installerPath -ArgumentList @(
        "/S", "/D=$installDir"
    ) -PassThru -WindowStyle Hidden
    $reinstallExitCode = Wait-DirectProcessExit $reinstall 120 "Reinstall"
    if ($reinstallExitCode -ne 0 -or -not (Test-Path -LiteralPath $installedExe)) {
        throw "Reinstall after responsive uninstall failed: exit $reinstallExitCode."
    }
    $reinstalled = Wait-ForInstalledSandglass 30
    if ($reinstalled.Count -eq 0) {
        throw "Reinstall finished without leaving Sandglass running."
    }
    $registeredAfterReinstall = (Get-ItemProperty -LiteralPath $uninstallKey).UninstallString
    if ($registeredAfterReinstall -notmatch [regex]::Escape($installedExe) -or
        $registeredAfterReinstall -notmatch "--uninstall") {
        throw "Reinstall uninstall entry does not target Sandglass.exe --uninstall."
    }
    $fixtureRunValue = '"' + $installedExe + '" --background'
    $existingReinstallRun = Get-OptionalRegistryValue $runKeySubKey $runValueName
    if ($null -ne $existingReinstallRun -and $existingReinstallRun.Exists) {
        throw "Reinstall unexpectedly created a Sandglass Run value before the fixture was set."
    }
    Set-ItemProperty -LiteralPath $runKeyPath -Name $runValueName `
        -Value $fixtureRunValue
    $fixtureRunCreated = $true
    Remove-Item -LiteralPath $uninstallReceipt -Force -ErrorAction SilentlyContinue

    Stop-InstalledSandglass

    $selfTest = Start-Process -FilePath $installedExe -ArgumentList "--self-test" `
        -PassThru -WindowStyle Hidden
    $selfTestExitCode = Wait-DirectProcessExit $selfTest 120 "Installed executable self-test"
    if ($selfTestExitCode -ne 0) {
        throw "Installed executable self-test returned $selfTestExitCode."
    }

    $uninstall = Start-Process -FilePath $uninstaller -ArgumentList "--uninstall --quiet" `
        -PassThru -WindowStyle Hidden
    $uninstallExitCode = Wait-DirectProcessExit $uninstall 120 "Stopped uninstaller"
    if ($uninstallExitCode -ne 0) {
        throw "Uninstaller returned exit code $uninstallExitCode."
    }
    $null = Wait-UninstallReceipt $uninstallReceipt 120

    foreach ($name in $ownedNames) {
        if (Test-Path -LiteralPath (Join-Path $installDir $name)) { throw "Stopped uninstall left owned path behind: $name" }
    }
    if ((Get-FileHash -LiteralPath $ownerExtra -Algorithm SHA256).Hash -ne $ownerHash -or
        (Get-FileHash -LiteralPath $stateMarker -Algorithm SHA256).Hash -ne $stateHash) {
        throw 'Stopped uninstall changed owner files or state.'
    }
    $remainingStoppedRun = Get-OptionalRegistryValue $runKeySubKey $runValueName
    if ($null -ne $remainingStoppedRun -and $remainingStoppedRun.Exists) {
        throw "Stopped uninstall left the fixture Sandglass Run value behind."
    }
    $stoppedUnrelatedRun = Get-OptionalRegistryValue $runKeySubKey $unrelatedRunName
    if ($null -eq $stoppedUnrelatedRun -or
        $stoppedUnrelatedRun.Value -cne $unrelatedRunValue) {
        throw "Stopped uninstall changed the unrelated Run value."
    }

    $after = Get-TreeSnapshot $providerRoot
    if ($after -cne $before) {
        throw "Provider directories changed during install, self-test, or uninstall."
    }
    $null = Wait-Until { -not (Test-Path -LiteralPath $installedExe) } 15 "Installed executable remained after uninstall."
    if (Test-Path -LiteralPath $desktopShortcut) {
        throw "Uninstall left the Sandglass desktop shortcut behind."
    }
    Write-Output ("PASS per-user install, post-install launch, running-copy " +
        "abort, automatic running-desktop exit and uninstall, packaged self-test, stopped uninstall, " +
        "owned-path deletion, owner/state preservation, provider byte invariance")
}
finally {
    foreach ($name in $oldEnvironment.Keys) {
        if ($null -eq $oldEnvironment[$name]) {
            Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
        } else {
            Set-Item -Path "Env:$name" -Value $oldEnvironment[$name]
        }
    }
    if ($script:SmokeCleanupBlocked) {
        throw "REVIEW REQUIRED: exact smoke process could not be reaped; fixture cleanup and registry recovery are suppressed for $smokeRoot."
    }
    foreach ($process in Get-InstalledSandglassProcesses) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($fixtureRunCreated -and $null -ne $fixtureRunValue) {
        $currentFixtureRun = Get-OptionalRegistryValue $runKeySubKey $runValueName
        if ($null -ne $currentFixtureRun -and
            $currentFixtureRun.Exists -and $currentFixtureRun.Value -ceq $fixtureRunValue) {
            Remove-ItemProperty -LiteralPath $runKeyPath -Name $runValueName `
                -ErrorAction SilentlyContinue
        }
    }
    if ($unrelatedRunCreated) {
        $currentUnrelatedRun = Get-OptionalRegistryValue $runKeySubKey $unrelatedRunName
        if ($null -ne $currentUnrelatedRun -and
            $currentUnrelatedRun.Exists -and $currentUnrelatedRun.Value -ceq $unrelatedRunValue) {
            Remove-ItemProperty -LiteralPath $runKeyPath -Name $unrelatedRunName `
                -ErrorAction SilentlyContinue
        }
    }
    if ($runKeyCreated -and (Test-Path -LiteralPath $runKeyPath -PathType Container)) {
        $runPropertyObject = Get-ItemProperty -LiteralPath $runKeyPath -ErrorAction SilentlyContinue
        $remainingProperties = @()
        if ($null -ne $runPropertyObject) {
            $remainingProperties = @(
                $runPropertyObject.PSObject.Properties |
                    Where-Object { $_.Name -notlike 'PS*' }
            )
        }
        $remainingSubkeys = @(Get-ChildItem -LiteralPath $runKeyPath -ErrorAction SilentlyContinue)
        if ($remainingProperties.Count -eq 0 -and $remainingSubkeys.Count -eq 0) {
            Remove-Item -LiteralPath $runKeyPath -Force -ErrorAction SilentlyContinue
        }
    }
    # Recover only this fixture's registration on a failed smoke. Preflight
    # refused every pre-existing product registration, and InstallDir must
    # still name this exact fixture before cleanup may touch these keys.
    $registered = Get-ItemProperty -LiteralPath 'HKCU:\Software\Sandglass' -ErrorAction SilentlyContinue
    if ($registered -and $registered.InstallDir -and
        [IO.Path]::GetFullPath($registered.InstallDir).Equals($installDir, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath 'HKCU:\Software\Sandglass' -Recurse -Force
        Remove-Item -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass' -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('Programs')) 'Sandglass\Sandglass.lnk') -Force -ErrorAction SilentlyContinue
    }
    if ($desktopShortcutExisted) {
        Copy-Item -LiteralPath $desktopShortcutBackup -Destination $desktopShortcut -Force
    } else {
        Remove-Item -LiteralPath $desktopShortcut -Force -ErrorAction SilentlyContinue
    }
    if ($startShortcutExisted) {
        New-Item -ItemType Directory -Path $startDirectory -Force | Out-Null
        Copy-Item -LiteralPath $startShortcutBackup -Destination $startShortcut -Force
    } else {
        Remove-Item -LiteralPath $startShortcut -Force -ErrorAction SilentlyContinue
    }
    if (-not $startDirectoryExisted -and (Test-Path -LiteralPath $startDirectory)) {
        if (@(Get-ChildItem -LiteralPath $startDirectory -Force).Count -eq 0) {
            Remove-Item -LiteralPath $startDirectory
        }
    }
    Assert-UnderSmokeRoot $smokeRoot
    Remove-SmokeRoot
}
