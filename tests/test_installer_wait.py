"""Regression coverage for the installer smoke process boundary."""

import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

if os.name == "nt":
    import winreg


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "tools" / "smoke_windows_installer.ps1"
BUILD = ROOT / "tools" / "build_windows_release.ps1"


def _powershells() -> list[str]:
    result = []
    for name in ("powershell.exe", "pwsh.exe"):
        path = shutil.which(name)
        if path and path not in result:
            result.append(path)
    return result


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)
    ]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.TerminateProcess.restype = ctypes.c_int
    return kernel32


def _alive(pid: int) -> bool:
    if os.name != "nt":
        return False
    kernel32 = _kernel32()
    process = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not process:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(process)


def _terminate(pid: int) -> None:
    if os.name != "nt" or not _alive(pid):
        return
    kernel32 = _kernel32()
    process = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if process:
        try:
            kernel32.TerminateProcess(process, 1)
        finally:
            kernel32.CloseHandle(process)


@unittest.skipUnless(
    os.name == "nt" and _powershells(), "Windows PowerShell is required"
)
class InstallerWaitTests(unittest.TestCase):
    def test_post_install_wait_requires_both_runtime_owners(self):
        """An ephemeral packaged PID is not evidence that the app is ready."""
        source = SMOKE.read_text(encoding="utf-8")
        function_start = source.index("function Wait-ForInstalledSandglass")
        function_end = source.index("function Stop-InstalledSandglass", function_start)
        function_source = source[function_start:function_end]
        self.assertIn("Test-MutexHeld $instanceMutexName", function_source)
        self.assertIn("Test-MutexHeld $observerMutexName", function_source)
        self.assertLess(
            function_source.index("$running.Count -gt 0"),
            function_source.index("return $running"),
        )

    def test_the_packaged_self_test_gate_reads_the_process_exit_code(self):
        """A GUI-subsystem child is not waited for by a direct call.

        The gate used `& (Join-Path $bundle Sandglass.exe) --self-test` and then read
        $LASTEXITCODE. PowerShell does not wait for GUI-subsystem binaries
        invoked that way: measured here, the call returned in 0.01s with the
        previous command's 0 still in $LASTEXITCODE while the process printed
        its refusal afterwards, and Start-Process -Wait reported the real 2.
        The gate could not fail, and it was hiding a packaged build that
        refused to run at all.

        pythonw.exe stands in because it is the GUI-subsystem binary this
        project is guaranteed to have; it exits 2 on --self-test. This covers
        the direction that discriminates -- a real non-zero exit must stop the
        build. It does not exercise a passing self-test, which needs a GUI
        stand-in that accepts the flag.
        """
        source = BUILD.read_text(encoding="utf-8")
        start = source.index("if (-not $SkipRuntimeSmoke) {")
        end = source.index("python -m tools.windows_release inspect", start)
        block = source[start:end]

        stand_in = Path(sys.executable).with_name("pythonw.exe")
        if not stand_in.exists():
            self.fail("pythonw.exe is required to stand in for the packaged exe")

        with tempfile.TemporaryDirectory(prefix="sandglass-selftest-gate-") as tmp:
            bundle = Path(tmp) / "bundle"
            bundle.mkdir()
            shutil.copy2(stand_in, bundle / "Sandglass.exe")
            for powershell in _powershells():
                with self.subTest(powershell=powershell):
                    runner = Path(tmp) / f"gate-{Path(powershell).stem}.ps1"
                    runner.write_text(
                        "$ErrorActionPreference = 'Stop'\n"
                        "$SkipRuntimeSmoke = $false\n"
                        f"$bundle = '{str(bundle).replace(chr(39), chr(39) * 2)}'\n"
                        "$global:LASTEXITCODE = 0\n"
                        + block,
                        encoding="utf-8",
                    )
                    done = subprocess.run(
                        [powershell, "-NoLogo", "-NoProfile", "-ExecutionPolicy",
                         "Bypass", "-File", str(runner)],
                        cwd=ROOT, capture_output=True, text=True,
                    )
                    self.assertNotEqual(
                        done.returncode, 0,
                        "自检退出 2，构建却继续了:\n" + done.stdout + done.stderr,
                    )
                    self.assertIn("self-test failed with exit code 2", done.stderr)

    def test_cleanup_waits_for_the_exited_process_image_to_be_released(self):
        """Cleanup proves deletion instead of assuming process exit released files."""
        source = SMOKE.read_text(encoding="utf-8")
        function_start = source.index("function Remove-SmokeRoot")
        function_end = source.index("Assert-UnderSmokeRoot $installDir", function_start)
        function_source = source[function_start:function_end]

        with tempfile.TemporaryDirectory(prefix="sandglass-installer-cleanup-") as tmp:
            root = Path(tmp) / "smoke-root"
            root.mkdir()
            locked = root / "mapped.dll"
            locked.write_bytes(b"fixture")

            for powershell in _powershells():
                with self.subTest(powershell=powershell):
                    if not root.exists():
                        root.mkdir()
                        locked.write_bytes(b"fixture")
                    ready = Path(tmp) / f"holder-{Path(powershell).stem}.ready"
                    error = Path(tmp) / f"holder-{Path(powershell).stem}.err"
                    holder_script = Path(tmp) / f"holder-{Path(powershell).stem}.ps1"
                    holder_script.write_text(
                        "param([string]$Locked, [string]$Ready, [string]$ErrorFile)\n"
                        "$ErrorActionPreference = 'Stop'\n"
                        "try {\n"
                        "  $deadline = [datetime]::UtcNow.AddSeconds(8)\n"
                        "  $f = $null\n"
                        "  while ($null -eq $f) {\n"
                        "    try {\n"
                        "      $f = [IO.File]::Open($Locked,'Open','ReadWrite','None')\n"
                        "    } catch {\n"
                        "      if ([datetime]::UtcNow -ge $deadline) { throw }\n"
                        "      Start-Sleep -Milliseconds 50\n"
                        "    }\n"
                        "  }\n"
                        "  [IO.File]::WriteAllText($Ready, 'ready')\n"
                        "  Start-Sleep -Milliseconds 1200\n"
                        "  $f.Dispose()\n"
                        "} catch {\n"
                        "  [IO.File]::WriteAllText($ErrorFile, $_.Exception.ToString())\n"
                        "  throw\n"
                        "}\n",
                        encoding="utf-8",
                    )
                    holder = subprocess.Popen(
                        [
                            powershell, "-NoLogo", "-NoProfile",
                            "-ExecutionPolicy", "Bypass", "-File",
                            str(holder_script), str(locked), str(ready),
                            str(error),
                        ],
                        cwd=ROOT,
                    )
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline and not ready.exists():
                        if holder.poll() is not None:
                            break
                        time.sleep(0.05)
                    why = error.read_text(encoding="utf-8", errors="replace") if error.exists() else ""
                    self.assertTrue(
                        ready.exists(),
                        "file-lock holder did not start"
                        + (f" (exit={holder.poll()}): {why}" if why or holder.poll() is not None else ""),
                    )
                    runner = Path(tmp) / f"cleanup-{Path(powershell).stem}.ps1"
                    runner.write_text(
                        "$ErrorActionPreference = 'Stop'\n"
                        f"$smokeRoot = '{str(root).replace("'", "''")}'\n"
                        + function_source
                        + "Remove-SmokeRoot\n",
                        encoding="utf-8",
                    )
                    cleanup = subprocess.run(
                        [
                            powershell, "-NoLogo", "-NoProfile",
                            "-ExecutionPolicy", "Bypass", "-File", str(runner),
                        ],
                        cwd=ROOT,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=10,
                    )
                    holder.wait(timeout=5)
                    self.assertEqual(cleanup.returncode, 0, cleanup.stderr)
                    self.assertFalse(root.exists())

    def test_smoke_refuses_when_the_observer_mutex_is_held_before_running_installer(self):
        """The real observer precondition rejects a held mutex before any install."""
        source = SMOKE.read_text(encoding="utf-8")
        function_start = source.index("function Assert-MutexAvailable")
        function_end = source.index(
            "# The silent path now starts Sandglass", function_start
        )
        function_source = source[function_start:function_end]
        observer_literal = '$observerMutexName = "Local\\Sandglass.Observer.SingleInstance"'
        observer_call = "Assert-MutexAvailable $observerMutexName"
        self.assertIn(observer_literal, source)
        self.assertIn(observer_call, source)
        self.assertLess(source.index(observer_call), source.index("$tempBase"))

        with tempfile.TemporaryDirectory(prefix="sandglass-installer-mutex-") as tmp:
            root = Path(tmp)
            holder = root / "hold-observer-mutex.ps1"
            holder.write_text(
                "param([string]$Name, [string]$Ready, [string]$Release)\n"
                "$createdNew = $false\n"
                "$mutex = [System.Threading.Mutex]::new($true, $Name, [ref]$createdNew)\n"
                "if (-not $createdNew) {\n"
                "  [IO.File]::WriteAllText($Ready, 'existing-mutex')\n"
                "  exit 73\n"
                "}\n"
                "try {\n"
                "  [IO.File]::WriteAllText($Ready, 'ready')\n"
                "  while (-not (Test-Path -LiteralPath $Release)) {\n"
                "    Start-Sleep -Milliseconds 50\n"
                "  }\n"
                "} finally {\n"
                "  $mutex.Dispose()\n"
                "}\n",
                encoding="utf-8",
            )

            for powershell in _powershells():
                with self.subTest(powershell=powershell):
                    mutex_name = (
                        "Local\\Sandglass.InstallerSmoke.ObserverProbe."
                        + uuid.uuid4().hex
                    )
                    ready = root / f"holder-ready-{uuid.uuid4().hex}.txt"
                    release = root / f"release-holder-{uuid.uuid4().hex}.txt"
                    check = root / f"check-observer-precondition-{uuid.uuid4().hex}.ps1"
                    check.write_text(
                        "$ErrorActionPreference = 'Stop'\n"
                        + function_source
                        + f"$probeMutexName = '{mutex_name}'\n"
                        + "Assert-MutexAvailable $probeMutexName "
                        + "'observer probe held' 'probe mutex unavailable: '\n"
                        + "exit 0\n",
                        encoding="utf-8",
                    )
                    holder_process = subprocess.Popen(
                        [
                            powershell, "-NoLogo", "-NoProfile",
                            "-ExecutionPolicy", "Bypass", "-File", str(holder),
                            mutex_name,
                            str(ready), str(release),
                        ],
                        cwd=ROOT,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    try:
                        deadline = time.monotonic() + 15
                        while time.monotonic() < deadline and not ready.exists():
                            if holder_process.poll() is not None:
                                break
                            time.sleep(0.05)
                        if not ready.exists():
                            stdout, stderr = holder_process.communicate(timeout=2)
                            self.fail(
                                "observer mutex holder did not start: "
                                f"stdout={stdout!r} stderr={stderr!r}"
                            )
                        self.assertEqual(ready.read_text(encoding="utf-8"), "ready")
                        self.assertIsNone(
                            holder_process.poll(),
                            "observer mutex holder exited before the check",
                        )

                        smoke = subprocess.run(
                            [
                                powershell, "-NoLogo", "-NoProfile",
                                "-ExecutionPolicy", "Bypass", "-File", str(check),
                            ],
                            cwd=ROOT,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            timeout=15,
                        )
                        output = smoke.stdout + smoke.stderr
                        self.assertNotEqual(smoke.returncode, 0, output)
                        self.assertIn(
                            "observer probe held",
                            output,
                        )
                    finally:
                        release.write_text("release", encoding="utf-8")
                        try:
                            holder_process.communicate(timeout=5)
                        except subprocess.TimeoutExpired:
                            _terminate(holder_process.pid)
                            holder_process.wait(timeout=2)

    def test_extracted_launch_blocks_return_for_both_exit_codes(self):
        """Each real installer launch waits only for its direct process.

        The fake installer parent records a long-lived child, then exits with
        the requested installer result.  The child is the same shape as NSIS's
        silent post-install Exec.  The launch blocks are extracted from the
        smoke script, so restoring process-tree ``-Wait`` makes the bounded
        communicate call fail instead of producing a false pass.
        """
        source = SMOKE.read_text(encoding="utf-8")
        first_start = source.index("$process = Start-Process")
        first_end = source.index("if ($process.ExitCode", first_start)
        first_block = source[first_start:first_end]
        second_start = source.index("$again = Start-Process")
        second_end = source.index("if ($again.ExitCode", second_start)
        second_block = source[second_start:second_end]
        blocks = ((first_block, "$process"), (second_block, "$again"))

        with tempfile.TemporaryDirectory(prefix="sandglass-installer-wait-") as tmp:
            root = Path(tmp)
            parent = root / "fake-installer.ps1"
            launcher = root / "fake-installer.cmd"
            parent.write_text(
                "param([string]$Handshake, [int]$ExitCode)\n"
                "$child = Start-Process -FilePath $env:FAKE_PS "
                "-ArgumentList @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass',"
                "'-Command','Start-Sleep -Seconds 60') "
                "-PassThru -WindowStyle Hidden\n"
                "[IO.File]::WriteAllText($Handshake, \"$($child.Id)|$ExitCode\")\n"
                "exit $ExitCode\n",
                encoding="utf-8",
            )
            launcher.write_text(
                "@echo off\n"
                '"%FAKE_PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass '
                '-File "%FAKE_PARENT%" "%FAKE_HANDSHAKE%" %FAKE_EXIT%\n'
                "exit /b %FAKE_EXIT%\n",
                encoding="utf-8",
            )

            for powershell in _powershells():
                for block, process_variable in blocks:
                    for expected in (0, 2):
                        handshake = root / (
                            f"handshake-{Path(powershell).stem}-"
                            f"{process_variable[1:]}-{expected}.txt"
                        )
                        runner = root / (
                            f"run-{Path(powershell).stem}-"
                            f"{process_variable[1:]}-{expected}.ps1"
                        )
                        runner.write_text(
                            "$ErrorActionPreference = 'Stop'\n"
                            "$installerPath = $args[0]\n"
                            "$installDir = $args[1]\n"
                            "$env:FAKE_PS = $args[2]\n"
                            "$env:FAKE_PARENT = $args[3]\n"
                            "$env:FAKE_HANDSHAKE = $args[4]\n"
                            "$env:FAKE_EXIT = $args[5]\n"
                            f"{block}"
                            f"if ({process_variable}.ExitCode -ne [int]$args[5]) {{ exit 41 }}\n"
                            f"exit {process_variable}.ExitCode\n",
                            encoding="utf-8",
                        )
                        process = subprocess.Popen(
                            [
                                powershell,
                                "-NoLogo",
                                "-NoProfile",
                                "-ExecutionPolicy",
                                "Bypass",
                                "-File",
                                str(runner),
                                str(launcher),
                                str(root / "install-dir"),
                                powershell,
                                str(parent),
                                str(handshake),
                                str(expected),
                            ],
                            cwd=ROOT,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )
                        child_pid = None
                        try:
                            deadline = time.monotonic() + 15
                            while time.monotonic() < deadline and not handshake.exists():
                                if process.poll() is not None:
                                    break
                                time.sleep(0.05)
                            if not handshake.exists():
                                stdout, stderr = process.communicate(timeout=2)
                                self.fail(
                                    "fake installer did not publish handshake: "
                                    f"stdout={stdout!r} stderr={stderr!r}"
                                )
                            child_pid, recorded = map(int, handshake.read_text().split("|"))
                            self.assertEqual(recorded, expected)
                            self.assertTrue(
                                _alive(child_pid),
                                "descendant exited before wait returned",
                            )
                            try:
                                _stdout, stderr = process.communicate(timeout=15)
                            except subprocess.TimeoutExpired as exc:
                                self.fail(
                                    f"launch block did not return for exit code {expected}; "
                                    f"descendant {child_pid} remained alive: {exc}"
                                )
                            self.assertEqual(process.returncode, expected, stderr)
                            self.assertTrue(
                                _alive(child_pid),
                                "descendant must outlive direct-process wait",
                            )
                        finally:
                            if process.poll() is None:
                                process.kill()
                                process.wait(timeout=2)
                            if child_pid is not None:
                                _terminate(child_pid)


def _makensis() -> Path | None:
    extra = []
    env_compiler = os.environ.get("NSIS_COMPILER") or os.environ.get("NsisCompiler")
    if env_compiler:
        extra.append(Path(env_compiler))
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", "")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", "")
    candidates = extra + [
        Path(local) / "SandglassBuildTools" / "nsis-3.12" / "makensis.exe",
        Path(local) / "Programs" / "NSIS" / "makensis.exe",
        Path(program_files) / "NSIS" / "makensis.exe",
        Path(program_files_x86) / "NSIS" / "makensis.exe",
    ]
    which = shutil.which("makensis")
    if which:
        candidates.append(Path(which))
    for path in candidates:
        if path and path.is_file():
            return path
    return None


