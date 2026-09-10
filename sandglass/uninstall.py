"""Safe removal of an installed Windows Sandglass copy.

The installed executable is the uninstall entry point.  Validation happens in
this process, while the final file removal is performed by the system
cmd.exe process because this executable is necessarily still mapped until it
returns.  The command is built entirely from validated literal paths; it
never interprets a product path as shell syntax.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PRODUCT_MANIFEST = "Sandglass-owned-paths.json"
INSTALLATION_MUTEX = r"Local\Sandglass.Installation.Mutation"
DESKTOP_MUTEX = r"Local\Sandglass.Desktop.SingleInstance"
OBSERVER_MUTEX = r"Local\Sandglass.Observer.SingleInstance"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Sandglass"
PRODUCT_KEY = r"Software\Sandglass"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
ALLOWED_TOP_LEVEL_NAMES = frozenset(
    {
        "Sandglass.exe", PRODUCT_MANIFEST, "_internal", "LICENSE", "PRIVACY.md",
        "SUPPORT.md", "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_LICENSES",
        ".sandglass-owner",
    }
)


class UninstallError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _message(text: str, *, error: bool = False) -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, text, "Sandglass", 0x10 if error else 0x30)
    except (AttributeError, OSError):
        pass


def _mutex_state(name: str) -> str:
    """Return held/free/unknown, preserving the old fail-closed semantics."""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.OpenMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenMutexW(0x00100000, 0, name)
        if handle:
            kernel32.CloseHandle(handle)
            return "held"
        if ctypes.get_last_error() == 2:
            return "free"
    except (AttributeError, OSError):
        pass
    return "unknown"


def _request_desktop_quit(timeout: float = 15.0) -> str:
    """Ask the installed orb window to quit, then wait for its mutex.

    The window handle is authenticated against the real Sandglass executable
    before the private WM_APP quit message is sent; a same-titled window cannot
    make an uninstall delete files.
    """
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_ssize_t]
        user32.PostMessageW.restype = ctypes.c_int
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        hwnd = user32.FindWindowW("SandglassOrb", "sandglass")
        if not hwnd:
            return "held"
        window_pid = ctypes.c_uint32()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        handle = kernel32.OpenProcess(0x1000, False, window_pid.value)
        if not handle:
            return "held"
        try:
            size = ctypes.c_uint32(32768)
            image = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
                return "held"
            if str(Path(image.value).resolve()).casefold() != str(Path(sys.executable).resolve()).casefold():
                return "held"
        finally:
            kernel32.CloseHandle(handle)
        # The HWND was authenticated above.  Send the private request to that
        # exact handle; rediscovering by title here would reopen the HWND-reuse
        # race after the image check.
        from sandglass.orb import WM_ORB_UNINSTALL

        if not user32.PostMessageW(hwnd, WM_ORB_UNINSTALL, 0, 0):
            return "held"
    except (AttributeError, OSError, ValueError):
        return "held"
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        state = _mutex_state(DESKTOP_MUTEX)
        if state != "held":
            return state
        time.sleep(0.1)
    return _mutex_state(DESKTOP_MUTEX)


def _no_reparse(path: Path) -> None:
    try:
        if path.is_symlink() or (path.lstat().st_file_attributes & 0x400):
            raise UninstallError(2, f"refusing reparse path: {path}")
    except AttributeError:
        if path.is_symlink():
            raise UninstallError(2, f"refusing reparse path: {path}")
    except FileNotFoundError:
        return
    if path.is_dir():
        for child in path.iterdir():
            _no_reparse(child)


def _no_reparse_ancestors(path: Path) -> None:
    current = path
    while current.parent != current:
        try:
            if current.is_symlink() or (current.lstat().st_file_attributes & 0x400):
                raise UninstallError(2, f"refusing reparse ancestor: {current}")
        except AttributeError:
            if current.is_symlink():
                raise UninstallError(2, f"refusing reparse ancestor: {current}")
        except FileNotFoundError:
            pass
        current = current.parent


def _manifest(root: Path) -> list[str]:
    path = root / PRODUCT_MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        names = data.get("paths") if isinstance(data, dict) else None
    except (OSError, ValueError, UnicodeError) as exc:
        raise UninstallError(2, f"invalid product manifest: {exc}") from exc
    if (not isinstance(data, dict) or data.get("schema") != 1
            or not isinstance(names, list) or not names):
        raise UninstallError(2, "invalid product manifest")
    if names != sorted(names) or len(names) != len(set(names)):
        raise UninstallError(2, "product manifest is not sorted and unique")
    required = {"Sandglass.exe", PRODUCT_MANIFEST}
    for name in names:
        if (not isinstance(name, str) or name not in ALLOWED_TOP_LEVEL_NAMES
                or name in {".", ".."} or "/" in name or "\\" in name):
            raise UninstallError(2, f"unsafe product path: {name}")
    if not required.issubset(names):
        raise UninstallError(2, "product manifest omits the launcher")
    return names


def _context() -> tuple[Path, list[str], int]:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise UninstallError(2, "uninstall is available only from a frozen Windows installation")
    exe = Path(sys.executable)
    _no_reparse_ancestors(exe)
    try:
        exe = exe.resolve(strict=True)
    except OSError as exc:
        raise UninstallError(2, f"cannot resolve installed executable: {exc}") from exc
    if exe.name.casefold() != "sandglass.exe":
        raise UninstallError(2, "uninstall executable is not Sandglass.exe")
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PRODUCT_KEY) as key:
            registered_raw = Path(winreg.QueryValueEx(key, "InstallDir")[0])
            if not registered_raw.is_absolute():
                raise UninstallError(2, "registered install directory is not absolute")
            _no_reparse_ancestors(registered_raw)
            registered = registered_raw.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise UninstallError(2, f"installed path is not registered: {exc}") from exc
    root = registered
    if not root.is_dir() or exe.parent != root:
        raise UninstallError(2, "registered install directory does not contain the running executable")
    _no_reparse(root)
    names = _manifest(root)
    for name in names:
        candidate = root / name
        if candidate.exists():
            _no_reparse(candidate)
    return root, names, os.getpid()


def _same_or_below(path: Path, parent: Path) -> bool:
    """Return whether *path* is *parent* or a descendant of it."""
    try:
        path_text = os.path.normcase(os.path.abspath(os.fspath(path)))
        parent_text = os.path.normcase(os.path.abspath(os.fspath(parent)))
        return os.path.commonpath((path_text, parent_text)) == parent_text
    except (OSError, ValueError):
        return False


def _state_home_for_uninstall(root: Path, names: list[str]) -> Path:
    """Validate that the preserved state home is outside product paths.

    The installed tree is the only tree the helper may remove.  A state home
    nested below an owned directory would nevertheless be removed by the
    helper's recursive directory operation, so reject that topology before
    asking either runtime process to stop.  ``resolve(strict=False)`` also
    catches a state-home junction that points into an owned directory.
    """
    from sandglass.paths import meter_home

    state = Path(meter_home())
    if not state.is_absolute():
        raise UninstallError(2, "SANDGLASS_HOME must be an absolute path")
    # Validate the exact spelling before the runtime is asked to stop.  The
    # helper consumes this path through a quoted environment expansion.
    _cmd_path(state, "SANDGLASS_HOME")
    try:
        root_real = root.resolve(strict=True)
        state_real = state.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise UninstallError(2, f"cannot resolve SANDGLASS_HOME: {exc}") from exc
    for name in names:
        try:
            owned_real = (root_real / name).resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise UninstallError(2, f"cannot resolve owned product path: {exc}") from exc
        if _same_or_below(state_real, owned_real):
            raise UninstallError(
                2,
                "SANDGLASS_HOME overlaps an owned installation path; refusing uninstall",
            )
    return state


# Values are expanded by cmd exactly once and every use below is quoted.  The
# characters commonly rejected by shell wrappers (``&``, ``(``, ``%``, ...)
# are valid in Windows user/profile paths and must not make a normal install
# impossible.  A quote would terminate the quoted value; control characters
# can create a second command, so those remain forbidden.
CMD_UNSAFE_CHARS = frozenset('"')


def _cmd_path(value: str | Path, label: str) -> str:
    """Return an absolute path safe to put in a quoted cmd command."""
    text = os.fspath(value)
    if not isinstance(text, str) or not text or not Path(text).is_absolute():
        raise UninstallError(2, f"{label} must be an absolute path")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):
        raise UninstallError(2, f"unsafe {label}: control character")
    if any(char in CMD_UNSAFE_CHARS or char in "*?" for char in text):
        raise UninstallError(2, f"unsafe {label}: cmd metacharacter")
    return text


def _cmd_registry(value: str, label: str) -> str:
    if (not isinstance(value, str) or not value.casefold().startswith("hkcu\\")
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
            or any(char in CMD_UNSAFE_CHARS or char in "*?" for char in value)
            or any(part in {"", ".", ".."} for part in value.split("\\")[1:])):
        raise UninstallError(2, f"unsafe {label}")
    return value


def _system_tool_path(system32: Path, name: str) -> Path:
    """Return a system utility path; kept separate for isolated test shims."""
    return system32 / name


def _normalised_path_text(path: str | Path) -> str:
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(path))))
    except (OSError, TypeError, ValueError):
        return ""


def _run_value_target(value: object) -> str:
    """Extract an unambiguous executable token from a Run command string."""
    if not isinstance(value, str):
        return ""
    text = value.lstrip()
    if not text:
        return ""
    if text.startswith('"'):
        end = text.find('"', 1)
        if end <= 1:
            return ""
        return text[1:end]
    # An unquoted command can only be trusted when its first whitespace-
    # delimited token is the complete path.  Environment indirection is not
    # expanded here because doing so would weaken the ownership proof.
    return text.split(None, 1)[0]


def _run_value_targets(executable: Path, value: object) -> bool:
    target = _run_value_target(value)
    if not target:
        return False
    return _normalised_path_text(target) == _normalised_path_text(executable)


def _product_registry_cleanup(root: Path) -> tuple[str, str]:
    """Return (delete InstallDir, delete now-empty product key) flags.

    Only an InstallDir that still names this validated install is ours.  Any
    extra value or subkey means the product key belongs partly to the owner and
    must remain after that one value is removed.
    """
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PRODUCT_KEY) as key:
            install_dir = winreg.QueryValueEx(key, "InstallDir")[0]
            if _normalised_path_text(install_dir) != _normalised_path_text(root):
                return "0", "0"
            subkeys, values = winreg.QueryInfoKey(key)[:2]
            value_names = {
                str(winreg.EnumValue(key, index)[0])
                for index in range(values)
            }
            delete_key = subkeys == 0 and value_names == {"InstallDir"}
            return "1", "1" if delete_key else "0"
    except (OSError, TypeError, ValueError):
        return "0", "0"


def _run_registry_cleanup(executable: Path) -> str:
    """Return whether the current user's Sandglass Run value is ours."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value = winreg.QueryValueEx(key, "sandglass")[0]
    except (OSError, TypeError, ValueError):
        return "0"
    return "1" if _run_value_targets(executable, value) else "0"


