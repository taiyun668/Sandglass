from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sandglass.models import Account, QuotaWindow, parse_ts
from sandglass.accounts import state_file_lock
from sandglass.paths import claude_home, codex_home, grok_home, meter_home

USER_AGENT = "sandglass/0.1"
# How long one vendor request may hang before it is abandoned. Named because
# a stop has to wait for whichever request is in flight, and that wait should
# be derived from this rather than guessed alongside it.
HTTP_TIMEOUT_SECONDS = 15.0

CACHE_TTL_SECONDS_BY_PROVIDER = {
    "claude": 180,
    "codex": 90,
    "grok": 90,
}
STALE_TTL_SECONDS = 24 * 3600
# A reading stops describing the current timer once a fresh one should already
# have replaced it -- two refresh cycles, so a single missed poll is not called
# staleness -- but never inside five minutes, because a provider polled every
# ninety seconds would otherwise show a stale badge for an ordinary hiccup.
DISPLAY_STALE_FLOOR_SECONDS = 300

_CACHE_LOCK = threading.Lock()
_FETCH_LOCK = threading.Lock()


def _cache_ttl_seconds(snapshot_name: str) -> int:
    provider = snapshot_name.split(":", 1)[0]
    return CACHE_TTL_SECONDS_BY_PROVIDER[provider]


def next_quota_fetch_at(provider: str, fetched_at: Any) -> str:
    """Wall-clock instant the vendor quota cache is due to be fetched again.

    Observer and `_fresh` both count CACHE_TTL_SECONDS_BY_PROVIDER from
    `fetched_at`. The panel counts down to this instant, not to its own HTTP
    reread of the cache.
    """
    fetched = parse_ts(fetched_at)
    if fetched is None:
        return ""
    ttl = CACHE_TTL_SECONDS_BY_PROVIDER.get(provider)
    if not ttl:
        return ""
    return (fetched + timedelta(seconds=ttl)).isoformat()


def _write_json_atomic(path: Path, payload: Any, *, component: str) -> None:
    """Replace a state file in one step, or leave the old one alone.

    These were the only two state writes in Sandglass that truncated the target
    and wrote in place; everything else already stages a temporary file and
    replaces. A process killed mid-write left the file half-written, and both of
    these files are read with a broad except that treats damaged as empty -- so
    a truncated quota-observations.json silently discards every reset anchor
    Sandglass ever caught, and reads as a machine that had never seen one.
    """
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        # Not raised: both callers are bookkeeping inside a quota fetch, and a
        # fetch that answers must not fail over a file it could not save. But
        # losing this write has exactly the effect the paragraph above
        # describes -- the anchors go missing and the machine reads as one that
        # never saw a reset -- so it is reported rather than dropped.
        from sandglass.diagnostics import record_component_failure

        record_component_failure(component, exc)
        return
    from sandglass.diagnostics import clear_component_failure

    clear_component_failure(component)


def _snapshot_display_stale(snapshot_name: str, entry: Any) -> bool:
    """Whether a cached quota is too old to describe the current timer state."""
    if not isinstance(entry, dict):
        return True
    fetched = parse_ts(entry.get("fetched_at"))
    if fetched is None:
        return True
    provider = snapshot_name.split(":", 1)[0]
    ttl = CACHE_TTL_SECONDS_BY_PROVIDER.get(provider, 0)
    limit = max(2 * ttl, DISPLAY_STALE_FLOOR_SECONDS)
    return (datetime.now(timezone.utc) - fetched).total_seconds() > limit


