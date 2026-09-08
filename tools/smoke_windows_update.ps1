param(
    [Parameter(Mandatory = $true)]
    [string]$OldInstaller,
    [Parameter(Mandatory = $true)]
    [string]$NewInstaller,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedGitCommit,
    [switch]$ExpectRollback
)

$ErrorActionPreference = "Stop"

function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead((Get-FullPath $Path))
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace("-", "")
    } finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Get-FullPath([string]$Path) {
    return [System.IO.Path]::GetFullPath($Path)
}

$oldInstallerPath = Get-FullPath $OldInstaller
$newInstallerPath = Get-FullPath $NewInstaller
foreach ($path in @($oldInstallerPath, $newInstallerPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Installer not found: $path"
    }
}
if ($oldInstallerPath.Equals($newInstallerPath, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OldInstaller and NewInstaller must be different files."
}
$oldInstallerHash = Get-Sha256 $oldInstallerPath
$newInstallerHash = Get-Sha256 $newInstallerPath
if ($oldInstallerHash -eq $newInstallerHash) {
    throw "OldInstaller and NewInstaller have the same SHA256; an update cannot be proven."
}

if (-not ("SandglassSmoke.NativeMethods" -as [type])) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
namespace SandglassSmoke {
public static class NativeMethods {
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool IsWindowVisible(IntPtr hWnd);
}
}
"@
}

function Assert-UnderSmokeRoot([string]$Path) {
    $resolved = Get-FullPath $Path
    $prefix = $smokeRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) +
        [System.IO.Path]::DirectorySeparatorChar
    if ($resolved -ne $smokeRoot -and
        -not $resolved.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing update-smoke mutation outside $smokeRoot`: $resolved"
    }
}

function Get-TreeSnapshot([string]$Root) {
    $result = [ordered]@{}
    $resolved = Get-FullPath $Root
    if (-not (Test-Path -LiteralPath $resolved -PathType Container)) {
        return "{}"
    }
    $prefix = $resolved.TrimEnd([System.IO.Path]::DirectorySeparatorChar) +
        [System.IO.Path]::DirectorySeparatorChar
    $rootItem = Get-Item -LiteralPath $resolved -Force
    if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Provider snapshot root is a reparse point: $resolved"
    }
    $pending = New-Object System.Collections.Generic.Queue[string]
    $pending.Enqueue($resolved)
    while ($pending.Count -gt 0) {
        $current = $pending.Dequeue()
        foreach ($item in @(Get-ChildItem -LiteralPath $current -Force | Sort-Object FullName)) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Provider snapshot refuses a reparse point: $($item.FullName)"
            }
            $relative = $item.FullName.Substring($prefix.Length)
            if ($item.PSIsContainer) {
                $result[$relative] = [ordered]@{ Type = "Directory" }
                $pending.Enqueue($item.FullName)
            } else {
                $result[$relative] = [ordered]@{
                    Type = "File"
                    Length = $item.Length
                    SHA256 = Get-Sha256 $item.FullName
                }
            }
        }
    }
    return ($result | ConvertTo-Json -Depth 4 -Compress)
}

function Get-ProcessesFromInstall {
    $prefix = (Get-FullPath $installDir).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    return @(Get-CimInstance Win32_Process -Filter "Name='Sandglass.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ExecutablePath -and
            $_.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
        })
}

function Get-PanelProcess {
    foreach ($row in Get-ProcessesFromInstall) {
        try {
            $process = Get-Process -Id $row.ProcessId -ErrorAction Stop
            if ($process.MainWindowHandle -ne 0) {
                return [pscustomobject]@{
                    ProcessId = [int]$row.ProcessId
                    ExecutablePath = $row.ExecutablePath
                    MainWindowHandle = [int64]$process.MainWindowHandle
                }
            }
        } catch {
            # A process can disappear between the CIM and process snapshots.
        }
    }
    return $null
}

function Get-ObserverProcessesFromInstall {
    return @(Get-ProcessesFromInstall | Where-Object {
        $_.CommandLine -and $_.CommandLine -match '(?i)(^|\s)--observer(?:\s|$)'
    })
}

function Test-MutexHeld([string]$MutexName) {
    $existingMutex = $null
    try {
        $held = [System.Threading.Mutex]::TryOpenExisting($MutexName, [ref]$existingMutex)
    } catch {
        throw "Cannot inspect ${MutexName}: $($_.Exception.Message)"
    }
    if ($null -ne $existingMutex) { $existingMutex.Dispose() }
    return $held
}

function Get-VisibleUpdateProcess([int]$ProcessId) {
    try {
        $process = Get-Process -Id $ProcessId -ErrorAction Stop
        $process.Refresh()
        $handle = [IntPtr]$process.MainWindowHandle
        if ($handle -eq [IntPtr]::Zero) { return $null }
        if (-not [SandglassSmoke.NativeMethods]::IsWindowVisible($handle)) { return $null }
        if ([string]::IsNullOrWhiteSpace($process.MainWindowTitle)) { return $null }
        return [pscustomobject]@{
            ProcessId = $process.Id
            MainWindowHandle = $process.MainWindowHandle
            IsWindowVisible = $true
            MainWindowTitle = $process.MainWindowTitle
        }
    } catch {
        return $null
    }
}

function Wait-Until([scriptblock]$Condition, [int]$TimeoutSeconds, [string]$Failure) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $value = & $Condition
        if ($null -ne $value -and $value -ne $false) { return $value }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    throw $Failure
}

function Wait-ProcessExitBounded([System.Diagnostics.Process]$Process,
    [int]$TimeoutSeconds, [string]$Description) {
    if ($null -eq $Process) { return }
    try {
        if ($Process.HasExited) { return $true }
        if ($Process.WaitForExit($TimeoutSeconds * 1000)) {
            $Process.Refresh()
            return $true
        }
    } catch [System.InvalidOperationException] {
        return $true
    }
    # The PID is the exact temporary process started by this smoke. End only
    # that PID, then give Windows a short bounded interval to release its image.
    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    try { $Process.WaitForExit(5000) | Out-Null } catch { }
    throw "$Description did not exit within ${TimeoutSeconds}s; terminated PID $($Process.Id)."
}

function Stop-InstalledProcesses {
    foreach ($row in @(Get-ProcessesFromInstall)) {
        Stop-Process -Id $row.ProcessId -Force -ErrorAction SilentlyContinue
    }
    $null = Wait-Until { @(Get-ProcessesFromInstall).Count -eq 0 } 20 `
        "Sandglass processes from $installDir did not stop."
}