def _nsi_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def _nsi_function(source: str, name: str) -> str:
    start = source.index(f"Function {name}")
    end = source.index("FunctionEnd", start) + len("FunctionEnd")
    return source[start:end]


def _nsi_uninstall_section(source: str) -> str:
    start = source.index('Section "Uninstall"')
    end = source.index("SectionEnd", start) + len("SectionEnd")
    return source[start:end]


def _restore_rename_desktop_gate(section: str) -> str:
    start = section.index("Call un.CheckSandglassDesktopMutex")
    end = section.index('${If} ${FileExists} "$INSTDIR\\Sandglass.exe"')
    return (
        section[:start]
        + '    ClearErrors\n'
        + '    Rename "$INSTDIR\\Sandglass.exe" "$INSTDIR\\Sandglass.exe.removing"\n'
        + '    ${If} ${Errors}\n'
        + '      SetErrorLevel 9\n'
        + '      Abort\n'
        + '    ${EndIf}\n'
        + '    Rename "$INSTDIR\\Sandglass.exe.removing" "$INSTDIR\\Sandglass.exe"\n\n  '
        + section[end:]
    )


def _tree_snapshot(root: Path) -> dict[str, tuple[int, str]]:
    snapshot = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            snapshot[path.relative_to(root).as_posix()] = (
                len(data),
                hashlib.sha256(data).hexdigest(),
            )
    return snapshot


