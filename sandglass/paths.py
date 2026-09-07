from __future__ import annotations

import os
import ctypes
import hashlib
import threading
from pathlib import Path
from ctypes import wintypes


class StateHomeAttestationError(RuntimeError):
    """The process cannot prove that its state home is the real state home."""


_STATE_HOME_CACHE: dict[str, dict[str, object]] = {}
_STATE_HOME_CACHE_LOCK = threading.Lock()
_ERROR_INSUFFICIENT_BUFFER = 122
_APPMODEL_ERROR_NO_PACKAGE = 15700
_ERROR_SUCCESS = 0
_INVALID_HANDLE_VALUE = -1
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_CREATE_NEW = 1
_FILE_ATTRIBUTE_TEMPORARY = 0x00000100
_FILE_FLAG_DELETE_ON_CLOSE = 0x04000000
_TOKEN_QUERY = 0x0008
_TOKEN_IS_APPCONTAINER = 29


def _path_hash(value: object) -> str:
    return hashlib.sha256(str(value).casefold().encode("utf-8")).hexdigest()


def _lexical_path(path: Path) -> str:
    """Return an absolute spelling without resolving junctions or links."""
    return os.path.abspath(os.fspath(path))


def _normalise_final_path(value: str) -> str:
    value = str(value or "")
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.normpath(value))


