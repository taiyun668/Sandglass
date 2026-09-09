"""Whether a newer Sandglass has been published, and installing it if so.

Two things this deliberately will not do. It never downloads during a check, so
opening the panel costs one small request and nothing else. And it never runs an
installer it cannot vouch for: the release has to publish a checksum file beside
the installer, the download has to match it, and that manifest has to carry the
Owner's detached signature. The signed manifest is the release identity used by
the public unsigned installer route.

Nothing here can offer anything until a release exists to find. That is the
correct dark state, not a failure.
"""

from __future__ import annotations

import atexit
import errno
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sandglass import __version__
from sandglass.diagnostics import clear_component_failure, record_component_failure
from sandglass.models import parse_ts
from sandglass.paths import meter_home

FEED_URL = "https://api.github.com/repos/taiyun668/Sandglass/releases/latest"
ALLOWED_HOSTS = ("api.github.com", "github.com", "objects.githubusercontent.com")
CHECK_TTL_SECONDS = 6 * 3600
MAX_FEED_BYTES = 1 << 20
MAX_INSTALLER_BYTES = 200 << 20
# These are the only installer families the release tooling emits.  Keep the
# names explicit: a portable zip is never an update executable, and a generic
# ``endswith('-setup.exe')`` check would silently accept unrelated assets.
INSTALLER_NAME_RE = re.compile(
    r"^Sandglass-(?P<version>[^/\\]+)-windows-x64-"
    r"unsigned-setup\.exe$",
    re.IGNORECASE,
)
CHECKSUMS_NAME = "SHA256SUMS.windows"
CHECKSUMS_SIGNATURE_NAME = "SHA256SUMS.windows.sig"

# P-256 X||Y, hex, from the Owner's release key. The private half is not in
# this repository and never will be.
RELEASE_PUBLIC_KEY = "21D35013DCA960BAE23B6A0C7CF08A79C888A45C0375F67A01D4FABA8E1A4B1E853FAA6A9C09D380272A2980AF44914CE109E07446A734BD7F3BC2A14D2D7EBA"

_LOCK = threading.RLock()
_UPDATE_GATE_NAME = "update-apply.lock"
_UPDATE_GATE: tuple[Path, Any] | None = None


class UpdateBusyError(RuntimeError):
    """Another Sandglass process is already handing an update to its installer."""


class UpdateGateError(RuntimeError):
    """The OS could not initialize or support the update gate."""


class UpdateStateLockError(RuntimeError):
    """The short-lived cross-process state transaction lock failed."""


def _gate_path() -> Path:
    return meter_home() / _UPDATE_GATE_NAME


def _is_lock_contention(exc: OSError) -> bool:
    """Classify only an OS lock refusal as contention, not setup failures."""
    return getattr(exc, "errno", None) in {
        errno.EACCES, errno.EAGAIN, errno.EDEADLK,
    }


def _lock_file(path: Path, *, blocking: bool, error_type: type[RuntimeError],
               purpose: str):
    """Open and lock one byte, keeping the handle alive for the context."""
    handle = None
    lock_started = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        if path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        lock_started = True
        if os.name == "nt":
            import msvcrt

            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            msvcrt.locking(handle.fileno(), mode, 1)
        else:
            import fcntl

            mode = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
            fcntl.flock(handle.fileno(), mode)
    except (ImportError, OSError) as exc:
        if handle is not None:
            handle.close()
        if lock_started and not blocking and _is_lock_contention(exc):
            raise UpdateBusyError("an update is already in progress") from exc
        raise error_type(f"could not initialize the Sandglass {purpose} lock") from exc
    return handle


def _unlock_file(handle: Any) -> None:
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass
    finally:
        handle.close()


def _acquire_update_gate() -> None:
    """Take a Sandglass-owned cross-process update gate.

    The OS byte-range lock is retained after Popen succeeds until this process
    exits.  The OS releases it if the owner crashes, without PID probing
    (``os.kill(pid, 0)`` is destructive on Windows) or stale-owner races.
    """
    global _UPDATE_GATE
    with _LOCK:
        if _UPDATE_GATE is not None:
            raise UpdateBusyError("an update is already in progress")
        path = _gate_path()
        handle = _lock_file(
            path, blocking=False, error_type=UpdateGateError,
            purpose="update apply",
        )
        _UPDATE_GATE = (path, handle)


