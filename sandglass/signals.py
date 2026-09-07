from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sandglass.models import parse_ts
from sandglass.paths import claude_homes, codex_home, grok_home, meter_home


_EVIDENCE_LOCK = threading.Lock()


@dataclass(frozen=True)
class ProviderSignalState:
    """First-party facts that can make a cached quota snapshot obsolete."""

    identity: tuple[str, ...]
    authentication: tuple[str, ...] = ()
    used: tuple[tuple[str, float], ...] = ()
    remaining: tuple[tuple[str, float], ...] = ()
    periods: tuple[tuple[str, str], ...] = ()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pairs(values: dict[str, Any]) -> tuple[tuple[str, float], ...]:
    return tuple(sorted((key, value) for key, raw in values.items() if (value := _number(raw)) is not None))


def _periods(values: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, str(value)) for key, value in values.items() if value not in (None, "")))


def _secret_fingerprint(value: Any) -> str:
    """Detect an official-client login change without retaining its credential."""

    text = str(value or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def codex_signal_state() -> ProviderSignalState:
    """Official current Codex identity and credential fingerprint only."""
    auth = _read_json(codex_home() / "auth.json")
    auth = auth if isinstance(auth, dict) else {}
    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    return ProviderSignalState(
        identity=(str(tokens.get("account_id") or ""),),
        authentication=(_secret_fingerprint(tokens.get("access_token")),),
    )


def claude_signal_state() -> ProviderSignalState:
    """Claude identities from official credential/profile files, never token bytes."""

    identities: list[str] = []
    authentications: list[str] = []
    for home in claude_homes():
        credentials = _read_json(home / ".credentials.json")
        credentials = credentials if isinstance(credentials, dict) else {}
        oauth = credentials.get("claudeAiOauth") if isinstance(credentials.get("claudeAiOauth"), dict) else {}
        config = _read_json(home / ".claude.json")
        config = config if isinstance(config, dict) else {}
        profile = config.get("oauthAccount") if isinstance(config.get("oauthAccount"), dict) else {}
        identities.extend(
            (
                str(home),
                str(profile.get("accountUuid") or config.get("userID") or ""),
                str(profile.get("emailAddress") or "").lower(),
                str(profile.get("organizationUuid") or ""),
                str(oauth.get("subscriptionType") or ""),
                str(oauth.get("rateLimitTier") or profile.get("organizationRateLimitTier") or ""),
            )
        )
        authentications.append(_secret_fingerprint(oauth.get("accessToken")))
    return ProviderSignalState(
        identity=tuple(identities),
        authentication=tuple(authentications),
    )


def _grok_profile(auth: Any) -> dict[str, Any]:
    if not isinstance(auth, dict):
        return {}
    profiles = [
        value
        for value in auth.values()
        if isinstance(value, dict) and (value.get("email") or value.get("user_id") or value.get("key"))
    ]
    profiles.sort(key=lambda value: 0 if value.get("auth_mode") in {"oidc", "oauth", "session"} else 1)
    return profiles[0] if profiles else {}


def _grok_profile_state(auth: Any) -> tuple[str, str]:
    profile = _grok_profile(auth)
    identity = str(profile.get("user_id") or profile.get("principal_id") or profile.get("email") or "")
    credential = profile.get("key") or profile.get("access_token") or profile.get("accessToken")
    return identity, _secret_fingerprint(credential)


def grok_signal_state() -> ProviderSignalState:
    """Identity and credential state written by the official Grok CLI."""

    cli_id, cli_credential = _grok_profile_state(_read_json(grok_home() / "auth.json"))
    return ProviderSignalState(
        identity=(cli_id,),
        authentication=(cli_credential,),
    )


def _decreased(before: tuple[tuple[str, float], ...], after: tuple[tuple[str, float], ...]) -> bool:
    old = dict(before)
    return any(key in old and value < old[key] for key, value in after)


def _increased(before: tuple[tuple[str, float], ...], after: tuple[tuple[str, float], ...]) -> bool:
    old = dict(before)
    return any(key in old and value > old[key] for key, value in after)


def quota_signal_reasons(before: ProviderSignalState, after: ProviderSignalState) -> set[str]:
    reasons: set[str] = set()
    if before.identity != after.identity:
        reasons.add("identity_changed")
    if before.authentication != after.authentication:
        reasons.add("authentication_changed")
    if _decreased(before.used, after.used) or _increased(before.remaining, after.remaining):
        reasons.add("quota_recovered")
    old_periods = dict(before.periods)
    if any(key in old_periods and value != old_periods[key] for key, value in after.periods):
        reasons.add("period_changed")
    return reasons


class QuotaSignalMonitor:
    """Edge detector for first-party Claude, Codex, and Grok state."""

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._fired_claude_resets: set[str] = set()
        self._states = {
            "claude": claude_signal_state(),
            "codex": codex_signal_state(),
            "grok": grok_signal_state(),
        }

    def poll_events(self) -> dict[str, set[str]]:
        current = {
            "claude": claude_signal_state(),
            "codex": codex_signal_state(),
            "grok": grok_signal_state(),
        }
        changed = {
            provider: reasons
            for provider, state in current.items()
            if (reasons := quota_signal_reasons(self._states[provider], state))
        }
        deadlines = _claude_reset_deadlines()
        due = {stamp for stamp in deadlines if (parsed := parse_ts(stamp)) is not None and parsed <= self._now()}
        if due - self._fired_claude_resets:
            changed.setdefault("claude", set()).add("scheduled_reset")
        self._fired_claude_resets.intersection_update(deadlines)
        self._fired_claude_resets.update(due)
        self._states = current
        return changed

    def poll(self) -> set[str]:
        return set(self.poll_events())

    def next_poll_delay(self, cap: float) -> float:
        """Seconds to sleep until the next unpublished Claude reset, or `cap`.

        Identity and auth files change rarely. Rereading them every second was
        leftover from wanting the published reset to fire on time. The deadline
        is already on disk, so wait for it instead of polling toward it.
        """
        now = self._now()
        delay = cap
        for stamp in _claude_reset_deadlines():
            if stamp in self._fired_claude_resets:
                continue
            parsed = parse_ts(stamp)
            if parsed is None:
                continue
            remaining = (parsed - now).total_seconds()
            if remaining <= 0:
                return 0.05
            if remaining < delay:
                delay = remaining
        return max(0.05, delay)


def _claude_reset_deadlines() -> set[str]:
    """Reset times previously returned by Claude's official quota endpoint."""

    cache = _read_json(meter_home() / "quota-cache.json")
    cache = cache if isinstance(cache, dict) else {}
    snapshot = cache.get("claude") if isinstance(cache.get("claude"), dict) else {}
    windows = snapshot.get("windows") if isinstance(snapshot.get("windows"), list) else []
    return {
        str(window.get("resets_at"))
        for window in windows
        if isinstance(window, dict) and window.get("resets_at")
    }


def record_quota_signal(
    provider: str,
    reasons: set[str],
    result: dict[str, dict[str, Any]] | None = None,
    error: str = "",
) -> None:
    """Append privacy-minimal propagation evidence under SANDGLASS_HOME."""

    snapshots = [
        value
        for key, value in (result or {}).items()
        if key.split(":", 1)[0] == provider and isinstance(value, dict)
    ]
    succeeded = sum(bool(snapshot.get("ok")) for snapshot in snapshots)
    attempted = len(snapshots)
    if attempted and succeeded == attempted:
        status = "success"
    elif succeeded:
        status = "partial"
    else:
        status = "failed"
    fetched = sorted(str(snapshot.get("fetched_at") or "") for snapshot in snapshots if snapshot.get("fetched_at"))
    event = {
        "at": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "signals": sorted(reasons),
        "official_refresh_status": status,
        "official_refresh_attempted": attempted,
        "official_refresh_succeeded": succeeded,
        "official_fetched_at": fetched[-1] if fetched else "",
        # Never persist arbitrary exception text or endpoint response bodies: they
        # can contain account identifiers or credential-bearing URLs.
        "error": "refresh_failed" if error or any(snapshot.get("error") for snapshot in snapshots) else "",
    }
    path = meter_home() / "quota-signal-events.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _EVIDENCE_LOCK, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError as exc:
        # Not raised: the refresh that produced this event has already
        # finished, and failing it over a file we could not save would
        # drop the live quota answer. But a missing row used to mean no
        # signal fired, and this file is the only local witness.
        from sandglass.diagnostics import record_component_failure

        record_component_failure("quota_signal_write", exc)
        return
    from sandglass.diagnostics import clear_component_failure

    clear_component_failure("quota_signal_write")
