"""Non-identifying evidence for the exact assets used by this process."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sandglass.paths import meter_home


_LOCK = threading.RLock()
_ACTIVE: dict[str, Any] = {}
_BUILD_PROVENANCE_NAME = "Sandglass-build-provenance.json"
_IDENTITY_SCHEMA = 2
_NATIVE_RUNTIME_FILES = (
    "Microsoft.Web.WebView2.Core.dll",
    "Microsoft.Web.WebView2.Wpf.dll",
    "WebView2Loader.dll",
)


def provenance_path() -> Path:
    return meter_home() / "runtime-provenance.json"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _path_hash(path: Path) -> str:
    return _sha256(str(path.resolve()).lower().encode("utf-8"))


def _file_evidence(path: Path, content: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if content is None else content
    return {
        "name": path.name,
        "path_sha256": _path_hash(path),
        "content_sha256": _sha256(data),
        "bytes": len(data),
    }


def _entries_digest(entries: dict[str, dict[str, Any]]) -> str:
    """Hash names and exact bytes without retaining either paths or contents."""

    return _sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _native_runtime_evidence(root: Path) -> dict[str, Any]:
    """Identify the exact Microsoft WebView2 files loaded by the panel."""

    files = {
        name: _file_evidence(root / name)
        for name in _NATIVE_RUNTIME_FILES
    }
    return {"files": files, "content_sha256": _entries_digest(files)}


def _source_runtime_evidence(root: Path) -> dict[str, Any]:
    """Identify editable Python and static resources used by the source runtime."""

    package = root / "sandglass"
    files: dict[str, dict[str, Any]] = {}
    if package.is_dir():
        for path in sorted(package.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(root).as_posix()
            # Both numbers from the same read. The size used to come from a
            # later stat(), so a file edited in between was recorded as one row
            # describing two different versions of itself -- the digest of the
            # old bytes beside the length of the new ones.
            payload = path.read_bytes()
            files[relative] = {
                "content_sha256": _sha256(payload),
                "bytes": len(payload),
            }
    return {"files": files, "content_sha256": _entries_digest(files)}


def _git_dir(root: Path) -> Path | None:
    marker = root / ".git"
    if marker.is_dir():
        return marker
    try:
        text = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.lower().startswith("gitdir:"):
        return None
    target = Path(text.split(":", 1)[1].strip())
    return target if target.is_absolute() else (root / target).resolve()


def _git_head(root: Path) -> str:
    git_dir = _git_dir(root)
    if git_dir is None:
        return ""
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not head.startswith("ref:"):
        return head if len(head) == 40 else ""
    ref = head.split(":", 1)[1].strip()
    candidates = [git_dir / ref]
    try:
        common = (git_dir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        common = ""
    common_dir = (git_dir / common).resolve() if common else git_dir
    candidates.append(common_dir / ref)
    for candidate in candidates:
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if len(value) == 40:
            return value
    try:
        packed = (common_dir / "packed-refs").read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in packed.splitlines():
        if line.endswith(f" {ref}"):
            return line.split(" ", 1)[0]
    return ""


def _packaged_build_provenance() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / _BUILD_PROVENANCE_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict) or value.get("schema") != 1:
        return {}
    head = str(value.get("git_head") or "")
    build_id = str(value.get("build_id") or "")
    if len(head) != 40 or len(build_id) != 64 or value.get("git_dirty") is not False:
        return {}
    # Recompute it. The build-time inspector in tools/windows_release.py already
    # requires build_id to be the digest of the rest of the manifest; the
    # runtime only measured its length, so any 64 hex characters were accepted
    # and a hand-edited git_head kept a build_id that no longer described it.
    # This does not make the manifest authentic -- only a signature can, and the
    # release is not signed yet. It makes it self-consistent, which is the rule
    # the build already applies to the same bytes.
    if build_id != _provenance_build_id(value):
        return {}
    return value


def _provenance_build_id(value: dict[str, Any]) -> str:
    """The digest the build computes over everything but the digest itself."""
    unsigned = {key: item for key, item in value.items() if key != "build_id"}
    return hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _source_root_evidence(root: Path) -> dict[str, Any]:
    packaged = _packaged_build_provenance()
    evidence = {
        "path_sha256": _path_hash(root),
        "git_head": str(packaged.get("git_head") or _git_head(root)),
        "build_id": str(packaged.get("build_id") or ""),
        "project_version": str(packaged.get("project_version") or ""),
        "packaged_provenance": bool(packaged),
    }
    # A packaged build's validated build_id is authoritative.  Its source tree is
    # not present as editable Python files -- PyInstaller packs them into the
    # PYZ, and the only thing under _internal/sandglass is the manifest plus the
    # web and native resources, which build_provenance binds separately. So a
    # second source digest here would restate those resources rather than say
    # anything about the Python, while making valid bundles fail to match.
    if not packaged:
        evidence["runtime_source"] = _source_runtime_evidence(root)
    return evidence


def build_runtime_identity(*, source_root: Path, web_index: Path,
                           native_runtime: Path, panel_bridge: Path,
                           bridge_content: bytes, bridge_mode: str) -> dict[str, Any]:
    """Describe exact executable resources without exposing absolute paths."""

    return {
        "schema": _IDENTITY_SCHEMA,
        "source_root": _source_root_evidence(source_root),
        "web_index": _file_evidence(web_index),
        "native_runtime": _native_runtime_evidence(native_runtime),
        "panel_bridge": {
            **_file_evidence(panel_bridge, bridge_content),
            "mode": bridge_mode,
            "contract_valid": True,
        },
    }


def build_web_runtime_identity(*, source_root: Path, web_index: Path) -> dict[str, Any]:
    """Describe a standalone dashboard server without borrowing desktop evidence."""

    return {
        "schema": _IDENTITY_SCHEMA,
        "runtime_role": "web_server",
        "source_root": _source_root_evidence(source_root),
        "web_index": _file_evidence(web_index),
    }


def _write(payload: dict[str, Any]) -> bool:
    path = provenance_path()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        # Not raised: identity recording is bookkeeping beside a process that
        # must keep starting. But `_ACTIVE` used to be stamped first, so
        # in-process reads looked healthy while the file a second launch and
        # the audit use stayed missing or stale.
        from sandglass.diagnostics import record_component_failure

        record_component_failure("runtime_provenance_write", exc)
        return False
    from sandglass.diagnostics import clear_component_failure

    clear_component_failure("runtime_provenance_write")
    return True


def record_runtime_identity(identity: dict[str, Any]) -> None:
    global _ACTIVE
    with _LOCK:
        payload = {
            **identity,
            "process_id": os.getpid(),
            "process_started_at": datetime.now(timezone.utc).isoformat(),
            "api_bridge": {"success_count": 0, "last_status": None},
        }
        if _write(payload):
            _ACTIVE = payload


def record_api_bridge_result(status: int) -> None:
    """Persist the first working bridge call for packaged smoke evidence."""

    global _ACTIVE
    with _LOCK:
        if not _ACTIVE:
            return
        bridge = dict(_ACTIVE.get("api_bridge") or {})
        previous = int(bridge.get("success_count") or 0)
        if status < 400:
            bridge["success_count"] = previous + 1
        bridge["last_status"] = int(status)
        payload = {**_ACTIVE, "api_bridge": bridge}
        if previous == 0 or status >= 400:
            if not _write(payload):
                return
        _ACTIVE = payload


def runtime_provenance() -> dict[str, Any]:
    with _LOCK:
        if _ACTIVE:
            return json.loads(json.dumps(_ACTIVE))
    try:
        value = json.loads(provenance_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def runtime_identity_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Compare only fields that prove code/resource identity."""

    try:
        if expected["schema"] != _IDENTITY_SCHEMA or actual["schema"] != _IDENTITY_SCHEMA:
            return False
        common = all((
            expected["source_root"] == actual["source_root"],
            expected["web_index"]["content_sha256"]
                == actual["web_index"]["content_sha256"],
        ))
        if expected.get("runtime_role") == "web_server":
            return common and actual.get("runtime_role") == "web_server"
        return all((
            common,
            expected["native_runtime"] == actual["native_runtime"],
            expected["panel_bridge"]["content_sha256"]
                == actual["panel_bridge"]["content_sha256"],
            expected["panel_bridge"]["mode"]
                == actual["panel_bridge"]["mode"],
        ))
    except (KeyError, TypeError):
        return False


def process_is_running(pid: int) -> bool:
    """Check a recorded PID without treating Windows signal semantics as POSIX."""

    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel32.CloseHandle(handle)