def _release_update_gate() -> None:
    global _UPDATE_GATE
    with _LOCK:
        gate, _UPDATE_GATE = _UPDATE_GATE, None
        if gate is None:
            return
        _, handle = gate
        _unlock_file(handle)


@contextmanager
def _state_transaction():
    """Serialize one update-check.json read/modify/write transaction.

    This lock is deliberately separate from the long-lived apply gate. Network
    requests must happen outside it; callers reacquire it and reread state
    immediately before writing their fields.
    """
    handle = _lock_file(
        meter_home() / "update-state.lock", blocking=True,
        error_type=UpdateStateLockError, purpose="update state",
    )
    try:
        yield
    finally:
        _unlock_file(handle)


atexit.register(_release_update_gate)


def state_path() -> Path:
    return meter_home() / "update-check.json"


def _version_tuple(text: str) -> tuple:
    parts = re.findall(r"\d+", str(text or ""))
    return tuple(int(part) for part in parts) if parts else ()


def is_newer(candidate: str, current: str = __version__) -> bool:
    """Compare as numbers. "0.10.0" is newer than "0.9.0"; as strings it is not."""
    left, right = _version_tuple(candidate), _version_tuple(current)
    if not left or not right:
        return False
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def _https(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("refusing a release URL outside the published host set")
    return url


def _get(url: str, limit: int, timeout: float = 20.0) -> bytes:
    request = urllib.request.Request(_https(url), headers={
        "User-Agent": "sandglass/" + __version__,
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("release response is larger than expected")
    return payload


def checksum_for(text: str, name: str) -> str:
    found = ""
    matches = 0
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            matches += 1
            digest = parts[0].strip().lower()
            if re.fullmatch(r"[0-9a-f]{64}", digest):
                found = digest
    # A duplicate filename is ambiguous: accepting the first entry would make
    # the manifest's meaning depend on ordering, so reject the whole asset.
    return found if matches == 1 else ""


def _installer_name(name: str, version: str) -> bool:
    """Whether *name* is an exact, version-bound installer asset."""
    match = INSTALLER_NAME_RE.fullmatch(str(name or ""))
    return bool(match and match.group("version") == version)


def _manifest_version(raw_sums: bytes) -> str:
    """Read the signed manifest's mandatory version metadata line."""
    try:
        text = raw_sums.decode("ascii")
    except UnicodeDecodeError:
        return ""
    versions = re.findall(r"^#\s*Sandglass-Version:\s*([^\s#]+)\s*$", text, re.MULTILINE)
    return versions[0] if len(versions) == 1 else ""


def offer_from(release: dict[str, Any]) -> dict[str, Any]:
    """Turn one release into something installable, or {} if it is not."""
    version = str(release.get("tag_name") or "").lstrip("vV")
    if not is_newer(version):
        return {}
    assets = {}
    for asset in release.get("assets") or []:
        if isinstance(asset, dict):
            assets[str(asset.get("name") or "")] = str(asset.get("browser_download_url") or "")
    # The public channel has exactly one Windows installer shape. A second
    # matching installer is ambiguous rather than a priority decision.
    candidates = [name for name in assets if _installer_name(name, version)]
    installer = candidates[0] if len(candidates) == 1 else ""
    if not installer or CHECKSUMS_NAME not in assets:
        # Without a published checksum there is nothing to verify against, so
        # there is nothing to offer. An update that cannot be checked is not an
        # update, it is a download.
        return {}
    try:
        raw_sums = _get(assets[CHECKSUMS_NAME], MAX_FEED_BYTES)
        sums = raw_sums.decode("utf-8", "replace")
        digest = checksum_for(sums, installer)
        url = _https(assets[installer])
        manifest_signed = _manifest_signature_ok(raw_sums, assets, version, installer)
    except (urllib.error.URLError, OSError, ValueError):
        return {}
    if not digest:
        return {}
    if not manifest_signed:
        # Automatic updates always require the Owner's detached release
        # signature. A checksum without its authorizing signature is only an
        # untrusted list of hashes, not a Sandglass update.
        return {}
    return {
        "version": version,
        "asset": installer,
        "url": url,
        "sha256": digest,
        "manifest_signed": manifest_signed,
        "published_at": str(release.get("published_at") or ""),
        "notes_url": str(release.get("html_url") or ""),
        # GitHub's release body is already plain text. Keep it as text rather
        # than attempting to interpret Markdown/HTML in the dashboard.
        "notes": release.get("body") if isinstance(release.get("body"), str) else "",
    }


def _manifest_signature_ok(
    raw_sums: bytes, assets: dict[str, str], version: str = "", installer: str = ""
) -> bool:
    """Whether the checksum manifest carries this project's own signature.

    The manifest names the installer's digest, so a signature over the manifest
    covers the installer without a second file to fetch per artifact.
    """
    from sandglass.release_signature import verify_release_signature

    if not RELEASE_PUBLIC_KEY or CHECKSUMS_SIGNATURE_NAME not in assets:
        return False
    # A signature over an old checksum-only manifest is not a signature over
    # this release identity.  Once a key exists, the signed version metadata
    # and the installer filename must both agree with the tag.
    if not version or not installer or _manifest_version(raw_sums) != version:
        return False
    if not _installer_name(installer, version):
        return False
    if not checksum_for(raw_sums.decode("ascii", "ignore"), installer):
        return False
    try:
        signature = _get(assets[CHECKSUMS_SIGNATURE_NAME], MAX_FEED_BYTES)
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return verify_release_signature(raw_sums, bytes.fromhex(
        signature.decode("ascii", "ignore").strip()
    ) if _is_hex(signature) else signature, RELEASE_PUBLIC_KEY)


def _is_hex(payload: bytes) -> bool:
    text = payload.decode("ascii", "ignore").strip()
    return bool(text) and len(text) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in text)


def _read_state() -> dict[str, Any]:
    """Read update-check.json. A missing file is empty; an unreadable one is not.

    FileNotFoundError is the first check. Any other OSError means the file is
    there and we could not read it -- treating that as `{}` made a failed
    fetch write `{offer: {}, error: ...}` over a cached offer.
    """
    try:
        text = state_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    value = json.loads(text)
    return value if isinstance(value, dict) else {}


def _write_state(value: dict[str, Any]) -> None:
    path = state_path()
    temporary = path.with_name("." + path.name + "." + str(os.getpid()) + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        record_component_failure("update_state_write", exc)
        raise
    clear_component_failure("update_state_write")


def _seconds_since_check(stored: dict[str, Any]) -> float | None:
    """How long ago the last check ran, or None when that cannot be trusted.

    This used to compare a persisted time.monotonic() against this process's
    own. That clock counts from boot on Windows, so after a restart the stored
    value is the larger of the two, the elapsed time comes out negative, and
    every negative number is younger than any TTL -- checking then stopped
    happening until the machine had been up longer than it was when the value
    was written, which for a long-running machine is days.

    A wall clock survives the restart. A clock that has moved backwards, or a
    value that cannot be read, means check again rather than assume freshness:
    a check costs one small request and never downloads.
    """
    checked = parse_ts(stored.get("checked_at"))
    if checked is None:
        return None
    elapsed = (datetime.now(timezone.utc) - checked).total_seconds()
    return elapsed if elapsed >= 0 else None


def available_update(force: bool = False) -> dict[str, Any]:
    """The offer to show, or {} for nothing. Cached, and never downloads."""
    try:
        with _LOCK, _state_transaction():
            stored = _read_state()
            elapsed = _seconds_since_check(stored)
            if not force and elapsed is not None and elapsed < CHECK_TTL_SECONDS:
                cached = stored.get("offer")
                # Freshness is not enough. A 0.1.1 process writes a 0.1.3
                # offer; after that install, this 0.1.3 process still sees a
                # cache inside the TTL whose offer.version is itself. Serving
                # it again is the stale update badge. Compare against this
                # module's __version__ on every cached return.
                if not isinstance(cached, dict):
                    return {}
                version = cached.get("version")
                if isinstance(version, str) and is_newer(version, __version__):
                    return dict(cached)
                if cached:
                    next_state = dict(stored)
                    next_state["offer"] = {}
                    _write_state(next_state)
                return {}
    except (OSError, ValueError, UpdateStateLockError) as exc:
        record_component_failure("update_state_write", exc)
        if force:
            raise UpdateStateLockError(
                "the update state could not be read or locked"
            ) from exc
        return {}

    # The network is intentionally outside the state transaction. Another
    # process may update pending_announcement while this request is in flight.
    offer: dict[str, Any] = {}
    error = ""
    try:
        release = json.loads(_get(FEED_URL, MAX_FEED_BYTES).decode("utf-8", "replace"))
        if isinstance(release, dict):
            offer = offer_from(release)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        error = type(exc).__name__

    try:
        with _LOCK, _state_transaction():
            # Re-read after the network request, then merge only this check's
            # fields. In particular, preserve an announcement written by an
            # update handoff while the request was running.
            latest = _read_state()
            latest.update({
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "current_version": __version__,
                "offer": offer,
                "error": error,
            })
            _write_state(latest)
    except (OSError, ValueError, UpdateStateLockError) as exc:
        record_component_failure("update_state_write", exc)
        if force:
            raise UpdateStateLockError(
                "the update state could not be committed"
            ) from exc
        return offer
    return offer


def download_verified(offer: dict[str, Any], into: Path) -> Path:
    """Fetch the installer, and refuse it unless it is exactly what was promised."""
    url = _https(str(offer.get("url") or ""))
    expected = str(offer.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("the release did not publish a usable checksum")
    into.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    written = 0
    request = urllib.request.Request(url, headers={"User-Agent": "sandglass/" + __version__})
    with urllib.request.urlopen(request, timeout=120) as response, into.open("wb") as handle:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_INSTALLER_BYTES:
                into.unlink(missing_ok=True)
                raise ValueError("installer is larger than any release we publish")
            digest.update(chunk)
            handle.write(chunk)
    if digest.hexdigest() != expected:
        into.unlink(missing_ok=True)
        raise ValueError("installer does not match the published checksum")
    # The detached signature over SHA256SUMS.windows is the only release
    # identity proof accepted here. `offer_from` verifies that signature and
    # binds it to this version and installer before setting this flag. Windows
    # Publisher trust is intentionally not a second path: unsigned preview
    # packages must remain installable without a certificate.
    if offer.get("manifest_signed") is not True:
        into.unlink(missing_ok=True)
        raise ValueError(
            "installer lacks a release manifest signed with this project's key"
        )
    return into


def apply_update(offer: dict[str, Any]) -> dict[str, Any]:
    """Download, verify, then hand over to the installer and leave.

    The installer refuses to write over a running copy, so this does not wait
    for it: it starts the installer detached and returns, and the caller quits.
    The installer relaunches Sandglass when it is done. The verified release
    metadata is retained until that new version has shown its announcement.
    """
    version = str(offer.get("version") or "")
    if not version:
        raise ValueError("the release did not publish a version")
    _acquire_update_gate()
    pending_record: dict[str, Any] | None = None
    previous_pending: Any = None
    previous_pending_present = False
    try:
        name = str(offer.get("asset") or "sandglass-setup.exe")
        staged = Path(tempfile.gettempdir()) / "sandglass-update" / name
        staged = download_verified(offer, staged)
        # The installer creates a private, one-shot named event.  The random
        # token is the only handoff capability; no command-line value is ever
        # interpreted as a path by either side.
        ready_token = secrets.token_hex(16)
        with _LOCK, _state_transaction():
            stored = _read_state()
            previous_pending_present = "pending_announcement" in stored
            previous_pending = stored.get("pending_announcement")
            next_state = dict(stored)
            pending_record = {
                "version": version,
                "notes": offer.get("notes") if isinstance(offer.get("notes"), str) else "",
                "published_at": str(offer.get("published_at") or ""),
                "notes_url": str(offer.get("notes_url") or ""),
            }
            next_state["pending_announcement"] = pending_record
            _write_state(next_state)
        process = subprocess.Popen(
            [
                str(staged),
                "/UPDATE",
                f"/PARENTPID={os.getpid()}",
                f"/RESTARTEXE={Path(sys.executable).resolve()}",
                f"/UPDATE_TOKEN={ready_token}",
            ],
            creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | NEW_PROCESS_GROUP
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        # Keep the gate until process exit.  The installer is detached and the
        # caller quits immediately after this returns; a second click during
        # that handoff must not launch another installer.
        return {
            "ok": True, "version": version, "staged": str(staged),
            # Kept in-process only. The desktop strips private keys before
            # serializing the response; they let it cancel a handoff when its
            # own window cannot begin shutting down.
            "_process": process,
            "_pending_record": pending_record,
            "_previous_pending": previous_pending,
            "_previous_pending_present": previous_pending_present,
        }
    except BaseException:
        # Failed verification, state persistence, or Popen must allow a later
        # attempt. If Popen failed after staging the announcement, undo only
        # that field and preserve any concurrently refreshed offer metadata.
        if pending_record is not None:
            try:
                with _LOCK, _state_transaction():
                    current = _read_state()
                    if current.get("pending_announcement") == pending_record:
                        restored = dict(current)
                        if previous_pending_present:
                            restored["pending_announcement"] = previous_pending
                        else:
                            restored.pop("pending_announcement", None)
                        _write_state(restored)
            except (OSError, ValueError, UpdateStateLockError) as exc:
                record_component_failure("update_state_write", exc)
        # Success deliberately leaves the gate owned until atexit.
        _release_update_gate()
        raise


def cancel_update_handoff(result: dict[str, Any]) -> bool:
    """Cancel an installer that is still waiting for this desktop to exit.

    This is used only when the desktop could not even begin shutting down. The
    installer has not crossed its parent-exit gate, so confirming its process
    exit is proof that no installation write occurred.
    """
    process = result.get("_process")
    try:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        if process is not None and process.poll() is None:
            return False
    except (OSError, subprocess.SubprocessError, AttributeError):
        return False

    pending_record = result.get("_pending_record")
    if isinstance(pending_record, dict):
        try:
            with _LOCK, _state_transaction():
                current = _read_state()
                if current.get("pending_announcement") == pending_record:
                    restored = dict(current)
                    if result.get("_previous_pending_present"):
                        restored["pending_announcement"] = result.get("_previous_pending")
                    else:
                        restored.pop("pending_announcement", None)
                    _write_state(restored)
        except (OSError, ValueError, UpdateStateLockError) as exc:
            record_component_failure("update_state_write", exc)
            return False
    _release_update_gate()
    return True


def public_update_result(result: dict[str, Any]) -> dict[str, Any]:
    """Remove in-process handoff handles before returning JSON to the panel."""
    return {key: value for key, value in result.items() if not key.startswith("_")}


def update_announcement() -> dict[str, Any]:
    """Return the current version's one-time release announcement, if any."""
    try:
        with _LOCK, _state_transaction():
            stored = _read_state()
    except (OSError, ValueError, UpdateStateLockError) as exc:
        record_component_failure("update_state_write", exc)
        return {}
    pending = stored.get("pending_announcement")
    if not isinstance(pending, dict) or pending.get("version") != __version__:
        return {}
    return dict(pending)


def dismiss_update_announcement(version: str) -> dict[str, Any]:
    """Dismiss only the named version's announcement; never a newer one."""
    version = str(version or "")
    try:
        with _LOCK, _state_transaction():
            stored = _read_state()
            pending = stored.get("pending_announcement")
            if not isinstance(pending, dict) or pending.get("version") != version:
                return {"ok": False, "dismissed": False}
            next_state = dict(stored)
            next_state.pop("pending_announcement", None)
            _write_state(next_state)
    except (OSError, ValueError, UpdateStateLockError) as exc:
        record_component_failure("update_state_write", exc)
        return {"ok": False, "dismissed": False}
    return {"ok": True, "dismissed": True, "version": version}
