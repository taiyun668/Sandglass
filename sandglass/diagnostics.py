from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sandglass.paths import meter_home


_LOCK = threading.RLock()
_ATTRIBUTION_SELF_CHECK_HEARTBEAT_LOCK = threading.Lock()
_ATTRIBUTION_SELF_CHECK_HEARTBEAT = {
    "last_ok_at": None,
    "check_count": 0,
    "state": "pending",
    "reason": "",
}
_BLOCKED_TEXT = (
    "application control policy",
    "blocked by group policy",
    "blocked by your administrator",
    "access disabled by policy",
)


def diagnostics_path() -> Path:
    return meter_home() / "runtime-diagnostics.json"


def _error_chain(error: BaseException):
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def classify_component_error(error: BaseException) -> tuple[str, int | None]:
    """Classify launch/load failures without retaining provider data or raw paths."""

    codes: list[int] = []
    messages: list[str] = []
    for item in _error_chain(error):
        messages.append(str(item).lower())
        for name in ("winerror", "errno", "hresult"):
            value = getattr(item, name, None)
            if isinstance(value, int):
                codes.append(value)
    normalized = {code & 0xFFFF for code in codes}
    if 1260 in normalized or any(marker in " ".join(messages) for marker in _BLOCKED_TEXT):
        return "blocked_by_application_control", next((code for code in codes if code & 0xFFFF == 1260), None)
    if 5 in normalized or isinstance(error, PermissionError):
        return "access_denied", next((code for code in codes if code & 0xFFFF == 5), None)
    if normalized.intersection({2, 126}) or isinstance(error, FileNotFoundError):
        return "missing_component", next((code for code in codes if code & 0xFFFF in {2, 126}), None)
    if isinstance(error, TimeoutError):
        return "startup_timeout", next(iter(codes), None)
    return "component_failed", next(iter(codes), None)


@contextmanager
def _exclusive():
    """Hold this file across a read/modify/write, against the other processes.

    Desktop, observer and the panel all record into one file, and a recorded
    failure is a read, an edit and a replace. The in-process lock below covers
    none of that: measured here, two processes recording 60 components each
    left 58 of the 120 in the file -- whoever read first wrote the other's
    failures back out of existence. The observer's coverage ledger had the same
    defect and was fixed the same way.

    Only the writers take it. Reading stays lock-free, the way every other
    ledger in this package is read, so that asking for diagnostics never
    creates state; a reader that collides with a replace still reads empty and
    is right again on its next poll, while an unserialized *writer* erased what
    was already recorded and stayed wrong until the next failure.

    It takes its own lock file rather than the shared state one, because a
    failure is usually recorded from an except handler, sometimes one reached
    while another state file is being written. The lock never gives up, so a
    report that queued behind the write it is reporting on would hang.
    """
    from sandglass.accounts import state_file_lock

    with state_file_lock(diagnostics_path(), lock_name=".runtime-diagnostics.lock"):
        yield


