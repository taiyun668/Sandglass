"""What a running process last answered, written down where anyone can check it.

The panel's numbers are reconciled against an independent recomputation, and for
three days that reconciliation did not happen. The check reached the panel over
HTTP, and since the native shell arrived the panel serves its pages over an
in-process bridge with no socket at all -- so the check ran only in the fallback
configuration nobody uses, and skipped everywhere else while reporting that the
service was down. A check that cannot run in the normal configuration is not a
check.

So the answer is recorded instead of requested. Every payload the panel hands
back is written here as it is returned, by the process that produced it, tagged
with the state directory that process actually resolved -- which is not always
the one it spelled, because a Windows app container redirects %LOCALAPPDATA%
per package and two instances can run the same meter_home() into different
directories. An audit then reads a fact rather than asking a question: in every
panel mode, without opening a loopback port that would serve account data to
anything on the machine that cared to ask for it.

One file per process, named by pid, so two of them never write the same bytes
and no lock is needed. Readers prune what is stale.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sandglass.diagnostics import clear_component_failure, record_component_failure
from sandglass.paths import meter_home

PREFIX = "live-snapshot-"
HISTORY = 8            # local-window readings kept, enough to bracket a recompute
_MIN_WRITE_SECONDS = 10.0

_LOCK = threading.RLock()
_ROLE = ""
_LAST_WRITE: dict[str, float] = {}


def set_role(role: str) -> None:
    """Declare what this process is. Nothing is recorded until one does.

    Recording is a write, and api_payload is called by more than the panel --
    tests drive it, and so does anything that imports the module. A process that
    has not said what it is has no business writing an authoritative answer into
    the state directory, so the entry points opt in and everything else is inert.
    """
    global _ROLE
    _ROLE = str(role)


def snapshot_path(pid: int | None = None) -> Path:
    return meter_home() / f"{PREFIX}{pid if pid is not None else os.getpid()}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_own() -> dict[str, Any]:
    """Read this process's snapshot. A missing file is empty; an unreadable one is not.

    FileNotFoundError is the first record. Any other OSError means the file is
    there and we could not read it -- treating that as `{}` made the next
    record write only the new reading over the top, and the write itself
    succeeded so nothing said the history had been wiped. Invalid JSON
    raises: overwriting a file we could not parse is a wipe, not a save.
    """
    try:
        text = snapshot_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        value = {}
    else:
        value = json.loads(text)
    if not isinstance(value, dict) or value.get("schema") != 1:
        value = {}
    readings = value.get("readings")
    value["readings"] = readings if isinstance(readings, dict) else {}
    return value


def _write_own(value: dict[str, Any]) -> None:
    path = snapshot_path()
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def record(kind: str, value: Any) -> None:
    """Note one answer this process just gave. Never raises: it is bookkeeping."""
    if not _ROLE:
        return
    try:
        with _LOCK:
            previous = _LAST_WRITE.get(kind, 0.0)
            elapsed = time.monotonic() - previous
            stored = _read_own()
            readings = stored["readings"]
            entry = {"at": _now(), "value": value}
            if kind == "local_windows":
                history = [row for row in readings.get(kind, []) if isinstance(row, dict)]
                # Only a changed reading, or an aged one, opens a new bracket edge.
                if history and elapsed < _MIN_WRITE_SECONDS and history[-1].get("value") == value:
                    return
                readings[kind] = (history + [entry])[-HISTORY:]
            else:
                current = readings.get(kind)
                if (isinstance(current, dict) and current.get("value") == value
                        and elapsed < _MIN_WRITE_SECONDS):
                    return
                readings[kind] = entry
            home = meter_home().resolve()
            stored.update({
                "schema": 1,
                "process_id": os.getpid(),
                "process_started_at": process_started_at(os.getpid()),
                "role": _ROLE,
                "state_home": str(home),
                "state_home_sha256": hashlib.sha256(
                    str(home).lower().encode("utf-8")
                ).hexdigest(),
                "readings": readings,
            })
            _write_own(stored)
            _LAST_WRITE[kind] = time.monotonic()
    except Exception as exc:  # noqa: BLE001 - a meter must not fail on its own telemetry
        # Not raised: the answer the caller already has must still return.
        # OSError and ValueError already reported; the leftover handler
        # returned with nothing recorded, so a broken identity stamp looked
        # like a quiet interval.
        record_component_failure("live_snapshot_write", exc)
    else:
        clear_component_failure("live_snapshot_write")


def process_started_at(pid: int) -> str:
    """When the process with this id started, or "" if there is no such process.

    A pid on its own does not identify a process. Windows reuses them, and a
    snapshot file named after a dead writer whose number has since been handed
    to something unrelated would read as a living process still answering --
    which is the exact substitution this whole file exists to rule out. The
    creation time makes the pair unambiguous.
    """
    if os.name != "nt":
        return ""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
        ctypes.POINTER(wintypes.FILETIME)
    ] * 4
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    created = wintypes.FILETIME()
    other = [wintypes.FILETIME() for _ in range(3)]
    try:
        ok = kernel.GetProcessTimes(
            handle, ctypes.byref(created), *(ctypes.byref(x) for x in other)
        )
    finally:
        kernel.CloseHandle(handle)
    if not ok:
        return ""
    ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
    return str(ticks)


def live_snapshots(max_age_seconds: float = 900.0) -> list[dict[str, Any]]:
    """Snapshots from processes still alive, newest reading first.

    A file whose process is gone is deleted rather than reported: a dead
    process's last answer looks exactly like a live one that stopped updating,
    and the difference is the entire point of reading this.
    """
    out: list[dict[str, Any]] = []
    home = meter_home()
    for path in sorted(home.glob(f"{PREFIX}*.json")):
        try:
            pid = int(path.stem[len(PREFIX):])
        except ValueError:
            continue
        started = process_started_at(pid)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(value, dict) or value.get("schema") != 1:
            continue
        if not started or str(value.get("process_started_at") or "") != started:
            # Either nothing holds that pid any more, or something else does.
            try:
                path.unlink()
            except OSError:
                pass
            continue
        if newest_age(value) > max_age_seconds:
            continue
        out.append(value)
    out.sort(key=lambda row: newest_age(row))
    return out


def _reading_times(value: dict[str, Any]):
    for row in (value.get("readings") or {}).values():
        rows = row if isinstance(row, list) else [row]
        for item in rows:
            if isinstance(item, dict) and item.get("at"):
                yield str(item["at"])


def newest_age(value: dict[str, Any]) -> float:
    """Seconds since this process last wrote anything down."""
    stamps = list(_reading_times(value))
    if not stamps:
        return float("inf")
    try:
        newest = datetime.fromisoformat(max(stamps))
    except ValueError:
        return float("inf")
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - newest).total_seconds()
