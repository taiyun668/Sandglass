"""Whether a newer Sandglass has been published, and installing it if so.

Two things this deliberately will not do. It never downloads during a check, so
opening the panel costs one small request and nothing else. And it never runs an
installer it cannot vouch for: the release has to publish a checksum file beside
the installer, the download has to match it, and the file has to carry either a
trusted Windows signature or the project's signed release manifest. The latter
is the release identity used by the public unsigned installer route.

Nothing here can offer anything until a release exists to find. That is the
correct dark state, not a failure.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from ctypes import wintypes
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
INSTALLER_SUFFIX = "-windows-x64-setup.exe"
CHECKSUMS_NAME = "SHA256SUMS.windows"
CHECKSUMS_SIGNATURE_NAME = "SHA256SUMS.windows.sig"

# P-256 X||Y, hex, from the Owner's release key. The private half is not in
# this repository and never will be.
RELEASE_PUBLIC_KEY = "21D35013DCA960BAE23B6A0C7CF08A79C888A45C0375F67A01D4FABA8E1A4B1E853FAA6A9C09D380272A2980AF44914CE109E07446A734BD7F3BC2A14D2D7EBA"

_LOCK = threading.RLock()


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
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            digest = parts[0].strip().lower()
            if re.fullmatch(r"[0-9a-f]{64}", digest):
                return digest
    return ""


def offer_from(release: dict[str, Any]) -> dict[str, Any]:
    """Turn one release into something installable, or {} if it is not."""
    version = str(release.get("tag_name") or "").lstrip("vV")
    if not is_newer(version):
        return {}
    assets = {}
    for asset in release.get("assets") or []:
        if isinstance(asset, dict):
            assets[str(asset.get("name") or "")] = str(asset.get("browser_download_url") or "")
    installer = next((name for name in assets if name.endswith(INSTALLER_SUFFIX)), "")
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
        manifest_signed = _manifest_signature_ok(raw_sums, assets)
    except (urllib.error.URLError, OSError, ValueError):
        return {}
    if not digest:
        return {}
    if RELEASE_PUBLIC_KEY and not manifest_signed:
        # A key is published, so every release is expected to carry a manifest
        # signed with it. One that does not is either older than the key or not
        # ours; either way it is not something to offer.
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


def _manifest_signature_ok(raw_sums: bytes, assets: dict[str, str]) -> bool:
    """Whether the checksum manifest carries this project's own signature.

    The manifest names the installer's digest, so a signature over the manifest
    covers the installer without a second file to fetch per artifact.
    """
    from sandglass.release_signature import verify_release_signature

    if not RELEASE_PUBLIC_KEY or CHECKSUMS_SIGNATURE_NAME not in assets:
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
    with _LOCK:
        try:
            stored = _read_state()
        except (OSError, ValueError) as exc:
            record_component_failure("update_state_write", exc)
            return {}
        elapsed = _seconds_since_check(stored)
        if not force and elapsed is not None and elapsed < CHECK_TTL_SECONDS:
            cached = stored.get("offer")
            return dict(cached) if isinstance(cached, dict) else {}
        offer: dict[str, Any] = {}
        error = ""
        try:
            release = json.loads(_get(FEED_URL, MAX_FEED_BYTES).decode("utf-8", "replace"))
            if isinstance(release, dict):
                offer = offer_from(release)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            error = type(exc).__name__
        try:
            next_state = dict(stored)
            next_state.update({
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "current_version": __version__,
                "offer": offer,
                "error": error,
            })
            # A successful check must not erase the announcement waiting for
            # the first launch of the version it belongs to.
            _write_state(next_state)
        except OSError:
            return offer
        return offer


def authenticode_valid(path: Path) -> bool:
    """Whether Windows itself trusts this file's signature."""
    if os.name != "nt":
        return False

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]

    class FileInfo(ctypes.Structure):
        _fields_ = [("cbStruct", wintypes.DWORD), ("pcwszFilePath", wintypes.LPCWSTR),
                    ("hFile", wintypes.HANDLE), ("pgKnownSubject", ctypes.c_void_p)]

    class TrustData(ctypes.Structure):
        _fields_ = [("cbStruct", wintypes.DWORD), ("pPolicyCallbackData", ctypes.c_void_p),
                    ("pSIPClientData", ctypes.c_void_p), ("dwUIChoice", wintypes.DWORD),
                    ("fdwRevocationChecks", wintypes.DWORD), ("dwUnionChoice", wintypes.DWORD),
                    ("pFile", ctypes.POINTER(FileInfo)), ("dwStateAction", wintypes.DWORD),
                    ("hWVTStateData", wintypes.HANDLE), ("pwszURLReference", wintypes.LPCWSTR),
                    ("dwProvFlags", wintypes.DWORD), ("dwUIContext", wintypes.DWORD),
                    ("pSignatureSettings", ctypes.c_void_p)]

    # WINTRUST_ACTION_GENERIC_VERIFY_V2
    action = GUID(0x00AAC56B, 0xCD44, 0x11D0,
                  (ctypes.c_byte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
    info = FileInfo(ctypes.sizeof(FileInfo), str(path), None, None)
    data = TrustData()
    data.cbStruct = ctypes.sizeof(TrustData)
    data.dwUIChoice = 2            # WTD_UI_NONE
    data.fdwRevocationChecks = 0   # WTD_REVOKE_NONE
    data.dwUnionChoice = 1         # WTD_CHOICE_FILE
    data.pFile = ctypes.pointer(info)
    data.dwStateAction = 1         # WTD_STATEACTION_VERIFY
    try:
        trust = ctypes.WinDLL("wintrust.dll")
    except OSError:
        return False
    trust.WinVerifyTrust.restype = wintypes.LONG
    trust.WinVerifyTrust.argtypes = [wintypes.HWND, ctypes.POINTER(GUID), ctypes.c_void_p]
    result = trust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data))
    data.dwStateAction = 2         # WTD_STATEACTION_CLOSE
    trust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data))
    return result == 0


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
    # Two independent proofs that these bytes are ours, and either is enough.
    # Authenticode says Windows trusts the publisher; the manifest signature
    # says the key that signed every previous release signed this digest too.
    # Requiring only the first meant no release could be offered at all until a
    # certificate existed, which made the update path hostage to a question it
    # does not need answered. Requiring neither would leave the channel open to
    # anyone who can serve a download.
    if not (offer.get("manifest_signed") or authenticode_valid(into)):
        into.unlink(missing_ok=True)
        raise ValueError(
            "installer carries neither a trusted Authenticode signature nor a "
            "release manifest signed with this project's key"
        )
    return into