def _reg_value(path: str, name: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value
    except OSError:
        return None


def _delete_reg_key(path: str) -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


def _delete_run_value(name: str) -> None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, name)
    except OSError:
        pass


class _HeldMutex:
    def __init__(self, name: str):
        self.name = name
        self.handle = None
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p
        ]
        self._kernel32.CreateMutexW.restype = ctypes.c_void_p
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = ctypes.c_int

    def __enter__(self):
        ctypes.set_last_error(0)
        handle = self._kernel32.CreateMutexW(None, True, self.name)
        error = ctypes.get_last_error()
        if not handle:
            raise OSError(error, f"CreateMutexW failed for {self.name}")
        if error == 183:
            self._kernel32.CloseHandle(handle)
            raise RuntimeError(f"mutex already held: {self.name}")
        self.handle = handle
        return self

    def __exit__(self, *exc):
        if self.handle:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None


class _UninstallProbe:
    def __init__(
        self,
        install_dir: Path,
        uninstaller: Path,
        shortcut: Path,
        desktop_mutex: str,
        product_key: str,
        uninstall_key: str,
        run_value: str,
        files_before: dict[str, tuple[int, str]],
        shortcut_before: bytes,
        reg_before: dict[str, object],
    ):
        self.install_dir = install_dir
        self.uninstaller = uninstaller
        self.shortcut = shortcut
        self.desktop_mutex = desktop_mutex
        self.product_key = product_key
        self.uninstall_key = uninstall_key
        self.run_value = run_value
        self.files_before = files_before
        self.shortcut_before = shortcut_before
        self.reg_before = reg_before

    def registration(self) -> dict[str, object]:
        return {
            "install_dir": _reg_value(self.product_key, "InstallDir"),
            "display_name": _reg_value(self.uninstall_key, "DisplayName"),
            "uninstall_string": _reg_value(self.uninstall_key, "UninstallString"),
            "run": _reg_value(
                r"Software\Microsoft\Windows\CurrentVersion\Run", self.run_value
            ),
        }

    def cleanup_registry(self) -> None:
        _delete_run_value(self.run_value)
        _delete_reg_key(self.uninstall_key)
        _delete_reg_key(self.product_key)