def _close_handles(handles: tuple[int, ...] | list[int]) -> None:
    if not handles or os.name != "nt":
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        for handle in handles:
            if handle:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
    except (AttributeError, OSError):
        pass


def _claim_mutexes(names: tuple[str, ...]) -> tuple[int, ...]:
    """Atomically reserve the requested named mutexes for the helper."""
    if os.name != "nt":
        raise UninstallError(2, "uninstall mutexes are available only on Windows")
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.SetHandleInformation.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        kernel32.SetHandleInformation.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
    except (AttributeError, OSError) as exc:
        raise UninstallError(10, "Windows could not create uninstall mutexes.") from exc

    handles: list[int] = []
    try:
        for name in names:
            ctypes.set_last_error(0)
            handle = kernel32.CreateMutexW(None, True, name)
            if not handle:
                raise UninstallError(10, f"Windows could not reserve {name}.")
            handle_value = int(handle)
            if ctypes.get_last_error() == 183:
                kernel32.CloseHandle(handle)
                raise UninstallError(10, "Sandglass started while uninstall was preparing.")
            if not kernel32.SetHandleInformation(handle, 0x00000001, 0x00000001):
                kernel32.CloseHandle(handle)
                raise UninstallError(10, "Windows could not make uninstall mutexes inheritable.")
            handles.append(handle_value)
    except UninstallError:
        _close_handles(handles)
        raise
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        _close_handles(handles)
        raise UninstallError(10, "Windows could not reserve uninstall mutexes.") from exc
    return tuple(handles)


