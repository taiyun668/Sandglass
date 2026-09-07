"""Long-lived Sandglass observation process, independent from the desktop UI."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sandglass.accounts import (
    codex_identity_events,
    note_codex_identity,
    note_codex_identity_gap,
    state_file_lock,
)
from sandglass.diagnostics import clear_component_failure, record_component_failure
from sandglass.paths import StateHomeAttestationError, meter_home, require_canonical_state_home
from sandglass import live_snapshot
from sandglass.serve import _start_quota_watch


OBSERVER_MUTEX_NAME = r"Local\Sandglass.Observer.SingleInstance"
OBSERVER_STOP_EVENT_NAME = r"Local\Sandglass.Observer.Stop"
_HEARTBEAT_SECONDS = 15.0
_STALE_AFTER_SECONDS = 90.0
_RECOVERY_WAIT_SECONDS = 0.75
_ERROR_ALREADY_EXISTS = 183
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_EVENT_MODIFY_STATE = 0x0002
_SYNCHRONIZE = 0x00100000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coverage_path() -> Path:
    return meter_home() / "observer-coverage.json"


def _read_coverage() -> dict:
    """Read the coverage ledger. A missing file is empty; an unreadable one is not.

    FileNotFoundError is the first observer. Any other OSError means the file
    is there and we could not read it -- treating that as `{runs: []}` made
    the next `_begin_coverage` write a single new run over the top, wiping
    every retained run. Invalid JSON raises for the same reason: overwriting
    a ledger we could not parse is a wipe, not a save.
    """
    try:
        text = _coverage_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"schema": 1, "runs": []}
    value = json.loads(text)
    if not isinstance(value, dict) or value.get("schema") != 1:
        return {"schema": 1, "runs": []}
    rows = value.get("runs")
    return {"schema": 1, "runs": rows if isinstance(rows, list) else []}


def _write_coverage(value: dict) -> None:
    path = _coverage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        # Sweeping up a temp file must never be the thing that kills the
        # writer. os.replace has already consumed it on the good path, so this
        # only runs for real when the write failed -- and on Windows a scanner
        # holding that fresh file answers unlink with PermissionError, not
        # FileNotFoundError. Both callers are unguarded: _begin_coverage runs
        # before main()'s try, and _heartbeat_locked runs inside its loop, so
        # either one would take the observation process down over a leftover
        # byte. live_snapshot and update already catch OSError here; this was
        # the odd one out.
        try:
            temporary.unlink()
        except OSError:
            pass


def _begin_coverage() -> str:
    stamp = _now()
    # Read and write under one lock: two observers overlap at handover, and
    # without it the one that read first wrote the other's run back out of
    # existence.
    with state_file_lock(_coverage_path()):
        try:
            value = _read_coverage()
            rows = [row for row in value["runs"] if isinstance(row, dict)][-199:]
            if rows and not rows[-1].get("ended_at"):
                rows[-1]["ended_at"] = str(rows[-1].get("last_heartbeat_at") or "")
                rows[-1]["end_reason"] = "observer_interrupted"
            rows.append({
                "started_at": stamp,
                "last_heartbeat_at": stamp,
                "ended_at": "",
                "end_reason": "",
            })
            _write_coverage({"schema": 1, "runs": rows})
        except (OSError, ValueError) as exc:
            # Not raised: this runs before main()'s try, and dying here would
            # take the observer down over a locked ledger. The stamp is still
            # returned so a later heartbeat can put the run back once the
            # file is readable -- on top of the history, not instead of it.
            record_component_failure("observer_coverage_write", exc)
            return stamp
    clear_component_failure("observer_coverage_write")
    return stamp


def _open_run_last_heartbeat() -> str:
    """Return the last heartbeat of the immediately previous run, if any."""
    rows = [
        row for row in _read_coverage().get("runs", []) if isinstance(row, dict)
    ]
    if not rows:
        return ""
    return str(rows[-1].get("last_heartbeat_at") or "")


def _codex_gap_boundary(
    previous_heartbeat: str,
    *,
    upper_bound: str | None = None,
) -> str:
    """Return an appendable boundary after the last known Codex event.

    Coverage files can outlive a clock correction or a test fixture and carry
    a heartbeat from the future.  Such a stamp must not be appended before a
    current observation: the v2 stream is strictly increasing, so doing that
    would make the real observation impossible to record.  The current run's
    start/stop timestamp is the upper bound for a boundary inherited from the
    previous run.
    """
    try:
        boundary = datetime.fromisoformat(
            str(previous_heartbeat or "").replace("Z", "+00:00")
        )
        if (boundary.tzinfo is None
                or boundary.utcoffset() != timezone.utc.utcoffset(boundary)):
            boundary = None
    except (TypeError, ValueError):
        boundary = None
    try:
        limit = datetime.fromisoformat(
            str(upper_bound or "").replace("Z", "+00:00")
        )
        if (limit.tzinfo is None
                or limit.utcoffset() != timezone.utc.utcoffset(limit)):
            limit = None
    except (TypeError, ValueError):
        limit = None
    if limit is not None and (boundary is None or boundary > limit):
        boundary = None
    try:
        events = codex_identity_events()
    except Exception:  # noqa: BLE001 - the observation attempt remains best effort
        events = []
    latest_owner = None
    for event in events:
        try:
            stamp = datetime.fromisoformat(str(event.get("at", "")).replace("Z", "+00:00"))
            if (stamp.tzinfo is None
                    or stamp.utcoffset() != timezone.utc.utcoffset(stamp)):
                continue
        except (TypeError, ValueError):
            continue
        if limit is not None and stamp > limit:
            continue
        if (event.get("kind") == "observed"
                and str(event.get("account_id") or "").strip()):
            if latest_owner is None or stamp > latest_owner:
                latest_owner = stamp
        elif (event.get("kind") == "unassigned"
              and latest_owner is not None and stamp >= latest_owner):
            latest_owner = None
    if latest_owner is None:
        return ""
    # Events at the inclusive upper bound have no representable strictly
    # later timestamp inside this call's interval.  In particular, startup
    # must not append ``upper_bound + 1us`` before the first observation.
    # A graceful stop that needs an explicit closing event must provide a
    # stop bound after the final observation.
    if limit is not None and latest_owner >= limit:
        return ""
    if boundary is None:
        boundary = latest_owner
    if latest_owner >= boundary:
        candidate = latest_owner + timedelta(microseconds=1)
        if limit is not None and candidate > limit:
            return ""
        boundary = candidate
    return boundary.isoformat()


def _run_last_heartbeat(started_at: str) -> str:
    """Return the final heartbeat persisted for one observer run."""
    try:
        rows = [
            row for row in _read_coverage().get("runs", []) if isinstance(row, dict)
        ]
    except (OSError, ValueError):
        return ""
    for row in reversed(rows):
        if row.get("started_at") == started_at:
            return str(row.get("last_heartbeat_at") or "")
    return ""


def _begin_codex_observation(started_at: str, previous_heartbeat: str) -> None:
    """Close the previous Codex interval before opening this observer run."""
    gap_at = _codex_gap_boundary(previous_heartbeat, upper_bound=started_at)
    try:
        # A restart is a coverage boundary even when the account is the
        # same. This keeps the interval unassigned between the old
        # heartbeat and the first successful observation of this run.
        if gap_at:
            note_codex_identity_gap(gap_at, reason="observer_interrupted")
    except Exception:  # noqa: BLE001 - identity bookkeeping cannot stop observing
        pass
    try:
        # The first run starts unassigned. note_codex_identity() records an
        # observed event only after the current official auth is readable; its
        # failure/empty-auth path records a Codex-scoped unassigned event.
        note_codex_identity()
    except Exception:  # noqa: BLE001 - provider failure is isolated in accounts.py
        try:
            note_codex_identity_gap(reason="identity_observation_error")
        except Exception:  # noqa: BLE001 - a broken ledger cannot stop the observer
            pass


def _end_codex_observation(started_at: str) -> None:
    """Close Codex ownership when this observer stops cleanly."""
    heartbeat = _run_last_heartbeat(started_at)
    if not heartbeat:
        return
    stop_bound = _now()
    # Shutdown needs a strictly later representable point when the final
    # observed event has the same microsecond as the stop instant.  Startup
    # deliberately treats that equality as no gap, but a clean stop must leave
    # an explicit unassigned boundary behind for later attribution.
    try:
        stop_at = datetime.fromisoformat(str(stop_bound).replace("Z", "+00:00"))
        if (
            stop_at.tzinfo is not None
            and stop_at.utcoffset() == timezone.utc.utcoffset(stop_at)
        ):
            stop_bound = (stop_at + timedelta(microseconds=1)).isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    gap_at = _codex_gap_boundary(heartbeat, upper_bound=stop_bound)
    if not gap_at:
        return
    try:
        note_codex_identity_gap(gap_at, reason="observer_stopped")
    except Exception:  # noqa: BLE001 - shutdown must still release native handles
        pass


def _stop_quota_watchers(
    stop: threading.Event, watchers: tuple[threading.Thread, ...]
) -> None:
    stop.set()
    for watcher in watchers:
        watcher.join()


def _heartbeat(started_at: str, *, ended: bool = False) -> None:
    with state_file_lock(_coverage_path()):
        _heartbeat_locked(started_at, ended=ended)


def _heartbeat_locked(started_at: str, *, ended: bool = False) -> None:
    try:
        value = _read_coverage()
        rows = [row for row in value["runs"] if isinstance(row, dict)]
        hit = next((row for row in reversed(rows) if row.get("started_at") == started_at), None)
        stamp = _now()
        if hit is None:
            if ended:
                return
            # Two observers overlap for a moment at handover, and both read the
            # ledger, edit it, and write it back with no lock between them. The one
            # that reads before the other appends erases that run on write.
            #
            # Returning here was silent and permanent: every later heartbeat looked
            # for the same row and found it just as gone, so a live observer beat
            # into nothing for as long as it ran while the ledger said it had
            # stopped. The run is still running -- put it back.
            hit = {"started_at": started_at, "last_heartbeat_at": stamp,
                   "ended_at": "", "end_reason": ""}
            rows.append(hit)
        hit["last_heartbeat_at"] = stamp
        if ended:
            hit["ended_at"] = stamp
            hit["end_reason"] = "observer_stopped"
        _write_coverage({"schema": 1, "runs": rows[-200:]})
    except (OSError, ValueError) as exc:
        record_component_failure("observer_coverage_write", exc)
        return
    clear_component_failure("observer_coverage_write")


def observer_status() -> dict[str, object]:
    """Return the persisted coverage boundary without probing provider state."""
    try:
        rows = [
            row for row in _read_coverage().get("runs", []) if isinstance(row, dict)
        ]
    except (OSError, ValueError):
        # Not "not_started": that made a locked ledger look like a stale
        # observer, and ensure_observer_running would kill a healthy one.
        return {
            "state": "unreadable",
            "active": True,
            "started_at": "",
            "last_heartbeat_at": "",
            "heartbeat_age_seconds": None,
            "last_end_reason": "",
            "retained_runs": 0,
        }
    latest = rows[-1] if rows else {}
    heartbeat = latest.get("last_heartbeat_at")
    try:
        age = max(
            0.0,
            (datetime.now(timezone.utc) - datetime.fromisoformat(str(heartbeat))).total_seconds(),
        )
    except (TypeError, ValueError):
        age = None
    active = (
        bool(latest)
        and not latest.get("ended_at")
        and age is not None
        and age <= _STALE_AFTER_SECONDS
    )
    return {
        "state": "observing" if active else ("stopped" if latest else "not_started"),
        "active": active,
        "started_at": str(latest.get("started_at") or ""),
        "last_heartbeat_at": str(heartbeat or ""),
        "heartbeat_age_seconds": age,
        "last_end_reason": str(latest.get("end_reason") or ""),
        "retained_runs": len(rows),
    }


def _kernel32():
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateEventW.restype = wintypes.HANDLE
    kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenEventW.restype = wintypes.HANDLE
    kernel.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.SetEvent.restype = wintypes.BOOL
    kernel.SetEvent.argtypes = [wintypes.HANDLE]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel


def observer_process_exists() -> bool:
    """Whether an observer holds its single-instance mutex, asked of Windows.

    The coverage ledger cannot answer this. Its heartbeat is a file written
    every fifteen seconds and considered fresh for ninety, so for a minute and
    a half after an observer dies the ledger still describes a living one --
    and a desktop starting inside that window concludes there is nothing to
    start, then never asks again for the life of the process. The mutex is the
    process: if nobody holds it, nobody is observing, whatever the file says.
    """
    if os.name != "nt":
        return False
    kernel = _kernel32()
    kernel.OpenMutexW.restype = wintypes.HANDLE
    kernel.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    handle = kernel.OpenMutexW(_SYNCHRONIZE, False, OBSERVER_MUTEX_NAME)
    if not handle:
        return False
    kernel.CloseHandle(handle)
    return True


def request_observer_stop() -> bool:
    """Signal the existing Observer only after an explicit user action."""
    if os.name != "nt":
        return False
    kernel = _kernel32()
    handle = kernel.OpenEventW(_EVENT_MODIFY_STATE, False, OBSERVER_STOP_EVENT_NAME)
    if not handle:
        return False
    try:
        return bool(kernel.SetEvent(handle))
    finally:
        kernel.CloseHandle(handle)


_SUPERVISE_SECONDS = 60.0
_SUPERVISE_STOP = threading.Event()


def stop_supervising_observer() -> None:
    """Stop respawning: the user asked for observation to end, not to restart."""
    _SUPERVISE_STOP.set()


def supervise_observer(interval: float = _SUPERVISE_SECONDS) -> None:
    """Keep one observer alive for as long as this process runs.

    ensure_observer_running() was called once, at startup, and never again, so
    an observer that died mid-session stayed dead until the whole app was
    restarted. This machine sat two hours and fifty-one minutes that way with
    the panel up and nothing recording, and the coverage ledger had already
    written the run down as observer_interrupted -- it knew, and nobody was
    asking it.

    It is also the only thing in a position to notice that a launch failed.
    The observer is a detached child with its handles on DEVNULL, so a process
    that application control kills on sight, or that dies before claiming its
    mutex, leaves no trace anywhere -- the launcher saw Popen return and
    concluded it was done. One round later the mutex says otherwise, and that
    is worth writing down rather than silently trying again forever.
    """
    def loop() -> None:
        launched = False
        while not _SUPERVISE_STOP.wait(interval):
            try:
                running = observer_process_exists()
                if launched and not running:
                    record_component_failure("observer", TimeoutError(
                        "the observer that was started never claimed its mutex"))
                elif running:
                    clear_component_failure("observer")
                launched = ensure_observer_running()
            except Exception:  # noqa: BLE001 - a watchdog that dies is worse than a late one
                continue

    threading.Thread(target=loop, name="sandglass-observer-watchdog", daemon=True).start()


def _observer_args() -> list[str]:
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return [str(executable), "--observer"]
    quiet = executable.with_name("pythonw.exe")
    if quiet.exists():
        executable = quiet
    launcher = Path(__file__).resolve().parent.parent / "sandglass-desktop.pyw"
    if launcher.is_file():
        return [str(executable), str(launcher), "--observer"]
    return [str(executable), "-m", "sandglass.observer"]


def stop_deadline_seconds() -> float:
    """How long a requested stop can reasonably take, derived from the code.

    The observer answers its stop event promptly and then joins the quota
    watchers, and one of those threads may be inside a vendor request that runs
    to HTTP_TIMEOUT_SECONDS, once per provider whose interval came due. So the
    exit latency is bounded by the providers and that timeout, not by anything
    the caller picked.

    This number decides only how long to wait before reporting "I could not
    confirm it stopped". Being wrong in either direction is safe: the answer
    comes from the mutex, never from the clock. That is the whole difference
    from the installer's old Sleep 2000, which concluded rather than reported.
    """
    from sandglass.quota import CACHE_TTL_SECONDS_BY_PROVIDER, HTTP_TIMEOUT_SECONDS

    providers = max(1, len(CACHE_TTL_SECONDS_BY_PROVIDER))
    return HTTP_TIMEOUT_SECONDS * providers + _HEARTBEAT_SECONDS


def stop_and_wait() -> bool:
    """Ask the observer to stop and report whether it actually let go.

    `--stop` used to signal and return 0 unconditionally, discarding even
    request_observer_stop()'s own result, so the installer that calls it before
    replacing the program files learned nothing. It compensated with a fixed
    two-second sleep and then queried the *desktop* mutex -- while its own
    comment named the observer as the process holding those files. Neither the
    sleep nor that query can see the observer still mapping the DLLs about to be
    overwritten.
    """
    stop_supervising_observer()
    request_observer_stop()
    return _wait_for_mutex_release(stop_deadline_seconds())


def _wait_for_mutex_release(timeout: float = _RECOVERY_WAIT_SECONDS) -> bool:
    """Wait until nobody holds the observer mutex. False if the holder stayed.

    Replacing a stale observer used to sleep three quarters of a second after
    asking it to stop, and then start the replacement whatever had happened.
    A replacement that starts while the mutex is still held does not wait for
    it and does not complain: main() sees ERROR_ALREADY_EXISTS and returns 0,
    so the process is gone without a word and nothing observes until the
    watchdog comes round a minute later. The guess was wrong in the other
    direction too -- an observer that exited in thirty milliseconds still cost
    the full three quarters of a second of startup.

    The mutex is the question, so the mutex is what this waits on. The timeout
    only bounds how long the caller is blocked, and saying so is the point:
    a caller told "no" can leave it to the next round instead of launching a
    process that will exit on sight.
    """
    deadline = time.monotonic() + timeout
    while observer_process_exists():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


def ensure_observer_running() -> bool:
    """Start one detached observer if nothing is observing.

    Returns whether a process was started -- not whether it came up, which
    only the mutex can answer and only once the child has had time to claim it.
    The watchdog asks that on its next round.
    """
    if os.name != "nt":
        return False
    if observer_process_exists():
        # Something holds the mutex. Only replace it if its heartbeat says it
        # has stopped observing; a healthy observer is left alone.
        if observer_status().get("active") is True:
            return False
        if not request_observer_stop():
            return False
        if not _wait_for_mutex_release():
            return False
    creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        _observer_args(),
        cwd=str(Path(__file__).resolve().parent.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )
    return True


def main() -> int:
    if os.name != "nt":
        return 2
    try:
        require_canonical_state_home()
    except StateHomeAttestationError:
        return 2
    kernel = _kernel32()
    mutex = kernel.CreateMutexW(None, False, OBSERVER_MUTEX_NAME)
    if not mutex:
        raise ctypes.WinError(kernel.GetLastError())
    if kernel.GetLastError() == _ERROR_ALREADY_EXISTS:
        kernel.CloseHandle(mutex)
        return 0
    stop_handle = kernel.CreateEventW(None, True, False, OBSERVER_STOP_EVENT_NAME)
    if not stop_handle:
        kernel.CloseHandle(mutex)
        raise ctypes.WinError(kernel.GetLastError())

    stop = threading.Event()
    live_snapshot.set_role("observer")
    try:
        previous_heartbeat = _open_run_last_heartbeat()
    except (OSError, ValueError) as exc:
        # An unreadable ledger is not a first run. Inventing a gap from
        # "" would close Codex ownership against a history we could not
        # see. Quota watching still starts; a later heartbeat puts the
        # coverage run back once the file is readable.
        record_component_failure("observer_coverage_write", exc)
        previous_heartbeat = None
    started_at = _begin_coverage()
    if previous_heartbeat is not None:
        _begin_codex_observation(started_at, previous_heartbeat)
    watchers = _start_quota_watch(stop, True)
    try:
        while True:
            result = int(kernel.WaitForSingleObject(stop_handle, int(_HEARTBEAT_SECONDS * 1000)))
            if result == _WAIT_OBJECT_0:
                break
            if result != _WAIT_TIMEOUT:
                raise ctypes.WinError(kernel.GetLastError())
            _heartbeat(started_at)
    finally:
        _stop_quota_watchers(stop, watchers)
        try:
            _heartbeat(started_at, ended=True)
        finally:
            _end_codex_observation(started_at)
        kernel.CloseHandle(stop_handle)
        kernel.CloseHandle(mutex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
