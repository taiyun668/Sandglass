from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sandglass.paths import meter_home


_LOCK = threading.RLock()
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
        "runtime": runtime_provenance(),
    }


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