def _claim_uninstall_mutexes(installation_handle: int | None = None) -> tuple[int, ...]:
    """Claim all three names, or add desktop/observer to an earlier claim."""
    if installation_handle is None:
        return _claim_mutexes((INSTALLATION_MUTEX, DESKTOP_MUTEX, OBSERVER_MUTEX))
    runtime_handles = _claim_mutexes((DESKTOP_MUTEX, OBSERVER_MUTEX))
    return (int(installation_handle), *runtime_handles)


def _claim_installation_mutex() -> int:
    """Reserve the cross-installer mutation gate after user confirmation."""
    return _claim_mutexes((INSTALLATION_MUTEX,))[0]


def _system_directory_path() -> Path:
    """Resolve the authoritative Windows system directory via Win32."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetSystemDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    kernel32.GetSystemDirectoryW.restype = ctypes.c_uint32
    size = 260
    while True:
        buffer = ctypes.create_unicode_buffer(size)
        length = int(kernel32.GetSystemDirectoryW(buffer, size))
        if not length:
            raise UninstallError(2, "Windows system directory is unavailable")
        if length < size - 1:
            break
        size = length + 1
    return Path(buffer.value)


def _known_folder_path(csidl: int, label: str) -> Path:
    """Resolve a user shell folder without consulting a shell interpreter."""
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.SHGetFolderPathW.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
        ctypes.c_wchar_p,
    ]
    shell32.SHGetFolderPathW.restype = ctypes.c_long
    buffer = ctypes.create_unicode_buffer(32768)
    result = int(shell32.SHGetFolderPathW(None, csidl, None, 0, buffer))
    if result != 0 or not buffer.value:
        raise UninstallError(2, f"Windows {label} directory is unavailable")
    path = Path(buffer.value)
    _cmd_path(path, label)
    return path


def _cmd_script(names: list[str]) -> str:
    """Build an ASCII batch helper whose paths arrive only through its env."""
    if not isinstance(names, list) or not names:
        raise UninstallError(2, "invalid product manifest")
    if names != sorted(names) or len(names) != len(set(names)):
        raise UninstallError(2, "product manifest is not sorted and unique")
    for name in names:
        if (not isinstance(name, str) or name not in ALLOWED_TOP_LEVEL_NAMES
                or "/" in name or "\\" in name or name in {".", ".."}):
            raise UninstallError(2, f"unsafe product path: {name}")

    def receipt(status: str, phase: str) -> str:
        payload = json.dumps(
            {"status": status, "phase": phase, "detail": "", "finished_at": ""},
            separators=(",", ":"),
        )
        return (
            f'> "%SANDGLASS_RECEIPT_TMP%" echo {payload} & '
            'move /y "%SANDGLASS_RECEIPT_TMP%" "%SANDGLASS_RECEIPT%" >nul 2>&1'
        )

    required_env = (
        "SANDGLASS_ROOT SANDGLASS_EXE SANDGLASS_RECEIPT "
        "SANDGLASS_RECEIPT_TMP SANDGLASS_DESKTOP SANDGLASS_PROGRAMS "
        "SANDGLASS_PING SANDGLASS_REG SANDGLASS_FC SANDGLASS_UNINSTALL_KEY "
        "SANDGLASS_PRODUCT_KEY SANDGLASS_RUN_KEY SANDGLASS_MANIFEST "
        "SANDGLASS_MANIFEST_SNAPSHOT SANDGLASS_HELPER "
        "SANDGLASS_DELETE_RUN SANDGLASS_DELETE_INSTALL_DIR "
        "SANDGLASS_DELETE_PRODUCT_KEY"
    )
    lines = [
        "@echo off",
        "setlocal EnableExtensions DisableDelayedExpansion",
        f"for %%V in ({required_env}) do if not defined %%V goto fail-validate-install",
        "if not exist \"%SANDGLASS_ROOT%\\.\" goto fail-validate-install",
        # ``fc /b`` is the helper's second, byte-level read of the manifest.
        # It prevents a changed manifest from changing the deletion set after
        # the frozen validation in the launcher.
        "\"%SANDGLASS_FC%\" /b \"%SANDGLASS_MANIFEST%\" \"%SANDGLASS_MANIFEST_SNAPSHOT%\" >nul 2>&1",
        "if errorlevel 1 goto fail-manifest-changed",
        # A delete succeeds only once the parent has released its mapped image.
        # The ping is merely a bounded one-second retry delay; the existence
        # check after ``del`` is the gate, never the delay itself.
        "if not exist \"%SANDGLASS_EXE%\" goto wait-parent-complete",
        "for /l %%i in (1,1,120) do if exist \"%SANDGLASS_EXE%\" (del /f /q \"%SANDGLASS_EXE%\" >nul 2>&1 & if exist \"%SANDGLASS_EXE%\" \"%SANDGLASS_PING%\" 127.0.0.1 -n 2 -w 1000 >nul)",
        ":wait-parent-complete",
        "if exist \"%SANDGLASS_EXE%\" goto fail-wait-parent",
    ]
    for name in names:
        target = f'"%SANDGLASS_ROOT%\\{name}"'
        if name in {"_internal", "THIRD_PARTY_LICENSES"}:
            operation = f"if exist {target} rmdir /s /q {target}"
        elif name != "Sandglass.exe":
            operation = f"if exist {target} del /f /q {target}"
        else:
            continue
        lines.append(operation)
        lines.append("if errorlevel 1 goto fail-delete-owned-paths")

    lines.extend([
        "if exist \"%SANDGLASS_DESKTOP%\\Sandglass.lnk\" del /f /q \"%SANDGLASS_DESKTOP%\\Sandglass.lnk\"",
        "if errorlevel 1 goto fail-delete-shortcuts",
        "if exist \"%SANDGLASS_PROGRAMS%\\Sandglass\\Sandglass.lnk\" del /f /q \"%SANDGLASS_PROGRAMS%\\Sandglass\\Sandglass.lnk\"",
        "if errorlevel 1 goto fail-delete-shortcuts",
        # A non-empty Start Menu folder belongs partly to the owner.  A
        # non-recursive rmdir therefore intentionally leaves it in place.
        "if exist \"%SANDGLASS_PROGRAMS%\\Sandglass\" rmdir /q \"%SANDGLASS_PROGRAMS%\\Sandglass\"",
        "\"%SANDGLASS_REG%\" query \"%SANDGLASS_UNINSTALL_KEY%\" >nul 2>&1",
        "if errorlevel 1 goto registry-product",
        "\"%SANDGLASS_REG%\" delete \"%SANDGLASS_UNINSTALL_KEY%\" /f >nul 2>&1",
        "if errorlevel 1 goto fail-delete-registration",
        ":registry-product",
        "if /i not \"%SANDGLASS_DELETE_INSTALL_DIR%\"==\"1\" goto registry-run-key",
        "\"%SANDGLASS_REG%\" query \"%SANDGLASS_PRODUCT_KEY%\" /v InstallDir >nul 2>&1",
        "if errorlevel 1 goto registry-run-key",
        "\"%SANDGLASS_REG%\" delete \"%SANDGLASS_PRODUCT_KEY%\" /v InstallDir /f >nul 2>&1",
        "if errorlevel 1 goto fail-delete-registration",
        "if /i not \"%SANDGLASS_DELETE_PRODUCT_KEY%\"==\"1\" goto registry-run-key",
        "\"%SANDGLASS_REG%\" query \"%SANDGLASS_PRODUCT_KEY%\" >nul 2>&1",
        "if errorlevel 1 goto registry-run-key",
        "\"%SANDGLASS_REG%\" delete \"%SANDGLASS_PRODUCT_KEY%\" /f >nul 2>&1",
        "if errorlevel 1 goto fail-delete-registration",
        ":registry-run-key",
        "if /i not \"%SANDGLASS_DELETE_RUN%\"==\"1\" goto remove-empty-install-root",
        "\"%SANDGLASS_REG%\" query \"%SANDGLASS_RUN_KEY%\" /v sandglass >nul 2>&1",
        "if errorlevel 1 goto remove-empty-install-root",
        "\"%SANDGLASS_REG%\" delete \"%SANDGLASS_RUN_KEY%\" /v sandglass /f >nul 2>&1",
        "if errorlevel 1 goto fail-delete-registration",
        ":remove-empty-install-root",
        "rmdir /q \"%SANDGLASS_ROOT%\"",
        "goto complete",
        ":fail-wait-parent",
        receipt("failed", "wait-parent"),
        "set \"sg_rc=1\"",
        "goto cleanup",
        ":fail-validate-install",
        receipt("failed", "validate-install"),
        "set \"sg_rc=1\"",
        "goto cleanup",
        ":fail-manifest-changed",
        receipt("failed", "manifest-changed"),
        "set \"sg_rc=1\"",
        "goto cleanup",
        ":fail-delete-owned-paths",
        receipt("failed", "delete-owned-paths"),
        "set \"sg_rc=1\"",
        "goto cleanup",
        ":fail-delete-shortcuts",
        receipt("failed", "delete-shortcuts"),
        "set \"sg_rc=1\"",
        "goto cleanup",
        ":fail-delete-registration",
        receipt("failed", "delete-registration"),
        "set \"sg_rc=1\"",
        "goto cleanup",
        ":complete",
        receipt("success", "complete"),
        "set \"sg_rc=0\"",
        ":cleanup",
        # This helper is the dedicated cmd.exe process, not a CALL frame.  A
        # deleted batch file followed by ``exit /b`` makes cmd search for the
        # vanished frame and report "batch file cannot be found"; plain EXIT
        # terminates the interpreter with the preserved helper status.
        "del /f /q \"%SANDGLASS_MANIFEST_SNAPSHOT%\" >nul 2>&1 & del /f /q \"%SANDGLASS_RECEIPT_TMP%\" >nul 2>&1 & del /f /q \"%SANDGLASS_HELPER%\" >nul 2>&1 & exit %sg_rc%",
    ])
    return "\r\n".join(lines) + "\r\n"


def _launch_helper(root: Path, names: list[str], pid: int,
                   installation_handle: int | None = None) -> subprocess.Popen:
    """Start the detached system ``cmd.exe`` cleanup process."""
    state_home = _state_home_for_uninstall(root, names)
    try:
        state_home.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UninstallError(2, f"could not create SANDGLASS_HOME: {exc}") from exc
    # Re-measure after creating the directory: a late junction or symlink must
    # not turn the preserved state home into a recursive product target.
    state_home = _state_home_for_uninstall(root, names)

    system32 = _system_directory_path()
    cmd = _system_tool_path(system32, "cmd.exe")
    ping = _system_tool_path(system32, "ping.exe")
    reg = _system_tool_path(system32, "reg.exe")
    compare = _system_tool_path(system32, "fc.exe")
    if not all(path.is_file() for path in (cmd, ping, reg, compare)):
        raise UninstallError(2, "required Windows system tools are unavailable")
    from sandglass.paths import meter_home

    # ``state_home`` is derived from meter_home; the second call is an
    # invariant check against a test or runtime that changes that source.
    if Path(meter_home()) != state_home:
        raise UninstallError(2, "SANDGLASS_HOME changed while preparing uninstall")
    receipt = state_home / "uninstall-result.json"
    manifest = Path(root) / PRODUCT_MANIFEST
    root_text = _cmd_path(root, "install root")
    receipt_text = _cmd_path(receipt, "uninstall receipt")
    manifest_text = _cmd_path(manifest, "product manifest")
    desktop_text = _cmd_path(_known_folder_path(0x10, "Desktop"), "Desktop directory")
    programs_text = _cmd_path(_known_folder_path(0x02, "Programs"), "Programs directory")
    ping_text = _cmd_path(ping, "ping.exe")
    reg_text = _cmd_path(reg, "reg.exe")
    compare_text = _cmd_path(compare, "fc.exe")
    uninstall_key = _cmd_registry(rf"HKCU\{UNINSTALL_KEY}", "uninstall registry key")
    product_key = _cmd_registry(rf"HKCU\{PRODUCT_KEY}", "product registry key")
    run_key = _cmd_registry(rf"HKCU\{RUN_KEY}", "Run registry key")

    temporary: list[Path] = []
    script: Path | None = None
    handles: tuple[int, ...] | None = (
        (int(installation_handle),) if installation_handle is not None else None
    )
    handles_to_close: tuple[int, ...] | None = None
    child: subprocess.Popen | None = None
    try:
        receipt.unlink(missing_ok=True)
        snapshot_fd, snapshot_name = tempfile.mkstemp(
            prefix=f".uninstall-manifest-{pid}-", suffix=".json", dir=str(state_home)
        )
        os.close(snapshot_fd)
        snapshot = Path(snapshot_name)
        temporary.append(snapshot)
        script_fd, script_name = tempfile.mkstemp(
            prefix="sandglass-uninstall-", suffix=".cmd", dir=str(state_home)
        )
        os.close(script_fd)
        script = Path(script_name)
        temporary.append(script)
        receipt_tmp_fd, receipt_tmp_name = tempfile.mkstemp(
            prefix=".uninstall-receipt-", suffix=".tmp", dir=str(state_home)
        )
        os.close(receipt_tmp_fd)
        receipt_tmp = Path(receipt_tmp_name)
        temporary.append(receipt_tmp)
        snapshot.write_bytes(manifest.read_bytes())
        script.write_text(_cmd_script(names), encoding="ascii", newline="")
        script_text = _cmd_path(script, "uninstall helper")
        snapshot_text = _cmd_path(snapshot, "uninstall manifest snapshot")
        receipt_tmp_text = _cmd_path(receipt_tmp, "uninstall receipt temporary path")
        environment = os.environ.copy()
        delete_install_dir, delete_product_key = _product_registry_cleanup(root)
        delete_run = _run_registry_cleanup(root / "Sandglass.exe")
        environment.update({
            "SANDGLASS_ROOT": root_text,
            "SANDGLASS_EXE": _cmd_path(Path(root_text) / "Sandglass.exe", "Sandglass.exe"),
            "SANDGLASS_RECEIPT": receipt_text,
            "SANDGLASS_RECEIPT_TMP": receipt_tmp_text,
            "SANDGLASS_DESKTOP": desktop_text,
            "SANDGLASS_PROGRAMS": programs_text,
            "SANDGLASS_PING": ping_text,
            "SANDGLASS_REG": reg_text,
            "SANDGLASS_FC": compare_text,
            "SANDGLASS_UNINSTALL_KEY": uninstall_key,
            "SANDGLASS_PRODUCT_KEY": product_key,
            "SANDGLASS_RUN_KEY": run_key,
            "SANDGLASS_MANIFEST": manifest_text,
            "SANDGLASS_MANIFEST_SNAPSHOT": snapshot_text,
            "SANDGLASS_HELPER": script_text,
            "SANDGLASS_DELETE_RUN": delete_run,
            "SANDGLASS_DELETE_INSTALL_DIR": delete_install_dir,
            "SANDGLASS_DELETE_PRODUCT_KEY": delete_product_key,
        })
        handles = _claim_uninstall_mutexes(installation_handle)
        # A handle claimed by main() is closed there after this function
        # returns.  The runtime copies are owned by this launcher and are
        # closed in the finally block below; direct callers own all copies.
        handles_to_close = handles[1:] if installation_handle is not None else handles
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": list(handles)}
        flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        )
        # The outer pair of quotes is significant: cmd expands the helper path
        # once as a single /c command even when a user directory contains
        # spaces, &, parentheses, %, or !.  /v:off keeps ! literal.
        command_line = f'"{cmd}" /d /q /v:off /c ""%SANDGLASS_HELPER%""'
        child = subprocess.Popen(
            command_line,
            executable=str(cmd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            cwd=str(system32),
            close_fds=True,
            creationflags=flags,
            startupinfo=startup,
        )
    except UninstallError:
        for path in temporary:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    except (OSError, UnicodeError) as exc:
        for path in temporary:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise UninstallError(2, f"could not prepare or start uninstall helper: {exc}") from exc
    finally:
        # The child owns the inherited copies.  Closing the launcher copies is
        # what lets the process exit without keeping either named object alive.
        if handles_to_close:
            _close_handles(handles_to_close)
    if child is None:  # defensive: the only successful path returns a child
        raise UninstallError(2, "uninstall helper did not start")
    return child


def _stop_observer() -> bool:
    from sandglass.observer import stop_and_wait

    return stop_and_wait()


def main(*, quiet: bool = False) -> int:
    installation_handle: int | None = None
    try:
        root, names, pid = _context()
        # This is a read-only topology check, deliberately before any stop
        # request.  The helper recursively removes owned directories, so a
        # state home below one of them would not actually be preserved.
        _state_home_for_uninstall(root, names)
        state = _mutex_state(DESKTOP_MUTEX)
        if state not in ("held", "free"):
            raise UninstallError(10, "Windows could not verify whether Sandglass is running.")
        if not quiet and os.name == "nt":
            answer = ctypes.windll.user32.MessageBoxW(
                None, "This will close Sandglass and uninstall it. Continue?",
                "Sandglass", 0x34,
            )
            if answer != 6:
                return 0
        # A user can leave the prompt open long enough for the runtime to
        # start.  Never act on the pre-confirmation observation.
        state = _mutex_state(DESKTOP_MUTEX)
        if state not in ("held", "free"):
            raise UninstallError(10, "Windows could not verify whether Sandglass is running.")
        # Serialize against NSIS (which takes this same gate in .onInit)
        # before asking either runtime process to stop.  Otherwise an
        # installer can pass its one-time probe while we are shutting down and
        # begin replacing the same tree concurrently.
        installation_handle = _claim_installation_mutex()
        if state == "held":
            state = _request_desktop_quit()
            if state == "held":
                raise UninstallError(9, "Sandglass could not close its desktop safely.")
            if state != "free":
                raise UninstallError(10, "Windows could not verify whether Sandglass stopped.")
        if not _stop_observer():
            raise UninstallError(6, "Sandglass could not confirm that its observer stopped.")
        state = _mutex_state(OBSERVER_MUTEX)
        if state == "held":
            raise UninstallError(7, "Sandglass's background observer is still running.")
        if state != "free":
            raise UninstallError(8, "Windows could not verify whether the observer is running.")
        _launch_helper(root, names, pid, installation_handle=installation_handle)
        return 0
    except UninstallError as exc:
        if not quiet:
            _message(str(exc), error=True)
        return exc.code
    except Exception as exc:  # fail closed before helper launch
        if not quiet:
            _message(f"Sandglass could not be removed: {exc}", error=True)
        return 2
    finally:
        if installation_handle:
            _close_handles((installation_handle,))