function Invoke-InstalledStop {
    $exe = Join-Path $installDir "Sandglass.exe"
    if (Test-Path -LiteralPath $exe) {
        $stop = Start-Process -FilePath $exe -ArgumentList "--stop" `
            -PassThru -WindowStyle Hidden
        $null = Wait-ProcessExitBounded $stop 45 "Installed --stop"
        $stop.Refresh()
        if ($stop.ExitCode -ne 0) {
            throw "Installed --stop returned exit code $($stop.ExitCode)."
        }
    }
}

function Invoke-Reg([ValidateSet("query", "export", "delete", "import")][string]$Operation,
    [string]$Key, [string]$File) {
    $arguments = @()
    switch ($Operation) {
        "query" { $arguments = @("query", $Key) }
        "export" { $arguments = @("export", $Key, $File, "/y") }
        "delete" { $arguments = @("delete", $Key, "/f") }
        "import" { $arguments = @("import", $File) }
    }

    # ProcessStartInfo keeps reg.exe stderr out of PowerShell 5.1's
    # native-command error adapter. Quote each argument explicitly so
    # export/import paths with spaces are passed as one argument. Windows paths
    # cannot contain a double quote, so this does not expose or log contents.
    $quotedArguments = @($arguments | ForEach-Object {
        '"' + ([string]$_).Replace('"', '\\"') + '"'
    })
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = "reg.exe"
    $startInfo.Arguments = ($quotedArguments -join " ")
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = $null
    try {
        $process = [System.Diagnostics.Process]::Start($startInfo)
        if (-not $process.WaitForExit(10000)) {
            try { $process.Kill() } catch { }
            try { $process.WaitForExit(2000) | Out-Null } catch { }
            throw "reg.exe $Operation timed out."
        }
        # Drain both redirected streams after the bounded wait. reg.exe output
        # is intentionally discarded; only the process-owned exit code matters.
        $null = $process.StandardOutput.ReadToEnd()
        $null = $process.StandardError.ReadToEnd()
        return [pscustomobject]@{ ExitCode = [int]$process.ExitCode }
    } finally {
        if ($null -ne $process) { $process.Dispose() }
    }
}

function Save-RegistryKey([string]$Key, [string]$File, [ref]$State) {
    $parent = Split-Path -Parent $File
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $queryResult = Invoke-Reg "query" $Key
    $queryExitCode = $queryResult.ExitCode
    if ($queryExitCode -eq 0) {
        # Mark existence before export. If export fails, finally sees a known
        # original key but an uncommitted snapshot and leaves it untouched.
        $State.Value = [pscustomobject]@{
            Known = $true; Existed = $true; Exported = $false; File = $File
        }
        $exportResult = Invoke-Reg "export" $Key $File
        if ($exportResult.ExitCode -ne 0) { throw "Could not export registry key $Key." }
        $State.Value.Exported = $true
        return
    }
    if ($queryExitCode -eq 1) {
        $State.Value = [pscustomobject]@{
            Known = $true; Existed = $false; Exported = $true; File = $File
        }
        return
    }
    throw "Could not query registry key $Key (reg.exe exit $queryExitCode)."
}

function Restore-RegistryKey([string]$Key, [psobject]$State) {
    if ($null -eq $State -or -not $State.Known -or -not $State.Exported) { return }
    $deleteResult = Invoke-Reg "delete" $Key
    if ($deleteResult.ExitCode -ne 0) {
        if ($State.Existed) {
            throw "Could not delete registry key $Key before restore (reg.exe exit $($deleteResult.ExitCode))."
        }
        $afterDelete = Invoke-Reg "query" $Key
        if ($afterDelete.ExitCode -ne 1) {
            throw "Could not prove originally absent registry key $Key was removed (delete exit $($deleteResult.ExitCode), query exit $($afterDelete.ExitCode))."
        }
    }
    if ($State.Existed) {
        $importResult = Invoke-Reg "import" $null $State.File
        if ($importResult.ExitCode -ne 0) { throw "Could not restore registry key $Key." }
    }
}

function Save-RunValue([ref]$State) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey(
        "Software\Microsoft\Windows\CurrentVersion\Run", $false)
    if ($null -eq $key) {
        $State.Value = [pscustomobject]@{ Known = $true; KeyExisted = $false; Existed = $false }
        return
    }
    try {
        $exists = @($key.GetValueNames()) -contains "Sandglass"
        if (-not $exists) {
            $State.Value = [pscustomobject]@{ Known = $true; KeyExisted = $true; Existed = $false }
            return
        }
        $State.Value = [pscustomobject]@{
            Known = $true
            KeyExisted = $true
            Existed = $true
            Kind = [Microsoft.Win32.RegistryValueKind]$key.GetValueKind("Sandglass")
            Data = $key.GetValue("Sandglass", $null,
                [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        }
    } finally {
        $key.Dispose()
    }
}

function Restore-RunValue([psobject]$State) {
    if ($null -eq $State -or -not $State.Known) { return }
    if (-not $State.KeyExisted -and -not $State.Existed) { return }
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey(
        "Software\Microsoft\Windows\CurrentVersion\Run")
    try {
        if ($State.Existed) {
            $key.SetValue("Sandglass", $State.Data, $State.Kind)
        } elseif (@($key.GetValueNames()) -contains "Sandglass") {
            # Only this value is ours; preserve every unrelated Run value.
            $key.DeleteValue("Sandglass", $false)
        }
    } finally {
        $key.Dispose()
    }
}

function Read-Provenance([string]$Root) {
    $files = @(Get-ChildItem -LiteralPath $Root -Recurse -Filter `
        "Sandglass-build-provenance.json" -File | Select-Object -First 2)
    if ($files.Count -ne 1) { throw "Expected exactly one installed build provenance under $Root." }
    $value = Get-Content -LiteralPath $files[0].FullName -Raw | ConvertFrom-Json
    foreach ($field in @("git_head", "build_id")) {
        if ([string]::IsNullOrWhiteSpace([string]$value.$field)) {
            throw "Installed build provenance is missing $field."
        }
    }
    return $value
}