def apply_update(offer: dict[str, Any]) -> dict[str, Any]:
    """Download, verify, then hand over to the installer and leave.

    The installer refuses to write over a running copy, so this does not wait
    for it: it starts the installer detached and returns, and the caller quits.
    The installer relaunches Sandglass when it is done. The verified release
    metadata is retained until that new version has shown its announcement.
    """
    name = str(offer.get("asset") or "sandglass-setup.exe")
    staged = Path(tempfile.gettempdir()) / "sandglass-update" / name
    download_verified(offer, staged)
    version = str(offer.get("version") or "")
    if not version:
        raise ValueError("the release did not publish a version")
    with _LOCK:
        stored = _read_state()
        next_state = dict(stored)
        next_state["pending_announcement"] = {
            "version": version,
            "notes": offer.get("notes") if isinstance(offer.get("notes"), str) else "",
            "published_at": str(offer.get("published_at") or ""),
            "notes_url": str(offer.get("notes_url") or ""),
        }
        _write_state(next_state)
    subprocess.Popen(
        [
            str(staged),
            "/UPDATE",
            f"/PARENTPID={os.getpid()}",
            f"/RESTARTEXE={Path(sys.executable).resolve()}",
        ],
        creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    return {"ok": True, "version": version, "staged": str(staged)}


def update_announcement() -> dict[str, Any]:
    """Return the current version's one-time release announcement, if any."""
    with _LOCK:
        try:
            stored = _read_state()
        except (OSError, ValueError) as exc:
            record_component_failure("update_state_write", exc)
            return {}
    pending = stored.get("pending_announcement")
    if not isinstance(pending, dict) or pending.get("version") != __version__:
        return {}
    return dict(pending)


def dismiss_update_announcement(version: str) -> dict[str, Any]:
    """Dismiss only the named version's announcement; never a newer one."""
    version = str(version or "")
    with _LOCK:
        try:
            stored = _read_state()
        except (OSError, ValueError) as exc:
            record_component_failure("update_state_write", exc)
            return {"ok": False, "dismissed": False}
        pending = stored.get("pending_announcement")
        if not isinstance(pending, dict) or pending.get("version") != version:
            return {"ok": False, "dismissed": False}
        next_state = dict(stored)
        next_state.pop("pending_announcement", None)
        try:
            _write_state(next_state)
        except OSError:
            return {"ok": False, "dismissed": False}
    return {"ok": True, "dismissed": True, "version": version}