@unittest.skipUnless(os.name == "nt", "Windows NSIS uninstaller probes are required")
class UninstallDesktopMutexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.makensis = _makensis()
        if cls.makensis is None:
            raise unittest.SkipTest(
                "NSIS 3.12 is required to compile the uninstall mutex probe"
            )

    def _compile_nsi(self, source: Path) -> None:
        done = subprocess.run(
            [str(self.makensis), "/V2", "/WX", str(source)],
            cwd=source.parent,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if done.returncode != 0:
            self.fail(
                f"makensis failed ({done.returncode}):\n{done.stdout}\n{done.stderr}"
            )

    def _compile_stop_stub(self, dest: Path) -> None:
        script = dest.with_suffix(".nsi")
        script.write_text(
            "Unicode True\n"
            "RequestExecutionLevel user\n"
            "SilentInstall silent\n"
            'Name "SandglassStopStub"\n'
            f'OutFile "{_nsi_path(dest)}"\n'
            "Function .onInit\n"
            "  SetSilent silent\n"
            "FunctionEnd\n"
            "Section\n"
            "SectionEnd\n",
            encoding="utf-8-sig",
        )
        self._compile_nsi(script)

    def _prepare_probe(self, root: Path, restore_rename: bool) -> _UninstallProbe:
        token = uuid.uuid4().hex
        install_dir = root / "installed"
        shortcut_dir = root / "start-menu"
        setup_exe = root / "probe-setup.exe"
        stub_exe = root / "Sandglass.exe"
        nsi_path = root / "probe.nsi"
        install_dir.mkdir(parents=True)
        shortcut_dir.mkdir()
        self._compile_stop_stub(stub_exe)

        source = (ROOT / "packaging" / "sandglass.nsi").read_text(encoding="utf-8")
        observer_fn = _nsi_function(source, "un.CheckSandglassMutex")
        desktop_fn = _nsi_function(source, "un.CheckSandglassDesktopMutex")
        uninstall = _nsi_uninstall_section(source)
        if restore_rename:
            uninstall = _restore_rename_desktop_gate(uninstall)
            self.assertNotIn("Call un.CheckSandglassDesktopMutex", uninstall)
            self.assertIn("Sandglass.exe.removing", uninstall)

        desktop_mutex = f"Local\\Sandglass.UninstallProbe.Desktop.{token}"
        observer_mutex = f"Local\\Sandglass.UninstallProbe.Observer.{token}"
        product_key = f"Software\\SandglassUninstallProbe_{token}"
        uninstall_key = (
            "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
            f"SandglassUninstallProbe_{token}"
        )
        run_value = f"sandglass-uninstall-probe-{token}"
        observer_fn = observer_fn.replace(
            r"Local\Sandglass.Observer.SingleInstance", observer_mutex
        )
        desktop_fn = desktop_fn.replace(
            r"Local\Sandglass.Desktop.SingleInstance", desktop_mutex
        )
        uninstall = (
            uninstall.replace(
                r'HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass"',
                f'HKCU "{uninstall_key}"',
            )
            .replace(
                r'HKCU "Software\Sandglass"',
                f'HKCU "{product_key}"',
            )
            .replace(
                r'HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "sandglass"',
                f'HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "{run_value}"',
            )
            .replace(
                r'Delete "$SMPROGRAMS\Sandglass\Sandglass.lnk"',
                f'Delete "{(shortcut_dir / "Sandglass.lnk").resolve()}"',
            )
            .replace(
                r'RMDir "$SMPROGRAMS\Sandglass"',
                f'RMDir "{shortcut_dir.resolve()}"',
            )
        )

        nsi_path.write_text(
            "Unicode True\n"
            "RequestExecutionLevel user\n"
            '!include "LogicLib.nsh"\n'
            'Name "SandglassUninstallProbe"\n'
            f'OutFile "{_nsi_path(setup_exe)}"\n'
            f'InstallDir "{_nsi_path(install_dir)}"\n'
            "SilentInstall silent\n"
            "Section\n"
            '  SetOutPath "$INSTDIR"\n'
            r'  WriteUninstaller "$INSTDIR\Uninstall.exe"'
            "\nSectionEnd\n\n"
            f"{observer_fn}\n\n"
            f"{'' if restore_rename else desktop_fn + chr(10) + chr(10)}"
            f"{uninstall}\n",
            encoding="utf-8-sig",
        )
        self._compile_nsi(nsi_path)
        setup = subprocess.run(
            [str(setup_exe), "/S", f"/D={install_dir}"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(setup.returncode, 0, setup.stderr or setup.stdout)
        uninstaller = install_dir / "Uninstall.exe"
        self.assertTrue(uninstaller.is_file(), "probe uninstaller was not written")

        shutil.copy2(stub_exe, install_dir / "Sandglass.exe")
        (install_dir / "LICENSE").write_text("license", encoding="utf-8")
        (install_dir / "PRIVACY.md").write_text("privacy", encoding="utf-8")
        (install_dir / "SUPPORT.md").write_text("support", encoding="utf-8")
        (install_dir / "THIRD_PARTY_NOTICES.md").write_text("notices", encoding="utf-8")
        (install_dir / "Sandglass-owned-paths.json").write_text("{}", encoding="utf-8")
        (install_dir / ".sandglass-owner").write_text("owner", encoding="utf-8")
        (install_dir / "_internal").mkdir()
        (install_dir / "_internal" / "payload.bin").write_bytes(b"internal")
        (install_dir / "THIRD_PARTY_LICENSES").mkdir()
        (install_dir / "THIRD_PARTY_LICENSES" / "notice.txt").write_text(
            "third", encoding="utf-8"
        )
        (install_dir / "owner-custom").mkdir()
        (install_dir / "owner-custom" / "keep.txt").write_text("keep", encoding="utf-8")
        (shortcut_dir / "Sandglass.lnk").write_bytes(b"shortcut")

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, product_key) as key:
            winreg.SetValueEx(key, "InstallDir", 0, winreg.REG_SZ, str(install_dir))
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, uninstall_key) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "Sandglass")
            winreg.SetValueEx(
                key, "UninstallString", 0, winreg.REG_SZ, str(uninstaller)
            )
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
        ) as key:
            winreg.SetValueEx(
                key,
                run_value,
                0,
                winreg.REG_SZ,
                f'"{install_dir / "Sandglass.exe"}"',
            )

        shortcut = shortcut_dir / "Sandglass.lnk"
        probe = _UninstallProbe(
            install_dir=install_dir,
            uninstaller=uninstaller,
            shortcut=shortcut,
            desktop_mutex=desktop_mutex,
            product_key=product_key,
            uninstall_key=uninstall_key,
            run_value=run_value,
            files_before=_tree_snapshot(install_dir),
            shortcut_before=shortcut.read_bytes(),
            reg_before={},
        )
        probe.reg_before = probe.registration()
        self.assertEqual(probe.reg_before["install_dir"], str(install_dir))
        self.assertEqual(probe.reg_before["display_name"], "Sandglass")
        self.assertIsNotNone(probe.reg_before["run"])
        return probe

    def _run_uninstaller(self, probe: _UninstallProbe, env: dict[str, str]):
        return subprocess.run(
            [str(probe.uninstaller), "/S", f"_?={probe.install_dir}"],
            cwd=str(probe.install_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_held_desktop_mutex_refuses_uninstall_and_stopped_uninstall_still_succeeds(
        self,
    ):
        """A held desktop mutex must fail closed before any product deletion.

        The rename-based gate accepted this state: Windows allows renaming a
        loaded PyInstaller Sandglass.exe, so Uninstall.exe /S returned 0 and
        deleted registration, Run, shortcuts, and most files while the panel
        kept running. Restoring that rename probe must make this test fail
        specifically because that partial uninstall is accepted.
        """
        with tempfile.TemporaryDirectory(prefix="sg-uninst-") as tmp:
            root = Path(tmp)
            sandglass_home = root / "sandglass-home"
            sandglass_home.mkdir()
            home_before = _tree_snapshot(sandglass_home)
            env = os.environ.copy()
            env["SANDGLASS_HOME"] = str(sandglass_home)

            correct = self._prepare_probe(root / "correct", restore_rename=False)
            try:
                with _HeldMutex(correct.desktop_mutex):
                    refused = self._run_uninstaller(correct, env)
                    self.assertEqual(
                        refused.returncode,
                        9,
                        refused.stdout + refused.stderr,
                    )
                    self.assertEqual(
                        _tree_snapshot(correct.install_dir), correct.files_before
                    )
                    self.assertEqual(correct.registration(), correct.reg_before)
                    self.assertTrue((correct.install_dir / "Sandglass.exe").is_file())
                    self.assertTrue((correct.install_dir / "Uninstall.exe").is_file())
                    self.assertTrue(
                        (correct.install_dir / "owner-custom" / "keep.txt").is_file()
                    )
                    self.assertEqual(
                        correct.shortcut.read_bytes(), correct.shortcut_before
                    )
                    self.assertEqual(_tree_snapshot(sandglass_home), home_before)

                succeeded = self._run_uninstaller(correct, env)
                self.assertEqual(
                    succeeded.returncode, 0, succeeded.stdout + succeeded.stderr
                )
                self.assertFalse((correct.install_dir / "Sandglass.exe").exists())
                self.assertFalse((correct.install_dir / "_internal").exists())
                self.assertFalse(
                    (correct.install_dir / "THIRD_PARTY_LICENSES").exists()
                )
                self.assertTrue(
                    (correct.install_dir / "owner-custom" / "keep.txt").is_file()
                )
                self.assertIsNone(correct.registration()["install_dir"])
                self.assertIsNone(correct.registration()["display_name"])
                self.assertIsNone(correct.registration()["run"])
                self.assertFalse(correct.shortcut.exists())
                self.assertEqual(_tree_snapshot(sandglass_home), home_before)
            finally:
                correct.cleanup_registry()

            mutated = self._prepare_probe(root / "mutated", restore_rename=True)
            try:
                with _HeldMutex(mutated.desktop_mutex):
                    accepted = self._run_uninstaller(mutated, env)

                    def assert_held_mutex_refuses() -> None:
                        self.assertNotEqual(accepted.returncode, 0)
                        self.assertEqual(
                            _tree_snapshot(mutated.install_dir),
                            mutated.files_before,
                        )
                        self.assertEqual(
                            mutated.registration(), mutated.reg_before
                        )
                        self.assertTrue(mutated.shortcut.is_file())
                        self.assertEqual(
                            mutated.shortcut.read_bytes(), mutated.shortcut_before
                        )

                    with self.assertRaises(AssertionError):
                        assert_held_mutex_refuses()
                    self.assertEqual(
                        accepted.returncode,
                        0,
                        "rename-gate mutation must accept uninstall while the "
                        f"desktop mutex is held, got {accepted.returncode}",
                    )
                    self.assertFalse(
                        (mutated.install_dir / "Sandglass.exe").exists(),
                        "rename-gate mutation must delete product files while "
                        "the desktop mutex is held",
                    )
                    self.assertIsNone(
                        mutated.registration()["run"],
                        "rename-gate mutation must delete registration while "
                        "the desktop mutex is held",
                    )
                    self.assertFalse(
                        mutated.shortcut.exists(),
                        "rename-gate mutation must delete the shortcut while "
                        "the desktop mutex is held",
                    )
                    self.assertEqual(_tree_snapshot(sandglass_home), home_before)
            finally:
                mutated.cleanup_registry()


if __name__ == "__main__":
    unittest.main()