function Read-UpdateFailurePhase {
    if ($updateFailureLog -and (Test-Path -LiteralPath $updateFailureLog -PathType Leaf)) {
        return (Get-Content -LiteralPath $updateFailureLog -Raw).Trim()
    }
    return "unreported"
}

function Assert-CommandLineIsUpdate([int]$ProcessId) {
    $row = Wait-Until {
        Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" `
            -ErrorAction SilentlyContinue
    } 5 "The update installer exited before its update window could be observed (phase=$(Read-UpdateFailurePhase))."
    if (-not $row.CommandLine -or $row.CommandLine -notmatch '(?i)/UPDATE') {
        throw "The new installer was not running in /UPDATE mode: $($row.CommandLine)"
    }
    if ($row.CommandLine -match '(?i)(^|\s)/S(?:\s|$)') {
        throw "The update installer was incorrectly launched with /S: $($row.CommandLine)"
    }
    return $row
}

$tempBase = Get-FullPath $env:TEMP
$script:smokeRoot = Join-Path $tempBase ("SandglassUpdateSmoke-" + [guid]::NewGuid().ToString("N"))
$script:installDir = Join-Path $smokeRoot "installed"
$providerRoot = Join-Path $smokeRoot "providers"
$sandglassHome = Join-Path $smokeRoot "sandglass-home"
$claudeRoot = Join-Path $providerRoot "claude"
$codexRoot = Join-Path $providerRoot "codex"
$grokRoot = Join-Path $providerRoot "grok"
$registryRoot = Join-Path $smokeRoot "registry"
$oldInstallReg = Join-Path $registryRoot "software-sandglass.reg"
$oldUninstallReg = Join-Path $registryRoot "uninstall-sandglass.reg"
$oldInstallKey = "HKCU\Software\Sandglass"
$oldUninstallKey = "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass"
$oldInstallState = [pscustomobject]@{ Known = $false; Existed = $false; Exported = $false }
$oldUninstallState = [pscustomobject]@{ Known = $false; Existed = $false; Exported = $false }
$oldRunValueState = [pscustomobject]@{ Known = $false; Existed = $false }
$oldEnvironment = @{
    CLAUDE_CONFIG_DIR = $env:CLAUDE_CONFIG_DIR
    CODEX_HOME = $env:CODEX_HOME
    GROK_HOME = $env:GROK_HOME
    SANDGLASS_HOME = $env:SANDGLASS_HOME
}
$oldPanelPid = $null
$updateProcess = $null
$newPanelPid = $null
$updateFailureLog = $null
$updateStagePath = $null
$updateBackupPath = $null
$uninstallerPath = $null
$uninstallAttempted = $false
$failure = $null