def _read_payload() -> tuple[str, dict[str, Any]]:
    """Read runtime-diagnostics.json. A missing file is empty; an unreadable one is not.

    FileNotFoundError is the first record. Any other OSError means the file is
    there and we could not read it -- treating that as empty made the next
    record write only the new component over the top, erasing every other
    failure that was already recorded.
    """
    try:
        text = diagnostics_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return "", {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return "", {}
    if not isinstance(value, dict):
        return "", {}
    components = value.get("components")
    return (
        str(value.get("updated_at") or ""),
        dict(components) if isinstance(components, dict) else {},
    )


def runtime_diagnostics() -> dict[str, Any]:
    from sandglass.runtime_provenance import runtime_provenance

    try:
        updated_at, components = _read_payload()
    except OSError:
        # A locked file is not "nothing recorded"; this call must still
        # answer. The wipe is the write path's job, closed below.
        updated_at, components = "", {}
    return {
        "updated_at": updated_at,
        "components": components,
        "attribution_self_check": attribution_self_check_heartbeat(),
        "runtime": runtime_provenance(),
    }


def attribution_self_check_events_path() -> Path:
    return meter_home() / "attribution-self-check-events.jsonl"


def _append_attribution_self_check_event(event: dict[str, Any]) -> None:
    """Append one durable transition line, serialized across processes.

    This is a distinct log from runtime-diagnostics.json on purpose: that file
    is overwritten on every clear_component_failure, so a drift that
    self-heals before anyone looks leaves nothing behind. This file is never
    truncated, rotated, or capped -- a line, once appended, stays.
    """
    from sandglass.accounts import state_file_lock

    path = attribution_self_check_events_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with state_file_lock(path, lock_name=".attribution-self-check-events.lock"):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
    except OSError as exc:
        # Not raised: this is bookkeeping beside a self-check result, and the
        # caller (a watcher loop, or an except handler already reporting its
        # own failure) must not be broken by a failure to log about it.
        record_component_failure("attribution_self_check_event_write", exc)


def record_attribution_self_check_result(state: str, reason: str = "") -> None:
    """Record one attribution self-check transition.

    The heartbeat's last_ok_at/check_count only ever tracked liveness in
    process memory, and runtime-diagnostics.json only ever holds the current
    component state -- the next success clears it. Together they cannot answer
    "did this drift and then recover" once it has recovered. This appends one
    line to attribution-self-check-events.jsonl exactly when (state, reason)
    changes from what this process last recorded, so a self-healing episode
    survives on disk. Unchanged results append nothing.
    """
    with _ATTRIBUTION_SELF_CHECK_HEARTBEAT_LOCK:
        previous = (
            _ATTRIBUTION_SELF_CHECK_HEARTBEAT["state"],
            _ATTRIBUTION_SELF_CHECK_HEARTBEAT["reason"],
        )
        if state == "ok":
            _ATTRIBUTION_SELF_CHECK_HEARTBEAT["last_ok_at"] = datetime.now(
                timezone.utc
            ).isoformat()
            _ATTRIBUTION_SELF_CHECK_HEARTBEAT["check_count"] += 1
        _ATTRIBUTION_SELF_CHECK_HEARTBEAT["state"] = state
        _ATTRIBUTION_SELF_CHECK_HEARTBEAT["reason"] = reason
        check_count = _ATTRIBUTION_SELF_CHECK_HEARTBEAT["check_count"]
        changed = previous != (state, reason)
    if not changed:
        return
    event = {
        "at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "state": state,
        "reason": reason,
        "check_count": check_count,
    }
    _append_attribution_self_check_event(event)


def record_attribution_self_check_success() -> None:
    """Record one successful attribution self-check. Thin wrapper kept for callers."""
    record_attribution_self_check_result("ok")


def attribution_self_check_heartbeat() -> dict[str, Any]:
    """Return a snapshot of the in-process attribution self-check heartbeat."""
    with _ATTRIBUTION_SELF_CHECK_HEARTBEAT_LOCK:
        return dict(_ATTRIBUTION_SELF_CHECK_HEARTBEAT)


def _write(components: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    payload = {"updated_at": now, "components": components}
    path = diagnostics_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        return


def record_component_failure(component: str, error: BaseException) -> dict[str, Any]:
    status, code = classify_component_error(error)
    issue = {
        "component": str(component),
        "status": status,
        "code": code,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    with _LOCK, _exclusive():
        try:
            _, components = _read_payload()
        except OSError:
            # The file is there and we could not read it. Writing only this
            # component would wipe the others. Callers are except handlers
            # and must not raise; the original bytes stay.
            return issue
        components[str(component)] = issue
        _write(components)
    return issue


def clear_component_failure(component: str) -> None:
    # Healthy is the common case, and clearing nothing is not worth a lock
    # file in the state directory. A failure recorded between this read and
    # the locked one below is a failure that just happened, and not clearing
    # it is the right answer anyway; the locked re-read decides.
    try:
        present = str(component) in _read_payload()[1]
    except OSError:
        return
    if not present:
        return
    with _LOCK, _exclusive():
        try:
            _, components = _read_payload()
        except OSError:
            return
        if str(component) not in components:
            return
        components.pop(str(component), None)
        _write(components)