def fetch_all_quotas(
    force: bool = False,
    providers: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    with _FETCH_LOCK:
        return _fetch_all_quotas(force=force, providers=providers)


def _fetch_all_quotas(
    force: bool = False,
    providers: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    # A targeted refresh must retain unrelated provider cache entries. A full
    # forced refresh keeps the old replacement semantics and rebuilds all keys.
    now = datetime.now(timezone.utc)
    requested = providers or {"claude", "codex", "grok"}
    jobs: dict[str, Any] = {}
    if "claude" in requested:
        jobs["claude"] = fetch_claude_quota
    if "codex" in requested:
        jobs["codex"] = fetch_codex_quota
    if "grok" in requested:
        jobs["grok"] = fetch_grok_quota
    out: dict[str, dict[str, Any]] = {}
    pending: dict[str, Any] = {}
    stale_by_name: dict[str, Any] = {}
    # The threading lock is in-process only. Observer and `serve` both write
    # this file. Hold the interprocess lock only around the disk merge, not
    # the vendor HTTP, or identity notes wait out a 15s timeout.
    with state_file_lock(_cache_path()):
        try:
            cached = _read_cache() if providers is not None or not force else {}
        except (OSError, ValueError) as exc:
            # A locked cache is not empty. Treating it as `{}` made a targeted
            # refresh write only the provider it had just fetched.
            from sandglass.diagnostics import record_component_failure

            record_component_failure("quota_cache_write", exc)
            cached = {}
        # Older releases cached one row per community Grok App profile. Those
        # rows are no longer admissible and must not survive in Sandglass state.
        if "grok" in requested:
            filtered = {key: value for key, value in cached.items() if not key.startswith("grok:")}
            if len(filtered) != len(cached):
                cached = filtered
                _write_cache(cached)
        for name, fn in jobs.items():
            hit = cached.get(name)
            usable = not force and _fresh(hit, now, _cache_ttl_seconds(name))
            if usable:
                out[name] = hit
            else:
                pending[name] = fn
                stale_by_name[name] = hit
    if pending:
        with ThreadPoolExecutor(max_workers=min(8, max(3, len(pending)))) as pool:
            futures = {pool.submit(fn): name for name, fn in pending.items()}
            for future in as_completed(futures):
                name = futures[future]
                stale_fallback = False
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001
                    result = {"ok": False, "provider": name, "error": str(exc), "scope": "account-wide"}
                if not result.get("ok"):
                    stale = stale_by_name.get(name)
                    if _fresh(stale, now, STALE_TTL_SECONDS) and stale.get("windows"):
                        stale = dict(stale)
                        stale["stale"] = True
                        stale["error"] = result.get("error")
                        stale["last_attempt_at"] = now.isoformat()
                        result = stale
                        stale_fallback = True
                if not stale_fallback:
                    result["fetched_at"] = now.isoformat()
                out[name] = result
        with state_file_lock(_cache_path()):
            try:
                latest = _read_cache()
            except (OSError, ValueError) as exc:
                from sandglass.diagnostics import record_component_failure

                record_component_failure("quota_cache_write", exc)
                if providers is not None:
                    return out
                latest = {}
            if "grok" in requested:
                latest = {key: value for key, value in latest.items() if not key.startswith("grok:")}
            latest.update(out)
            _write_cache(latest)
    return out


def refresh_quota_provider(provider: str) -> dict[str, dict[str, Any]]:
    """Bypass the provider cache only for an invalidated provider."""

    if provider not in {"claude", "codex", "grok"}:
        raise ValueError(f"unsupported provider: {provider}")
    return fetch_all_quotas(force=True, providers={provider})


_OBS_LOCK = threading.Lock()
_RESET_DROP = 5.0
# Longer than this between two observations and we cannot vouch for what happened in
# between -- a reset could have hidden inside it, so anything extrapolated from used%
# stops being trustworthy.
_GAP_SECONDS = 600.0


def _observations_path() -> Path:
    return meter_home() / "quota-observations.json"


def _read_owned_object(path: Path) -> dict[str, Any] | None:
    """Sandglass-owned JSON object. Missing is None; unreadable is not.

    Vendor files go through `_read_json`, which treats any OSError as "the
    vendor said nothing". These two files are ours: a locked
    quota-observations.json became `{}` and the next note wrote one window
    over every reset anchor; a locked quota-cache.json became `{}` and a
    targeted refresh wrote only the provider it had just fetched.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not an object")
    return value


def _read_observations() -> dict[str, Any]:
    return _read_owned_object(_observations_path()) or {}


# Windows whose reset can hide inside a period: used% goes back to zero without the
# period moving, so window_start stops bounding what that percentage covers.
#
#   grok  - reset cards zero the credit without touching currentPeriod.
#   claude 7d - the weekly allowance runs on a schedule assigned per account: a fixed
#     weekday and time, which differs between accounts but never moves for one of them.
#     It shows up as an anchor on the exact hour, unlike Codex and Grok whose anchors
#     land on arbitrary seconds because they re-anchor on first use. A schedule cannot
#     be pushed, so a granted reset can only zero the usage and leave resets_at alone.
#     (One account here: Wednesday 23:00 US Pacific. The weekday is not the signal --
#     the fixed, on-the-hour schedule is.)
#
# Everything else re-anchors on first use after a reset, which makes the reported
# period start authoritative and needs no observation history to trust.
_HIDDEN_RESET_WINDOWS = {("grok", None), ("claude", "7d")}


def _can_hide_reset(provider: str, label: str) -> bool:
    return (provider, None) in _HIDDEN_RESET_WINDOWS or (provider, label) in _HIDDEN_RESET_WINDOWS


def _period_end_key(value: Any) -> str:
    """Canonical provider period end at the precision the official UI exposes.

    Each account keeps its own schedule.  Claude's API can move the fractional
    seconds around the same displayed minute, so comparing the raw timestamp would
    turn one account's unchanged period into a new one.
    """
    stamp = parse_ts(value)
    if stamp is None:
        return str(value or "")
    stamp = stamp.astimezone(timezone.utc)
    return (stamp + timedelta(seconds=30)).replace(second=0, microsecond=0).isoformat()


def _period_end_changed(previous: str, current: str) -> bool:
    if not previous or not current:
        return False
    return _period_end_key(previous) != _period_end_key(current)


def _window_started_at(window: dict[str, Any]):
    resets = parse_ts(window.get("resets_at"))
    minutes = window.get("window_minutes")
    if resets is None or not isinstance(minutes, (int, float)) or not minutes:
        return None
    return resets - timedelta(minutes=float(minutes))


def _missed_window_start(provider: str, window: dict[str, Any], now: datetime) -> bool:
    """True when this window was already running before we first looked at it.

    Only meaningful where a reset can hide inside a period: elsewhere the reported
    period start always bounds what used% covers, so a first reading is enough.
    """
    if not _can_hide_reset(provider, str(window.get("label") or "")):
        return False
    started = _window_started_at(window)
    if started is None:
        return True
    return (now - started).total_seconds() > _GAP_SECONDS


_READINGS_LOCK = threading.Lock()


def quota_readings_path() -> Path:
    return meter_home() / "quota-readings.jsonl"


def _note_quota_reading(provider: str, account_id: str, label: str,
                        used: float, resets_at: str, at: str,
                        previous: dict[str, Any] | None) -> None:
    """Keep what the provider said, and when, as a step function.

    Nothing else retains this. quota-observations.json holds one current value
    per window and overwrites it, so after the fact there is no way to ask what
    the vendor was reporting an hour ago or exactly when a period rolled over --
    the panel's own recorded answers are what made the reset bug findable, and
    the provider's side of the same minutes had no equivalent.

    Only a changed reading is appended: used% is a step function and a row is
    the step. No row means the value had not moved, which the observer coverage
    ledger separately says whether we were there to see.

    Deliberately not a check. Quota is charged in weighted units the provider
    does not publish, so a ratio against local tokens drifts for honest reasons
    -- different models and cache classes cost differently -- and any threshold
    over it would be fitted, not derived. This is evidence to read later.
    """
    if previous is not None:
        if (float(previous.get("used") or 0.0) == float(used)
                and str(previous.get("resets_at") or "") == resets_at):
            return
    row = {
        "at": at,
        "provider": provider,
        "account_id": account_id,
        "label": label,
        "used_percent": float(used),
        "resets_at": resets_at,
    }
    try:
        # Resolving the path is inside the guard too: this is bookkeeping beside
        # an observation, and nothing about it may stop the observation itself.
        path = quota_readings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _READINGS_LOCK, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError as exc:
        # Not raised: this is bookkeeping beside an observation, and the
        # observation itself must still land. But a missing jsonl row used
        # to mean the value had not moved, and after observations stored the
        # new used% the next poll never tried the append again. Report it
        # the same way the observation and cache writes already do.
        from sandglass.diagnostics import record_component_failure

        record_component_failure("quota_reading_write", exc)
        return
    from sandglass.diagnostics import clear_component_failure

    clear_component_failure("quota_reading_write")


def note_quota_windows(provider: str, account_id: str, windows: list[dict[str, Any]]) -> None:
    """Track used% over time so a mid-period reset can be seen at all.

    A reset card -- and xAI's own launch-day resets -- put creditUsagePercent back to
    zero WITHOUT moving the period: reset time stays where it was. Nothing in the
    billing payload announces it, so the only evidence is used% dropping between two
    observations. Anchoring local usage to that moment is what keeps the panel from
    dividing by a percentage that no longer covers the work already logged.
    """
    if not windows:
        return
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    with _OBS_LOCK, state_file_lock(_observations_path()):
        try:
            store = _read_observations()
        except (OSError, ValueError) as exc:
            from sandglass.diagnostics import record_component_failure

            record_component_failure("quota_observation_write", exc)
            return
        changed = False
        for window in windows:
            label = str(window.get("label") or "")
            used = window.get("used_percent")
            if not label or not isinstance(used, (int, float)):
                continue
            key = f"{provider}:{account_id}:{label}"
            resets_at = str(window.get("resets_at") or "")
            prev = store.get(key) if isinstance(store.get(key), dict) else None
            _note_quota_reading(provider, account_id, label, float(used),
                                resets_at, now, prev)
            if prev is None:
                # First sight of this window. If it was already running, we cannot say
                # whether it was reset before we arrived.
                store[key] = {"anchor": "", "anchor_kind": "period", "resets_at": resets_at,
                              "used": float(used), "seen_at": now,
                              "source": "official_live",
                              "gap": _missed_window_start(provider, window, now_dt)}
                changed = True
                continue
            if not str(prev.get("source") or "").startswith("official_live"):
                # Older builds did not record whether an observation came from
                # a live official response or a retained adapter snapshot. The
                # first proven live response establishes a clean baseline rather
                # than comparing against an ambiguous value and inventing a reset.
                store[key] = {
                    "anchor": "",
                    "anchor_kind": "period",
                    "resets_at": resets_at,
                    "used": float(used),
                    "seen_at": now,
                    "source": "official_live",
                    "gap": _missed_window_start(provider, window, now_dt),
                }
                changed = True
                continue
            prev_used = float(prev.get("used") or 0.0)
            prev_seen = parse_ts(prev.get("seen_at"))
            prev_resets_at = str(prev.get("resets_at") or "")
            gap = bool(prev.get("gap"))
            idle = (now_dt - prev_seen).total_seconds() if prev_seen is not None else 0.0
            if not _can_hide_reset(provider, label):
                gap = False
            elif idle > _GAP_SECONDS:
                # Only dangerous if a reset could hide in it. A reset drops used% to
                # zero, so one that stayed invisible would have had to be followed by
                # burning the whole pre-gap amount back within the gap itself. Below an
                # hour that is not credible; past it, assume we missed something.
                gap = float(used) < prev_used or idle > 3600.0
            elif gap and float(used) >= prev_used:
                gap = False
            # A full official allowance is direct evidence: everything before this
            # observation belongs to the old allowance.  It outranks sub-second
            # movement in the provider's period timestamp.
            refilled = float(used) == 0.0 and prev_used > 0.0
            if _can_hide_reset(provider, label) and refilled:
                store[key] = {
                    "anchor": now,
                    "anchor_kind": "reset",
                    "reset_signal": "refilled",
                    "resets_at": resets_at or prev_resets_at,
                    "used": 0.0,
                    "seen_at": now,
                    "source": "official_live",
                    "gap": False,
                }
                changed = True
                continue
            if _period_end_changed(prev_resets_at, resets_at):
                # A genuinely new period. Its own start is the anchor again -- and if we
                # caught it starting, the blind spot is closed and extrapolation is fair.
                store[key] = {"anchor": "", "anchor_kind": "period", "resets_at": resets_at,
                              "used": float(used), "seen_at": now,
                              "source": "official_live",
                              "gap": _missed_window_start(provider, window, now_dt)}
                changed = True
                continue
            # Back to a full allowance with the period untouched: that is a reset, full
            # stop -- no threshold needed, 0 cannot be rounding noise. But the zero is
            # only visible if nobody worked between the reset and the next poll (quota
            # is cached 90-180s), and a card that only tops part of the pool never reaches
            # it, so a large drop counts too.
            if float(used) <= prev_used - _RESET_DROP:
                # The exact moment is somewhere between the two polls; take the earlier
                # one so nothing already logged is dropped.
                store[key] = {"anchor": str(prev.get("seen_at") or now), "anchor_kind": "reset",
                              "reset_signal": "drop",
                              "resets_at": resets_at, "used": float(used), "seen_at": now,
                              "source": "official_live"}
                changed = True
                continue
            # seen_at must keep moving even when nothing else does: it is the proof that
            # we were watching. Throttle the disk write, never the observation itself,
            # or an idle window freezes its own timestamp and can never clear its gap.
            if (
                gap != bool(prev.get("gap"))
                or abs(float(used) - prev_used) >= 0.5
                or idle > _GAP_SECONDS / 2
            ):
                prev["used"] = float(used)
                prev["seen_at"] = now
                prev["gap"] = gap
                prev["resets_at"] = resets_at or prev.get("resets_at") or ""
                store[key] = prev
                changed = True
        if not changed:
            return
        _write_json_atomic(_observations_path(), store,
                           component="quota_observation_write")


def quota_anchor(provider: str, account_id: str, label: str) -> tuple[str, str, bool]:
    """(anchor_iso, kind, gap). Empty anchor means "use the period's own start".

    gap=True means observation stopped for a while, so a reset could have happened
    unseen and used% can no longer be trusted to cover the same span as local usage.
    """
    try:
        entry = _read_observations().get(f"{provider}:{account_id}:{label}")
    except (OSError, ValueError):
        return "", "period", True
    if not isinstance(entry, dict):
        return "", "period", True
    return (
        str(entry.get("anchor") or ""),
        str(entry.get("anchor_kind") or "period"),
        bool(entry.get("gap")),
    )


def quota_anchor_source(provider: str, account_id: str, label: str) -> str:
    try:
        entry = _read_observations().get(f"{provider}:{account_id}:{label}")
    except (OSError, ValueError):
        return ""
    return str(entry.get("source") or "") if isinstance(entry, dict) else ""


def attach_live_quota(
    accounts: list[Account],
    force: bool = False,
    providers: set[str] | None = None,
) -> list[Account]:
    return _attach_quota_snapshots(
        accounts,
        fetch_all_quotas(force=force, providers=providers),
        record_observations=True,
    )


def attach_cached_quota(accounts: list[Account]) -> list[Account]:
    """Attach Observer-owned snapshots without fetching or writing observations."""
    try:
        snapshots = _read_cache()
    except (OSError, ValueError):
        snapshots = {}
    return _attach_quota_snapshots(
        accounts,
        snapshots,
        record_observations=False,
    )


def _attach_quota_snapshots(
    accounts: list[Account],
    snapshots: dict[str, dict[str, Any]],
    *,
    record_observations: bool,
) -> list[Account]:
    out: list[Account] = []
    for account in accounts:
        extra = dict(account.extra)
        snap = None
        if account.provider == "claude":
            snap = snapshots.get("claude")
            snap_id = str((snap or {}).get("account_id") or "")
            if account.extra.get("account_source_official") is False:
                # A user adapter may call the official quota endpoint itself and
                # submit account-scoped windows. The built-in Claude response is
                # not account-keyed, so it must not be copied to another account.
                if not snap_id or snap_id != account.account_id:
                    snap = None
        elif account.provider == "grok":
            snap = snapshots.get(f"grok:{account.account_id}") or snapshots.get("grok")
            snap_id = str((snap or {}).get("account_id") or "")
            if snap and snap.get("ok"):
                if snap_id and snap_id != account.account_id:
                    snap = None
                elif not snap_id and not account.active:
                    snap = None
        elif account.provider == "codex":
            snap = snapshots.get("codex")
            snap_id = str((snap or {}).get("account_id") or "")
            if snap and snap.get("ok"):
                if snap_id and snap_id != account.account_id:
                    snap = None
                elif not snap_id and not account.active:
                    snap = None
        if snap and (snap.get("windows") or snap.get("ok")):
            extra["windows"] = snap.get("windows") or extra.get("windows") or []
            snapshot_name = (
                f"grok:{account.account_id}"
                if account.provider == "grok" and snapshots.get(f"grok:{account.account_id}") is snap
                else account.provider
            )
            display_stale = not record_observations and _snapshot_display_stale(
                snapshot_name, snap
            )
            extra["quota_source"] = (
                "stale" if snap.get("stale") or display_stale else "live"
            )
            extra["quota_error"] = snap.get("error") or ""
            extra["quota_fetched_at"] = snap.get("fetched_at")
            extra["quota_note"] = snap.get("note") or ""
            if snap.get("plan") and not account.plan:
                account = Account(**{**account.as_dict(), "plan": str(snap["plan"]), "extra": extra})
                if record_observations and extra.get("quota_source") == "live":
                    note_quota_windows(account.provider, account.account_id, list(extra.get("windows") or []))
                out.append(account)
                continue
        elif snap and not snap.get("ok"):
            extra["quota_source"] = extra.get("quota_source") or "error"
            extra["quota_error"] = snap.get("error") or ""
        if (
            snap is not None
            and not snap.get("ok")
            and extra.get("windows")
            and extra.get("quota_source") not in {"live", "stale"}
            and extra.get("quota_fetched_at")
            and _snapshot_display_stale(
                account.provider,
                {"fetched_at": extra.get("quota_fetched_at")},
            )
        ):
            # A user-adapter or older cached quota may remain as useful
            # historical evidence, but it must not masquerade as the current
            # allowance after its observation time has expired.
            extra["quota_previous_source"] = extra.get("quota_source") or ""
            extra["quota_source"] = "stale"
        extra.setdefault("quota_source", "cached" if extra.get("windows") else "unknown")
        account = Account(**{**account.as_dict(), "extra": extra})
        if record_observations and extra.get("quota_source") == "live":
            note_quota_windows(account.provider, account.account_id, list(extra.get("windows") or []))
        out.append(account)
    return out


def fetch_claude_quota() -> dict[str, Any]:
    cred_path = claude_home() / ".credentials.json"
    cred = _read_json(cred_path)
    oauth = cred.get("claudeAiOauth") if isinstance(cred, dict) else None
    if not isinstance(oauth, dict) or not oauth.get("accessToken"):
        return {"ok": False, "provider": "claude", "error": "no local Claude credentials", "scope": "account-wide"}
    headers = {
        "Authorization": f"Bearer {oauth['accessToken']}",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20",
        "accept": "application/json",
        "user-agent": USER_AGENT,
    }
    body, error = _get("https://api.anthropic.com/api/oauth/usage", headers)
    if error:
        return {
            "ok": False,
            "provider": "claude",
            "error": f"{error}. Re-login with the official Claude client if the session expired.",
            "scope": "account-wide",
        }
    if not isinstance(body, dict) or not isinstance(body.get("five_hour"), dict):
        # A 200 response is not the same claim as "here is your usage" -- during a
        # provider incident this endpoint can answer with a body that carries no
        # five_hour window at all. Showing that as "no window here" or "just
        # updated" would be the wrong why; it is a fetch failure, worded like one.
        return {
            "ok": False,
            "provider": "claude",
            "error": "HTTP 200 with no five_hour window in the body",
            "scope": "account-wide",
        }
    windows = parse_claude_usage(body)
    extra = body.get("extra_usage") if isinstance(body.get("extra_usage"), dict) else {}
    return {
        "ok": True,
        "provider": "claude",
        "scope": "account-wide",
        "windows": [w.as_dict() for w in windows],
        "extra_usage": extra,
        "note": "Includes this Claude account on every device, not only this computer.",
    }


def fetch_codex_quota() -> dict[str, Any]:
    auth = _read_json(codex_home() / "auth.json")
    if not isinstance(auth, dict):
        return {"ok": False, "provider": "codex", "error": "no local Codex auth.json", "scope": "account-wide"}
    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    access = str(tokens.get("access_token") or "")
    if not access:
        return {"ok": False, "provider": "codex", "error": "Codex access token missing", "scope": "account-wide"}
    account_id = str(tokens.get("account_id") or "")
    headers = {
        "Authorization": f"Bearer {access}",
        "Accept": "application/json",
        "User-Agent": "Codex-CLI",
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id
    body, error = _get("https://chatgpt.com/backend-api/wham/usage", headers)
    if error:
        return {"ok": False, "provider": "codex", "error": error, "scope": "account-wide"}
    if not isinstance(body, dict) or not isinstance(body.get("rate_limit"), dict):
        # Same shape as the Claude check above: a 200 with no rate_limit object is
        # a provider-side failure, not an account that simply has no windows.
        return {
            "ok": False,
            "provider": "codex",
            "error": "HTTP 200 with no rate_limit data in the body",
            "scope": "account-wide",
        }
    windows = parse_codex_wham(body)
    from sandglass.accounts import _codex_plan_name

    return {
        "ok": True,
        "provider": "codex",
        "scope": "account-wide",
        "account_id": str(body.get("account_id") or account_id),
        "email": str(body.get("email") or ""),
        "plan": _codex_plan_name(body.get("plan_type") or ""),
        "windows": [w.as_dict() for w in windows],
        "credits": body.get("credits") if isinstance(body.get("credits"), dict) else {},
        "note": "Live Codex 5h/7d for the account currently in auth.json. Includes other devices.",
    }


def fetch_grok_quota() -> dict[str, Any]:
    return fetch_grok_quota_from_auth(str(grok_home() / "auth.json"), "")


def fetch_grok_quota_from_auth(auth_path: str, account_id: str = "") -> dict[str, Any]:
    path = Path(auth_path)
    token, resolved_id, pick_err = _grok_access_token(path, account_id)
    if not token:
        return {
            "ok": False,
            "provider": "grok",
            "error": f"{pick_err or 'Grok session key missing'}. Re-login with the official Grok client.",
            "scope": "account-wide",
            "account_id": resolved_id or account_id,
        }
    headers = {
        "Authorization": f"Bearer {token}",
        "x-xai-token-auth": "xai-grok-cli",
        "Accept": "application/json",
        "User-Agent": "grok-cli",
    }
    body, error = _get("https://cli-chat-proxy.grok.com/v1/billing?format=credits", headers)
    if error:
        return {
            "ok": False,
            "provider": "grok",
            "error": f"{error}. Re-login with the official Grok client if the session expired.",
            "scope": "account-wide",
            "account_id": resolved_id or account_id,
        }
    windows = parse_grok_billing(body)
    config = body.get("config") if isinstance(body.get("config"), dict) else {}
    plan = _grok_plan(body if isinstance(body, dict) else {}, token, resolved_id or account_id)
    return {
        "ok": True,
        "provider": "grok",
        "scope": "account-wide",
        "account_id": resolved_id,
        "plan": plan,
        "windows": [w.as_dict() for w in windows],
        "prepaid_balance": _nested_val(config.get("prepaidBalance")),
        "note": f"{plan or 'Grok'} weekly pool from this login. Includes other devices.",
    }


def _grok_access_token(auth_path: Path, account_id: str = "") -> tuple[str, str, str | None]:
    from sandglass.accounts import grok_account_id, grok_profiles

    auth = _read_json(auth_path)
    for profile in grok_profiles(auth):
        token = str(profile.get("key") or "")
        if not token:
            continue
        resolved = grok_account_id(profile)
        if account_id and resolved and account_id != resolved:
            continue
        expires = parse_ts(profile.get("expires_at"))
        if expires is not None and expires <= datetime.now(timezone.utc) + timedelta(seconds=60):
            return "", resolved, "OAuth access token has expired"
        return token, resolved, None
    return "", account_id, "Grok session key missing"


def parse_claude_usage(body: dict[str, Any]) -> list[QuotaWindow]:
    windows: list[QuotaWindow] = []
    mapping = (
        ("five_hour", "5h", 300),
        ("seven_day", "7d", 10080),
        ("seven_day_sonnet", "7d sonnet", 10080),
        ("seven_day_opus", "7d opus", 10080),
    )
    for key, label, minutes in mapping:
        raw = body.get(key)
        if isinstance(raw, dict) and raw.get("utilization") is not None:
            windows.append(
                QuotaWindow(
                    label=label,
                    used_percent=_float(raw.get("utilization")),
                    resets_at=str(raw.get("resets_at") or "") or None,
                    window_minutes=minutes,
                )
            )
    return windows


def parse_codex_wham(body: dict[str, Any]) -> list[QuotaWindow]:
    rate = body.get("rate_limit") if isinstance(body.get("rate_limit"), dict) else {}
    windows: list[QuotaWindow] = []
    for key, fallback in (("primary_window", "5h"), ("secondary_window", "7d")):
        raw = rate.get(key)
        if not isinstance(raw, dict):
            continue
        seconds = raw.get("limit_window_seconds")
        minutes = int(seconds) // 60 if isinstance(seconds, (int, float)) else None
        label = "5h" if minutes == 300 else ("7d" if minutes == 10080 else fallback)
        windows.append(
            QuotaWindow(
                label=label,
                used_percent=_float(raw.get("used_percent")),
                resets_at=_unix_to_iso(raw.get("reset_at")),
                window_minutes=minutes,
                extra={"reset_after_seconds": raw.get("reset_after_seconds")},
            )
        )
    extra_limits = body.get("additional_rate_limits")
    if isinstance(extra_limits, list):
        for item in extra_limits:
            if not isinstance(item, dict) or item.get("used_percent") is None:
                continue
            windows.append(
                QuotaWindow(
                    label=str(item.get("name") or item.get("label") or "extra"),
                    used_percent=_float(item.get("used_percent")),
                    resets_at=_unix_to_iso(item.get("reset_at") or item.get("resets_at")),
                )
            )
    return windows


_GROK_TIER_PLANS = {1: "SuperGrok"}


def _grok_plan_name(raw: Any) -> str:
    if raw is None or isinstance(raw, bool):
        return ""
    if isinstance(raw, (int, float)):
        return _GROK_TIER_PLANS.get(int(raw), "")
    text = str(raw).strip()
    if not text:
        return ""
    if text.isdigit():
        return _GROK_TIER_PLANS.get(int(text), "")
    compact = text.lower().replace(" ", "").replace("_", "")
    if compact == "supergrok":
        return "SuperGrok"
    return text


def _grok_plan(body: dict[str, Any], token: str = "", account_id: str = "") -> str:
    config = body.get("config") if isinstance(body.get("config"), dict) else {}
    for raw in (
        body.get("subscriptionTier"),
        config.get("subscriptionTier"),
        body.get("plan"),
        config.get("plan"),
        body.get("tier"),
        config.get("tier"),
    ):
        name = _grok_plan_name(raw)
        if name:
            return name
    if token:
        from sandglass.accounts import _jwt_payload

        return _grok_plan_name(_jwt_payload(token).get("tier"))
    return ""


def parse_grok_billing(body: dict[str, Any]) -> list[QuotaWindow]:
    config = body.get("config") if isinstance(body.get("config"), dict) else body
    windows: list[QuotaWindow] = []
    period = config.get("currentPeriod") if isinstance(config.get("currentPeriod"), dict) else {}
    period_type = str(period.get("type") or "")
    minutes = 10080 if "WEEKLY" in period_type else (43200 if "MONTHLY" in period_type else 10080)
    label = "7d" if minutes == 10080 else ("30d" if minutes == 43200 else "period")
    pct = _float(config.get("creditUsagePercent"))
    if pct is None:
        used = _nested_val(config.get("onDemandUsed"))
        cap = _nested_val(config.get("onDemandCap"))
        if cap:
            pct = 100.0 * used / cap
        elif period.get("end") or config.get("billingPeriodEnd"):
            pct = 0.0
    if pct is not None:
        windows.append(
            QuotaWindow(
                label=label,
                used_percent=pct,
                resets_at=str(period.get("end") or config.get("billingPeriodEnd") or "") or None,
                window_minutes=minutes,
                extra={"period_type": period_type or "weekly"},
            )
        )
    products = config.get("productUsage")
    if isinstance(products, list):
        for item in products:
            if not isinstance(item, dict):
                continue
            product = str(item.get("product") or "")
            prod_pct = _float(item.get("usagePercent"))
            if product and prod_pct is not None and product.lower() not in {"grokbuild", "grok build"}:
                windows.append(QuotaWindow(label=product, used_percent=prod_pct, window_minutes=minutes))
    return windows


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _cache_path() -> Path:
    return meter_home() / "quota-cache.json"


def _read_cache() -> dict[str, Any]:
    with _CACHE_LOCK:
        return _read_owned_object(_cache_path()) or {}


def _write_cache(payload: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        _write_json_atomic(_cache_path(), payload,
                           component="quota_cache_write")


def _fresh(entry: Any, now: datetime, ttl: int) -> bool:
    if not isinstance(entry, dict) or not entry.get("fetched_at"):
        return False
    fetched = parse_ts(entry.get("fetched_at"))
    if fetched is None:
        return False
    return (now - fetched).total_seconds() <= ttl


def _get(url: str, headers: dict[str, str]) -> tuple[Any, str | None]:
    req = Request(url, headers=headers, method="GET")
    return _send(req)


def _send(req: Request) -> tuple[Any, str | None]:
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw:
                return {}, None
            return json.loads(raw), None
    except HTTPError as exc:
        if exc.code == 429:
            return {}, "Rate limited (HTTP 429)"
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:240]
        except Exception:
            detail = ""
        message = f"HTTP {exc.code}"
        if "expired" in detail.lower():
            message = "OAuth access token has expired"
        elif detail:
            try:
                parsed = json.loads(detail)
                err = parsed.get("error") if isinstance(parsed, dict) else None
                if isinstance(err, dict) and err.get("message"):
                    message = str(err["message"])
                elif isinstance(err, str):
                    message = str(parsed.get("error_description") or err)
            except json.JSONDecodeError:
                pass
        if exc.code == 401 and "expired" not in message.lower():
            message = "Authentication expired or rejected (HTTP 401)"
        return {}, message
    except Exception as exc:  # noqa: BLE001
        return {}, str(exc)


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _nested_val(value: Any) -> float:
    if isinstance(value, dict) and "val" in value:
        return _float(value.get("val")) or 0.0
    return _float(value) or 0.0


def _unix_to_iso(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    ts = float(value)
    if ts > 1e12:
        ts /= 1000.0
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return None
