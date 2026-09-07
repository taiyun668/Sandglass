param(
    [Parameter(Mandatory = $true)]
    [string]$Bundle
)

$ErrorActionPreference = "Stop"
$bundlePath = [System.IO.Path]::GetFullPath($Bundle)
$executable = Join-Path $bundlePath "Sandglass.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Packaged executable not found: $executable"
}

$tempBase = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
$tempBase = [System.IO.Path]::GetFullPath($tempBase)
$smokeRoot = Join-Path $tempBase ("SandglassBundleSmoke-" + [guid]::NewGuid().ToString("N"))
$providerRoot = Join-Path $smokeRoot "providers"
$sandglassHome = Join-Path $smokeRoot "sandglass-home"
$observedProcessIds = [System.Collections.Generic.HashSet[int]]::new()

function Assert-UnderSmokeRoot([string]$Path) {
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $prefix = $smokeRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if ($resolved -ne $smokeRoot -and
        -not $resolved.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing bundle-smoke mutation outside $smokeRoot`: $resolved"
    }
}

Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

namespace SandglassBundleSmoke
{
    public sealed class WindowSnapshot
    {
        public IntPtr Handle { get; set; }
        public int ProcessId { get; set; }
        public string ClassName { get; set; }
        public string Title { get; set; }
        public bool Visible { get; set; }
    }

    public static class NativeWindows
    {
        private delegate bool EnumWindowsProc(IntPtr window, IntPtr parameter);

        [DllImport("user32.dll")]
        private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetClassNameW(IntPtr window, StringBuilder value, int maximum);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowTextW(IntPtr window, StringBuilder value, int maximum);

        [DllImport("user32.dll")]
        private static extern bool IsWindowVisible(IntPtr window);

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);

        [DllImport("user32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool PostMessageW(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);

        public static WindowSnapshot[] Enumerate()
        {
            var result = new List<WindowSnapshot>();
            EnumWindows(delegate(IntPtr window, IntPtr parameter)
            {
                uint processId;
                GetWindowThreadProcessId(window, out processId);
                var className = new StringBuilder(512);
                var title = new StringBuilder(512);
                GetClassNameW(window, className, className.Capacity);
                GetWindowTextW(window, title, title.Capacity);
                result.Add(new WindowSnapshot {
                    Handle = window,
                    ProcessId = unchecked((int)processId),
                    ClassName = className.ToString(),
                    Title = title.ToString(),
                    Visible = IsWindowVisible(window),
                });
                return true;
            }, IntPtr.Zero);
            return result.ToArray();
        }
    }
}
"@

function Get-CandidateProcessIds([int]$RootProcessId) {
    $rows = @(Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId -ErrorAction SilentlyContinue)
    $pending = [System.Collections.Generic.Queue[int]]::new()
    $seen = [System.Collections.Generic.HashSet[int]]::new()
    $pending.Enqueue($RootProcessId)
    while ($pending.Count -gt 0) {
        $current = $pending.Dequeue()
        if (-not $seen.Add($current)) { continue }
        [void]$observedProcessIds.Add($current)
        foreach ($row in $rows) {
            if ([int]$row.ParentProcessId -eq $current) {
                $pending.Enqueue([int]$row.ProcessId)
            }
        }
    }
    return @($seen)
}

function Wait-ForOwnedWindow(
    [System.Diagnostics.Process]$RootProcess,
    [scriptblock]$Match,
    [string]$Description,
    [int]$TimeoutSeconds
) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $RootProcess.Refresh()
        if ($RootProcess.HasExited) {
            throw "Packaged Sandglass exited before $Description appeared (exit code $($RootProcess.ExitCode))."
        }
        $candidateIds = @(Get-CandidateProcessIds $RootProcess.Id)
        foreach ($window in [SandglassBundleSmoke.NativeWindows]::Enumerate()) {
            if ($candidateIds -contains $window.ProcessId -and (& $Match $window)) {
                return $window
            }
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for $Description."
}

function Wait-ForRuntimeProof([System.Diagnostics.Process]$RootProcess, [int]$TimeoutSeconds) {
    $proofPath = Join-Path $sandglassHome "runtime-provenance.json"
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $RootProcess.Refresh()
        if ($RootProcess.HasExited) {
            throw "Packaged Sandglass exited before runtime provenance was recorded."
        }
        if (Test-Path -LiteralPath $proofPath -PathType Leaf) {
            try {
                $proof = Get-Content -LiteralPath $proofPath -Raw | ConvertFrom-Json
                if ([int]$proof.api_bridge.success_count -gt 0) {
                    return $proof
                }
            } catch {
                # The writer uses atomic replace; a transient antivirus delay may
                # still make this read fail, so retry until the bounded deadline.
            }
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for proof that the packaged PanelShell bridge served an API request."
}

$oldEnvironment = @{
    CLAUDE_CONFIG_DIR = $env:CLAUDE_CONFIG_DIR
    CODEX_HOME = $env:CODEX_HOME
    GROK_HOME = $env:GROK_HOME
    SANDGLASS_HOME = $env:SANDGLASS_HOME
}
$process = $null
try {
    Assert-UnderSmokeRoot $providerRoot
    Assert-UnderSmokeRoot $sandglassHome
    New-Item -ItemType Directory -Force -Path `
        (Join-Path $providerRoot "claude"), `
        (Join-Path $providerRoot "codex"), `
        (Join-Path $providerRoot "grok"), `
        $sandglassHome | Out-Null

    $env:CLAUDE_CONFIG_DIR = Join-Path $providerRoot "claude"
    $env:CODEX_HOME = Join-Path $providerRoot "codex"
    $env:GROK_HOME = Join-Path $providerRoot "grok"
    $env:SANDGLASS_HOME = $sandglassHome

    # This gate intentionally allows UI windows: their visibility is the evidence.
    $process = Start-Process -FilePath $executable -WorkingDirectory $bundlePath -PassThru
    [void]$observedProcessIds.Add($process.Id)

    $orb = Wait-ForOwnedWindow $process {
        param($window)
        $window.ClassName -ceq "SandglassOrb" -and $window.Visible
    } "the visible SandglassOrb window" 30

    $WM_LBUTTONUP = 0x0202
    if (-not [SandglassBundleSmoke.NativeWindows]::PostMessageW(
        $orb.Handle, $WM_LBUTTONUP, [IntPtr]::Zero, [IntPtr]::Zero
    )) {
        throw "Failed to activate the packaged Sandglass orb."
    }

    $panel = Wait-ForOwnedWindow $process {
        param($window)
        $window.Visible -and
        $window.Title -ceq "sandglass" -and
        $window.ClassName.StartsWith("HwndWrapper[", [System.StringComparison]::Ordinal)
    } "the WebView2-backed native WPF panel after its first fit message" 45

    $proof = Wait-ForRuntimeProof $process 20
    if ($proof.panel_bridge.mode -cne "packaged" -or
        $proof.panel_bridge.contract_valid -ne $true -or
        -not $proof.panel_bridge.content_sha256 -or
        -not $proof.web_index.content_sha256) {
        throw "Packaged runtime provenance does not identify a valid packaged bridge and web index."
    }

    $process.Refresh()
    if ($process.HasExited) {
        throw "Packaged Sandglass exited after opening its native panel."
    }
    Write-Output ("PASS packaged desktop launch: orb HWND 0x{0:X}, native panel HWND 0x{1:X}, bridge {2}" -f `
        $orb.Handle.ToInt64(), $panel.Handle.ToInt64(),
        $proof.panel_bridge.content_sha256.Substring(0, 12))
}
finally {
    if ($null -ne $process) {
        foreach ($candidate in @(Get-CandidateProcessIds $process.Id)) {
            [void]$observedProcessIds.Add($candidate)
        }
    }
    foreach ($processId in @($observedProcessIds) | Sort-Object -Descending) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }

    foreach ($name in $oldEnvironment.Keys) {
        if ($null -eq $oldEnvironment[$name]) {
            Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
        } else {
            Set-Item -Path "Env:$name" -Value $oldEnvironment[$name]
        }
    }
    Assert-UnderSmokeRoot $smokeRoot
    if (Test-Path -LiteralPath $smokeRoot) {
        Remove-Item -LiteralPath $smokeRoot -Recurse -Force
    }
}