function Remove-SmokeRoot {
    if (-not (Test-Path -LiteralPath $smokeRoot)) { return }
    $deadline = (Get-Date).AddSeconds(20)
    do {
        try {
            Remove-Item -LiteralPath $smokeRoot -Recurse -Force
            return
        } catch [System.IO.IOException], [System.UnauthorizedAccessException] {
            Start-Sleep -Milliseconds 250
        }
    } while ((Get-Date) -lt $deadline)
    throw "Could not remove update smoke root $smokeRoot."
}

function Remove-ExactResidue([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return }
    $deadline = (Get-Date).AddSeconds(20)
    do {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return
        } catch [System.IO.IOException], [System.UnauthorizedAccessException] {
            Start-Sleep -Milliseconds 250
        }
    } while ((Get-Date) -lt $deadline)
    throw "Could not remove exact smoke residue $Path."
}

try {
    # Refuse to run alongside the user's real product. The temp environment is
    # not sufficient isolation when the global desktop/observer mutexes exist.
    $outside = @(Get-CimInstance Win32_Process -Filter "Name='Sandglass.exe'" `
        -ErrorAction SilentlyContinue)
    if ($outside.Count -gt 0) {
        throw "A Sandglass.exe process is already running; stop the real product before this smoke."
    }

    foreach ($path in @($smokeRoot, $installDir, $providerRoot, $sandglassHome,
                        $claudeRoot, $codexRoot, $grokRoot, $registryRoot)) {
        Assert-UnderSmokeRoot $path
    }
    New-Item -ItemType Directory -Force -Path @(
        $installDir, $claudeRoot, $codexRoot, $grokRoot, $sandglassHome, $registryRoot
    ) | Out-Null
    [IO.File]::WriteAllText((Join-Path $claudeRoot ".credentials.json"), '{"fixture":"claude"}')
    [IO.File]::WriteAllText((Join-Path $codexRoot "auth.json"), '{"fixture":"codex"}')
    [IO.File]::WriteAllText((Join-Path $grokRoot "auth.json"), '{"fixture":"grok"}')
    $providerBefore = Get-TreeSnapshot $providerRoot

    Save-RegistryKey $oldInstallKey $oldInstallReg ([ref]$oldInstallState)
    Save-RegistryKey $oldUninstallKey $oldUninstallReg ([ref]$oldUninstallState)
    Save-RunValue ([ref]$oldRunValueState)

    $env:CLAUDE_CONFIG_DIR = $claudeRoot
    $env:CODEX_HOME = $codexRoot
    $env:GROK_HOME = $grokRoot
    $env:SANDGLASS_HOME = $sandglassHome

    $oldInstall = Start-Process -FilePath $oldInstallerPath -ArgumentList @(
        "/S", "/D=$installDir"
    ) -PassThru -WindowStyle Hidden
    $null = Wait-ProcessExitBounded $oldInstall 90 "Old installer"
    $oldInstall.Refresh()
    if ($oldInstall.ExitCode -ne 0) {
        throw "Old installer returned exit code $($oldInstall.ExitCode)."
    }
    $oldExe = Join-Path $installDir "Sandglass.exe"
    if (-not (Test-Path -LiteralPath $oldExe -PathType Leaf)) {
        throw "Old installer did not create $oldExe."
    }
    $oldProvenance = Read-Provenance $installDir
    $oldPanel = Wait-Until { Get-PanelProcess } 30 `
        "Old installer did not leave a Sandglass panel running from the temp install directory."
    $oldPanelPid = [int]$oldPanel.ProcessId
    $updateFailureLog = Join-Path $env:TEMP "Sandglass-update-$oldPanelPid.failure.txt"
    $updateStagePath = Join-Path $env:TEMP "Sandglass-update-$oldPanelPid"
    $updateBackupPath = "$installDir.update-backup"

    $oldObserver = Wait-Until {
        $observer = @(Get-ObserverProcessesFromInstall)
        if ($observer.Count -eq 1 -and (Test-MutexHeld "Local\Sandglass.Observer.SingleInstance")) {
            return $observer[0]
        }
        return $null
    } 30 "Old installation did not expose exactly one temp --observer holding the observer mutex."

    # The updater must be alive and visibly in its dedicated progress mode while
    # the old panel is still holding the parent process handle.
    $newArgs = @(
        "/UPDATE", "/PARENTPID=$oldPanelPid", "/RESTARTEXE=$oldExe"
    )
    $updateProcess = Start-Process -FilePath $newInstallerPath -ArgumentList $newArgs `
        -PassThru
    $updateCommand = Assert-CommandLineIsUpdate $updateProcess.Id
    $updateWindow = Wait-Until { Get-VisibleUpdateProcess $updateProcess.Id } 15 `
        "The /UPDATE installer did not expose a visible titled progress window (phase=$(Read-UpdateFailurePhase))."

    # Match the real in-app handoff: the panel quits after launching the
    # installer, while its detached observer remains for the installer itself
    # to stop. Stopping the observer here would make the installer's own --stop
    # correctly report "nothing was running" and exercise rollback instead of
    # the success path.
    Stop-Process -Id $oldPanelPid -Force -ErrorAction Stop
    $null = Wait-Until { @(Get-ProcessesFromInstall | Where-Object { $_.ProcessId -eq $oldPanelPid }).Count -eq 0 } `
        15 "The old temp-installed panel did not exit."

    $null = Wait-ProcessExitBounded $updateProcess 120 "Update installer"
    $updateProcess.Refresh()
    if ($ExpectRollback) {
        if ($updateProcess.ExitCode -eq 0) {
            throw "Fault-injected update unexpectedly returned exit code 0."
        }
        $phase = Read-UpdateFailurePhase
        if ($phase -notlike "fault-post-activation*") {
            throw "Fault-injected update reported unexpected phase=$phase."
        }
    } elseif ($updateProcess.ExitCode -ne 0) {
        $phase = Read-UpdateFailurePhase
        throw "Update installer returned exit code $($updateProcess.ExitCode), phase=$phase."
    }

    $newPanel = Wait-Until { Get-PanelProcess } 30 `
        "The update did not relaunch Sandglass from the same temp install directory."
    $newPanelPid = [int]$newPanel.ProcessId
    if (-not $newPanel.ExecutablePath.StartsWith(
            $installDir + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase)) {
        throw "Relaunched Sandglass is not from the temp install directory."
    }

    $selfTest = Start-Process -FilePath $oldExe -ArgumentList "--self-test" `
        -PassThru -WindowStyle Hidden
    $null = Wait-ProcessExitBounded $selfTest 45 "Updated executable self-test"
    $selfTest.Refresh()
    if ($selfTest.ExitCode -ne 0) {
        throw "Updated executable self-test returned exit code $($selfTest.ExitCode)."
    }
    $provenanceValue = Read-Provenance $installDir
    if ($ExpectRollback) {
        if ($provenanceValue.git_head -ne $oldProvenance.git_head -or
            $provenanceValue.build_id -ne $oldProvenance.build_id) {
            throw "Fault-injected update did not restore the old build provenance."
        }
    } else {
        if ($provenanceValue.git_head -ne $ExpectedGitCommit) {
            throw "Installed provenance HEAD $($provenanceValue.git_head) does not equal ExpectedGitCommit $ExpectedGitCommit."
        }
        if ($provenanceValue.git_head -eq $oldProvenance.git_head) {
            throw "Update did not change installed provenance git_head."
        }
        if ($provenanceValue.build_id -eq $oldProvenance.build_id) {
            throw "Update did not change installed provenance build_id."
        }
    }
    if ($provenanceValue.git_dirty -ne $false) {
        throw "Installed build provenance is not from a clean worktree."
    }

    foreach ($residue in @(
        $updateBackupPath, "$installDir\.update-backup", $updateStagePath
    )) {
        if (Test-Path -LiteralPath $residue) { throw "Update residue remains: $residue" }
    }

    Invoke-InstalledStop
    Stop-InstalledProcesses
    $uninstallerPath = Join-Path $installDir "Uninstall.exe"
    if (-not (Test-Path -LiteralPath $uninstallerPath)) {
        $uninstallerPath = Join-Path $installDir "unins000.exe"
    }
    if (-not (Test-Path -LiteralPath $uninstallerPath)) { throw "Updated uninstaller is missing." }
    # Use the same self-copy path as the registered uninstaller. Passing
    # _?=$installDir forces it to execute in place, which makes its own file and
    # directory undeletable and creates a smoke-only residue the real user path
    # does not create.
    $uninstall = Start-Process -FilePath $uninstallerPath -ArgumentList "/S" `
        -PassThru -WindowStyle Hidden
    $uninstallAttempted = $true
    $null = Wait-ProcessExitBounded $uninstall 120 "Updated uninstaller"
    $uninstall.Refresh()
    if ($uninstall.ExitCode -ne 0) { throw "Updated uninstaller returned exit code $($uninstall.ExitCode)." }
    $null = Wait-Until { -not (Test-Path -LiteralPath $installDir) } 30 `
        "Registered uninstall path did not remove the temporary install directory."
    if (-not (Test-Path -LiteralPath $sandglassHome -PathType Container)) {
        throw "SANDGLASS_HOME was removed by uninstall."
    }
    if ((Get-TreeSnapshot $providerRoot) -cne $providerBefore) {
        throw "Provider fixtures changed during update, self-test, or uninstall."
    }
    if ($ExpectRollback) {
        Write-Output ("PASS fault-injected post-activation rollback, old-version restart, " +
            "old provenance restored, residue cleanup, state preservation, provider byte invariance")
    } else {
        Write-Output ("PASS update protocol, visible /UPDATE progress, parent wait, " +
            "same-directory restart, provenance, rollback cleanup, state preservation, " +
            "provider byte invariance")
    }
}
catch {
    $failure = $_
    [Console]::Error.WriteLine("UPDATE SMOKE FAILED: $($_.Exception.Message)")
}
finally {
    try {
        if ($null -ne $updateProcess) {
            try {
                if (-not $updateProcess.HasExited) {
                    Stop-Process -Id $updateProcess.Id -Force -ErrorAction SilentlyContinue
                    $null = Wait-ProcessExitBounded $updateProcess 10 "Update installer cleanup"
                }
            } catch { if ($null -eq $failure) { $failure = $_ } }
        }
        try {
            foreach ($row in @(Get-ProcessesFromInstall)) {
                Stop-Process -Id $row.ProcessId -Force -ErrorAction SilentlyContinue
            }
            $null = Wait-Until { @(Get-ProcessesFromInstall).Count -eq 0 } 20 `
                "Temporary Sandglass processes did not stop during smoke cleanup."
        } catch { if ($null -eq $failure) { $failure = $_ } }

        try {
            foreach ($name in $oldEnvironment.Keys) {
                if ($null -eq $oldEnvironment[$name]) {
                    Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
                } else {
                    Set-Item -Path "Env:$name" -Value $oldEnvironment[$name]
                }
            }
        } catch { if ($null -eq $failure) { $failure = $_ } }

        # Each registry restoration is isolated: an export failure or restore
        # failure for one key must never cause the other key to be deleted.
        try { Restore-RegistryKey $oldInstallKey $oldInstallState }
        catch { if ($null -eq $failure) { $failure = $_ } }
        try { Restore-RegistryKey $oldUninstallKey $oldUninstallState }
        catch { if ($null -eq $failure) { $failure = $_ } }
        try { Restore-RunValue $oldRunValueState }
        catch { if ($null -eq $failure) { $failure = $_ } }

        try {
            if ($uninstallAttempted) {
                foreach ($target in @(
                    $installDir,
                    (Join-Path $installDir "Sandglass.exe"),
                    $uninstallerPath
                )) {
                    if ($target -and (Test-Path -LiteralPath $target)) {
                        throw "Uninstall left temporary target behind: $target"
                    }
                }
            }
        } catch { if ($null -eq $failure) { $failure = $_ } }

        # Only these exact paths are owned by this smoke. They are siblings of
        # smokeRoot because NSIS stages updates beneath %TEMP% by parent PID.
        try {
            foreach ($residue in @($updateStagePath, $updateBackupPath, $updateFailureLog)) {
                if ($residue -and (Test-Path -LiteralPath $residue)) {
                    Remove-ExactResidue $residue
                }
            }
        } catch { if ($null -eq $failure) { $failure = $_ } }
    } catch {
        if ($null -eq $failure) { $failure = $_ }
    } finally {
        try { Remove-SmokeRoot }
        catch { if ($null -eq $failure) { $failure = $_ } }
    }
}

# Native helpers used while restoring an originally absent registry key can
# legitimately leave $LASTEXITCODE at 1. Reaching here means every assertion
# and the full finally cleanup completed; make the smoke process's result
# explicit instead of leaking an incidental helper exit code to its caller.
if ($null -ne $failure) {
    [Console]::Error.WriteLine("UPDATE SMOKE CLEANUP FAILED: $($failure.Exception.Message)")
    exit 1
}
exit 0