def _package_identity() -> tuple[bool, bool, str]:
    """Return (known, allowed, reason) for the current Windows package."""
    if os.name != "nt":
        return True, True, "non_windows"
    try:
        kernel = ctypes.windll.kernel32
        fn = kernel.GetCurrentPackageFullName
        fn.argtypes = [ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
        fn.restype = wintypes.LONG
        length = wintypes.UINT(0)
        result = int(fn(ctypes.byref(length), None))
        if result == _APPMODEL_ERROR_NO_PACKAGE:
            return True, True, "unpackaged"
        if result != _ERROR_INSUFFICIENT_BUFFER or not length.value:
            return False, False, "package_identity_api_ambiguous"
        buffer = ctypes.create_unicode_buffer(length.value)
        result = int(fn(ctypes.byref(length), buffer))
        if result != _ERROR_SUCCESS or not buffer.value:
            return False, False, "package_identity_api_ambiguous"
        package = buffer.value.casefold()
        expected = str(os.environ.get("SANDGLASS_PACKAGE_IDENTITY") or "").casefold()
        if expected:
            allowed = package == expected
        else:
            # The public desktop is a Win32 app and has no package. If it is
            # packaged, only a package explicitly naming Sandglass is ours.
            allowed = package.split("_", 1)[0].startswith(("sandglass", "ayun.sandglass"))
        return True, allowed, "package_identity_foreign" if not allowed else "packaged_sandglass"
    except Exception:  # noqa: BLE001 - an ambiguous identity must fail closed
        return False, False, "package_identity_api_ambiguous"


def _is_app_container() -> tuple[bool, bool, str]:
    """Return (known, is_app_container, reason)."""
    if os.name != "nt":
        return True, False, "non_windows"
    token = wintypes.HANDLE()
    try:
        kernel = ctypes.windll.kernel32
        advapi = ctypes.windll.advapi32
        advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi.OpenProcessToken.restype = wintypes.BOOL
        advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        advapi.GetTokenInformation.restype = wintypes.BOOL
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
            return False, False, "token_api_ambiguous"
        try:
            value = wintypes.BOOL()
            needed = wintypes.DWORD()
            if not advapi.GetTokenInformation(token, _TOKEN_IS_APPCONTAINER, ctypes.byref(value), ctypes.sizeof(value), ctypes.byref(needed)):
                return False, False, "token_api_ambiguous"
            return True, bool(value.value), "app_container" if value.value else "desktop_token"
        finally:
            kernel.CloseHandle(token)
    except Exception:  # noqa: BLE001
        return False, False, "token_api_ambiguous"


def _win32_probe(final_target: str) -> tuple[bool, str, str]:
    """Create a delete-on-close file and ask Windows where its handle landed."""
    kernel = ctypes.windll.kernel32
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(final_target, _GENERIC_READ | _GENERIC_WRITE,
                    _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
                    None, _CREATE_NEW,
                    _FILE_ATTRIBUTE_TEMPORARY | _FILE_FLAG_DELETE_ON_CLOSE, None)
    if handle in (None, 0, _INVALID_HANDLE_VALUE):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        get_final = kernel.GetFinalPathNameByHandleW
        get_final.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        get_final.restype = wintypes.DWORD
        size = 512
        while size <= 32768:
            buffer = ctypes.create_unicode_buffer(size)
            length = int(get_final(handle, buffer, size, 0))
            if length == 0:
                raise ctypes.WinError(ctypes.get_last_error())
            if length < size:
                return True, buffer.value, "probe_ok"
            size *= 2
        raise OSError("final path is too long")
    finally:
        kernel.CloseHandle(handle)


def state_home_attestation(*, path: Path | None = None, diagnostic: bool = False) -> dict[str, object]:
    """Measure whether this process's writes land in its Sandglass state home.

    The cache contains only successful results. Failure is measured again on
    every call so a transient API result cannot turn into an unsafe pass.

    A junction or directory symlink the user pointed the state home through
    is not a failure: other processes opening that same path see the same
    files. AppContainer copy-on-write is: CreateFileW of a new file lands in
    a package store that the spelled path does not name.
    """
    target = path or meter_home()
    spelled = _lexical_path(target)
    key = spelled.casefold()
    with _STATE_HOME_CACHE_LOCK:
        cached = _STATE_HOME_CACHE.get(key)
    if cached is not None:
        return dict(cached)

    result: dict[str, object] = {
        "ok": False,
        "windows": os.name == "nt",
        "package_identity_known": False,
        "package_identity_ok": False,
        "app_container_known": False,
        "app_container": False,
        "probe_ok": False,
        "final_path_matches": False,
        "reason": "",
        "target_path_sha256": "",
        "final_path_sha256": "",
    }
    try:
        package_known, package_ok, package_reason = _package_identity()
        result.update(package_identity_known=package_known, package_identity_ok=package_ok)
        if not package_known or not package_ok:
            result["reason"] = package_reason
            return result
        token_known, in_container, token_reason = _is_app_container()
        result.update(app_container_known=token_known, app_container=in_container)
        if not token_known or in_container:
            result["reason"] = token_reason
            return result
        # Directory creation is part of the probe, never a side effect of an
        # identity or token failure.
        target.mkdir(parents=True, exist_ok=True)
        probe_name = (
            f".state-home-probe-{os.getpid()}-{threading.get_ident()}"
            if os.name == "nt"
            else ".audit-redirect-probe"
        )
        probe = target / probe_name
        lexical_probe = _lexical_path(probe)
        # Resolve the existing parent before creating the probe. Junctions
        # and directory symlinks are followed; AppContainer write
        # virtualization of a newly created file is not, because that write
        # is the CreateFileW below.
        try:
            expected_final = str(target.resolve() / probe_name)
        except OSError:
            expected_final = lexical_probe
        result["target_path_sha256"] = _path_hash(_normalise_final_path(expected_final))
        if os.name == "nt":
            probe_ok, landed, probe_reason = _win32_probe(lexical_probe)
        else:
            probe.write_bytes(b"")
            try:
                landed = str(probe.resolve())
            finally:
                probe.unlink(missing_ok=True)
            probe_ok, probe_reason = True, "probe_ok"
        result["probe_ok"] = probe_ok
        result["final_path_sha256"] = _path_hash(_normalise_final_path(landed))
        if not diagnostic:
            result["landed_path"] = landed
        matches = _normalise_final_path(landed) == _normalise_final_path(expected_final)
        result["final_path_matches"] = matches
        result["ok"] = bool(probe_ok and matches)
        result["reason"] = probe_reason if matches else "probe_final_path_mismatch"
    except Exception as exc:  # noqa: BLE001 - callers need a fail-closed reason
        result["reason"] = f"probe_error:{type(exc).__name__}"
    if result["ok"]:
        with _STATE_HOME_CACHE_LOCK:
            _STATE_HOME_CACHE[key] = dict(result)
    response = dict(result)
    if not diagnostic and not result.get("ok") and "landed_path" in result:
        response["final_path"] = result["landed_path"]
    response.pop("landed_path", None)
    return response


def require_canonical_state_home() -> Path:
    """Fail before normal state access if Windows identity or redirection is unsafe."""
    result = state_home_attestation()
    if not result.get("ok"):
        raise StateHomeAttestationError(
            f"Sandglass state home attestation failed ({result.get('reason') or 'unknown'})"
        )
    return meter_home()


def _clear_state_home_attestation_cache() -> None:
    """Test hook; no failure result is ever retained by the production path."""
    with _STATE_HOME_CACHE_LOCK:
        _STATE_HOME_CACHE.clear()


def home() -> Path:
    return Path.home()


def claude_homes() -> list[Path]:
    """Every Claude config dir. CLAUDE_CONFIG_DIR may list several, comma separated."""
    override = os.environ.get("CLAUDE_CONFIG_DIR") or os.environ.get("CLAUDE_HOME")
    if override:
        parts = [chunk.strip() for chunk in override.split(",")]
        dirs = [Path(chunk).expanduser() for chunk in parts if chunk]
        if dirs:
            return dirs
    return [home() / ".claude"]


def claude_home() -> Path:
    return claude_homes()[0]


def claude_projects() -> Path:
    override = os.environ.get("CLAUDE_PROJECTS")
    if override:
        return Path(override).expanduser()
    return claude_home() / "projects"


def codex_home() -> Path:
    override = os.environ.get("CODEX_HOME")
    if override:
        return Path(override.split(",")[0]).expanduser()
    return home() / ".codex"


def grok_home() -> Path:
    override = os.environ.get("GROK_HOME")
    if override:
        return Path(override).expanduser()
    return home() / ".grok"


def meter_home() -> Path:
    override = os.environ.get("SANDGLASS_HOME")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "sandglass"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(home() / "AppData" / "Local")
        return Path(base) / "sandglass"
    return home() / ".cache" / "sandglass"


def cache_db() -> Path:
    return meter_home() / "cache.sqlite"


def telemetry_db() -> Path:
    return meter_home() / "telemetry.sqlite"


def user_sources_db() -> Path:
    return meter_home() / "user-sources.sqlite"
