param(
    [Parameter(Mandatory = $true)]
    [string]$Installer,
    [switch]$RequireSigned
)

$ErrorActionPreference = "Stop"
$installerPath = [System.IO.Path]::GetFullPath($Installer)
if (-not (Test-Path -LiteralPath $installerPath)) {
    throw "Installer not found: $installerPath"
}
if ($RequireSigned -and
    (Get-AuthenticodeSignature -LiteralPath $installerPath).Status -ne "Valid") {
    throw "Installer does not have a valid Authenticode signature."
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

$tempBase = [System.IO.Path]::GetFullPath($env:TEMP)
$smokeRoot = Join-Path $tempBase ("SandglassInstallerSmoke-" + [guid]::NewGuid().ToString("N"))
$installDir = Join-Path $smokeRoot "installed"
$providerRoot = Join-Path $smokeRoot "providers"
$sandglassHome = Join-Path $smokeRoot "sandglass-home"

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

function Stop-InstalledSandglass {
    # --stop reaches the observer only; the panel is quit from the tray, which a
    # smoke cannot click. Ask for the observer, then end whatever still holds the
    # program files -- otherwise the uninstaller correctly refuses to run.
    $exe = Join-Path $installDir "Sandglass.exe"
    if (Test-Path -LiteralPath $exe) {
        Start-Process -FilePath $exe -ArgumentList "--stop" -Wait -PassThru `
            -WindowStyle Hidden | Out-Null
    }
    foreach ($process in Get-InstalledSandglassProcesses) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {
        if ((Get-InstalledSandglassProcesses).Count -eq 0) { return }
        Start-Sleep -Milliseconds 500
    }
    throw "Sandglass processes from $installDir did not stop."
}

function Remove-SmokeRoot {
    if (-not (Test-Path -LiteralPath $smokeRoot)) { return }

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

    $process = Start-Process -FilePath $installerPath -ArgumentList @(
        "/S", "/D=$installDir"
    ) -PassThru -WindowStyle Hidden
    $process.WaitForExit()
    $process.Refresh()
    if ($process.ExitCode -ne 0) {
        throw "Installer returned exit code $($process.ExitCode)."
    }

    $installedExe = Join-Path $installDir "Sandglass.exe"
    if (-not (Test-Path -LiteralPath $installedExe)) {
        throw "Installed executable is missing."
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
    $again.WaitForExit()
    $again.Refresh()
    if ($again.ExitCode -ne 2) {
        throw "Installing over a running copy returned $($again.ExitCode), expected 2."
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

    Stop-InstalledSandglass

    $selfTest = Start-Process -FilePath $installedExe -ArgumentList "--self-test" `
        -Wait -PassThru -WindowStyle Hidden
    if ($selfTest.ExitCode -ne 0) {
        throw "Installed executable self-test returned $($selfTest.ExitCode)."
    }

    $uninstaller = Join-Path $installDir "unins000.exe"
    if (-not (Test-Path -LiteralPath $uninstaller)) {
        $uninstaller = Join-Path $installDir "Uninstall.exe"
    }
    if (-not (Test-Path -LiteralPath $uninstaller)) {
        throw "Uninstaller is missing."
    }
    if ($RequireSigned) {
        foreach ($signedPath in @((Join-Path $installDir "Sandglass.exe"), $uninstaller)) {
            if ((Get-AuthenticodeSignature -LiteralPath $signedPath).Status -ne "Valid") {
                throw "Installed component does not have a valid signature: $signedPath"
            }
        }
    }
    $uninstall = Start-Process -FilePath $uninstaller -ArgumentList @(
        "/S", "_?=$installDir"
    ) -Wait -PassThru -WindowStyle Hidden
    if ($uninstall.ExitCode -ne 0) {
        throw "Uninstaller returned exit code $($uninstall.ExitCode)."
    }

    $after = Get-TreeSnapshot $providerRoot
    if ($after -cne $before) {
        throw "Provider directories changed during install, self-test, or uninstall."
    }
    if (Test-Path -LiteralPath $installedExe) {
        throw "The installed executable remained after uninstall."
    }
    Write-Output ("PASS per-user install, post-install launch, running-copy " +
        "abort, packaged self-test, uninstall, provider byte invariance")
}
finally {
    foreach ($name in $oldEnvironment.Keys) {
        if ($null -eq $oldEnvironment[$name]) {
            Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
        } else {
            Set-Item -Path "Env:$name" -Value $oldEnvironment[$name]
        }
    }
    foreach ($process in Get-InstalledSandglassProcesses) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Assert-UnderSmokeRoot $smokeRoot
    Remove-SmokeRoot
}
