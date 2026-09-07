from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import replace as dataclass_replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sandglass.models import (
    PROVIDERS,
    Account,
    QuotaWindow,
    SessionRecord,
    mask_email,
    parse_ts,
    replace_account,
)
from sandglass.paths import (
    claude_homes,
    codex_home,
    grok_home,
    meter_home,
)


_IDENTITY_LOCK = threading.RLock()
_IDENTITY_PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat()
_OWNED_JSON_MISSING = object()
_OWNED_JSON_INVALID = object()

# The legacy Codex list is retained as roster evidence only.  Ownership needs
# an explicit coverage-aware stream so an observer restart can close the old
# interval before a later observation reopens it.
_CODEX_IDENTITY_EVENTS_SCHEMA = 2
_CODEX_EVENT_OBSERVED = "observed"
_CODEX_EVENT_UNASSIGNED = "unassigned"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_owned_json(path: Path) -> Any:
    """Read Sandglass state without collapsing missing and damaged files.

    Vendor files remain best-effort through ``_read_json``.  Sandglass-owned
    identity state is different: replacing an existing unreadable ledger with
    a fresh partial history would permanently destroy attribution evidence.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _OWNED_JSON_MISSING
    except OSError:
        return _OWNED_JSON_INVALID
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _OWNED_JSON_INVALID


def _jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    pad = "=" * ((4 - len(payload) % 4) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload + pad))
    except (ValueError, json.JSONDecodeError):
        return {}


def _short_id(value: str, n: int = 8) -> str:
    value = value or ""
    return value[:n]


def _account_label(email: str, account_id: str, alias: str = "") -> str:
    if alias:
        return alias
    masked = mask_email(email)
    suffix = _short_id(account_id)
    if masked and suffix:
        return f"{masked} · {suffix}"
    return masked or suffix or "unknown"


def _accounts_with_identity_runs(
    accounts: list[Account],
    provider: str,
    runs: list[tuple[str, str]],
) -> list[Account]:
    """Make every identity-ledger principal a first-class account.

    The identity ledger decides which accounts existed and when. Current auth
    and observed-account rows only enrich those principals with email, plan and
    authentication metadata. A historical principal therefore remains usable
    even after the provider has rotated it out of its current auth snapshot.
    """
    metadata = list(accounts)
    # Historical observed-account rows are metadata only. Current official
    # config/auth rows remain independently displayable, while historical
    # existence comes from the identity timeline below.
    out = [
        account
        for account in metadata
        if account.extra.get("account_source") != "official_observation"
    ]
    spans: dict[str, tuple[str, str]] = {}
    for at, identity in runs:
        who = str(identity or "")
        if not who:
            continue
        first, _ = spans.get(who, (str(at), str(at)))
        spans[who] = (first, str(at))

    for who, (first_at, last_at) in spans.items():
        hit_index = next(
            (
                index
                for index, account in enumerate(out)
                if account.account_id == who or (account.email and account.email == who)
            ),
            None,
        )
        metadata_hit = next(
            (
                account
                for account in metadata
                if account.account_id == who or (account.email and account.email == who)
            ),
            None,
        )
        if hit_index is not None or metadata_hit is not None:
            current = out[hit_index] if hit_index is not None else metadata_hit
            assert current is not None
            extra = dict(current.extra)
            extra["identity_source"] = f"official_{provider}_identity_ledger"
            extra["identity_source_official"] = True
            extra["identity_first_observed_at"] = first_at
            extra["identity_last_observed_at"] = last_at
            enriched = dataclass_replace(current, extra=extra)
            if hit_index is None:
                out.append(enriched)
            else:
                out[hit_index] = enriched
            continue
        out.append(
            Account(
                provider=provider,
                account_id=who,
                label=_account_label("", who),
                active=False,
                extra={
                    "account_source": "official_identity_ledger",
                    "account_source_grade": "C",
                    "account_source_official": True,
                    "account_metadata_incomplete": True,
                    "identity_source": f"official_{provider}_identity_ledger",
                    "identity_source_official": True,
                    "identity_first_observed_at": first_at,
                    "identity_last_observed_at": last_at,
                },
            )
        )
    return sorted(out, key=lambda account: (not account.active, account.label, account.account_id))


def load_claude_accounts() -> list[Account]:
    """One account per Claude config dir (CLAUDE_CONFIG_DIR may list several)."""
    by_id: dict[str, Account] = {}
    observed = _read_json(_claude_accounts_path())
    if isinstance(observed, list):
        for item in observed:
            if not isinstance(item, dict):
                continue
            account_id = str(item.get("account_id") or "")
            if not account_id:
                continue
            email = str(item.get("email") or "")
            by_id[account_id] = Account(
                provider="claude",
                account_id=account_id,
                email=email,
                label=_account_label(email, account_id),
                plan=str(item.get("plan") or ""),
                auth_mode=str(item.get("auth_mode") or ""),
                active=False,
                extra={
                    "account_source": "official_observation",
                    "account_source_grade": "C",
                    "account_source_official": True,
                    "first_observed_at": item.get("first_observed_at") or "",
                    "last_observed_at": item.get("last_observed_at") or "",
                },
            )
    current: list[Account] = []
    for home in claude_homes():
        account = _claude_account_at(home, active=not current)
        if account is None:
            continue
        current.append(account)
        by_id[account.account_id] = account
    return _accounts_with_identity_runs(
        list(by_id.values()), "claude", claude_identity_runs()
    )


def load_claude_account() -> Account | None:
    accounts = load_claude_accounts()
    return accounts[0] if accounts else None


def _claude_tier_display(raw: Any) -> str:
    """Strip default_/claude_ prefixes from a rateLimitTier value."""
    text = str(raw or "").strip()
    if not text or isinstance(raw, bool):
        return ""
    while True:
        lowered = text.lower()
        if lowered.startswith("default_"):
            text = text[len("default_") :]
            continue
        if lowered.startswith("claude_"):
            text = text[len("claude_") :]
            continue
        break
    return " ".join(text.replace("_", " ").split()).lower()


def _claude_plan_name(oauth: dict[str, Any] | None = None, profile: dict[str, Any] | None = None) -> str:
    """Plan badge: Max/Team variants live in rateLimitTier, not subscriptionType.

    subscriptionType is `max` / `team` / `pro`. 5x vs 20x is only in
    rateLimitTier (`default_claude_max_5x`). A generic `default_claude_ai`
    formats to `ai`, which is worse than `pro`, so named plans keep
    subscriptionType unless the tier actually distinguishes them.
    """
    oauth = oauth if isinstance(oauth, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    sub = str(oauth.get("subscriptionType") or profile.get("seatTier") or "").strip()
    tier = _claude_tier_display(
        oauth.get("rateLimitTier") or profile.get("organizationRateLimitTier") or ""
    )
    sub_norm = sub.lower().replace(" ", "").replace("_", "")
    if sub_norm in {"max", "team"}:
        return tier or sub
    if not sub:
        return tier
    if not tier or tier == "ai":
        return sub
    return tier


def _claude_account_at(home: Path, active: bool = True) -> Account | None:
    cred = _read_json(home / ".credentials.json") or {}
    oauth = cred.get("claudeAiOauth") if isinstance(cred, dict) else None
    cfg = _read_json(home / ".claude.json") or {}
    profile = cfg.get("oauthAccount") if isinstance(cfg, dict) else None
    if not isinstance(oauth, dict) and not isinstance(profile, dict):
        return None
    profile = profile if isinstance(profile, dict) else {}
    oauth = oauth if isinstance(oauth, dict) else {}
    email = str(profile.get("emailAddress") or "")
    account_id = str(profile.get("accountUuid") or cfg.get("userID") or email)
    if not account_id:
        return None
    plan = _claude_plan_name(oauth, profile)
    return Account(
        provider="claude",
        account_id=account_id,
        email=email,
        label=_account_label(email, account_id),
        plan=plan,
        auth_mode="oauth" if oauth else "unknown",
        active=active,
        extra={
            "organization": profile.get("organizationName") or "",
            "organization_uuid": profile.get("organizationUuid") or "",
            "billing_type": profile.get("billingType") or "",
            "rate_limit_tier": oauth.get("rateLimitTier") or profile.get("organizationRateLimitTier") or "",
            "has_token": bool(oauth.get("accessToken")),
            "config_dir": str(home),
            "account_source": "official_claude_config",
            "account_source_grade": "C",
            "account_source_official": True,
        },
    )


def _codex_plan_name(raw: Any) -> str:
    """WHAM/JWT `pro` is Pro 20x; `prolite` is the $100 Pro 5x plan."""
    if raw is None or isinstance(raw, bool):
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    compact = text.lower().replace(" ", "").replace("_", "").replace("-", "")
    if compact in {"prolite", "pro5x", "pro5"}:
        return "pro 5x"
    if compact in {"pro", "pro20x", "pro20"}:
        return "pro 20x"
    return text


def load_codex_accounts() -> list[Account]:
    """Official current login plus secret-free accounts observed by Sandglass.

    The public product deliberately ignores ``accounts/registry.json``. That file
    belongs to a third-party switcher and is not present on a normal Codex install.
    """
    current = _official_codex_account()
    observed = _read_json(_codex_accounts_path())
    by_id: dict[str, Account] = {}
    if isinstance(observed, list):
        for item in observed:
            if not isinstance(item, dict):
                continue
            account_id = str(item.get("account_id") or "")
            if not account_id:
                continue
            email = str(item.get("email") or "")
            by_id[account_id] = Account(
                provider="codex",
                account_id=account_id,
                email=email,
                label=_account_label(email, account_id),
                plan=_codex_plan_name(item.get("plan") or ""),
                auth_mode=str(item.get("auth_mode") or ""),
                active=False,
                extra={
                    "account_source": "official_observation",
                    "account_source_grade": "C",
                    "account_source_official": True,
                    "first_observed_at": item.get("first_observed_at") or "",
                    "last_observed_at": item.get("last_observed_at") or "",
                },
            )
    for account_id in _codex_legacy_roster_ids():
        if not account_id:
            continue
        existing = by_id.get(account_id)
        if existing is None:
            by_id[account_id] = Account(
                provider="codex",
                account_id=account_id,
                label=_account_label("", account_id),
                active=False,
                extra={
                    "account_source": "official_identity_ledger_roster",
                    "account_source_grade": "C",
                    "account_source_official": True,
                    "account_metadata_incomplete": True,
                },
            )
            continue
        # v1 proves that this principal belonged in the roster, but does not
        # prove ownership or replace the richer observed-account metadata.
        # Keep the observed email/label/plan/auth fields and change only the
        # source marker so the v2-free roster entry is not filtered out.
        extra = dict(existing.extra)
        extra["account_source"] = "official_identity_ledger_roster"
        extra["account_source_grade"] = "C"
        extra["account_source_official"] = True
        sources = set(extra.get("account_sources") or [])
        sources.update({"official_observation", "official_identity_ledger_roster"})
        extra["account_sources"] = sorted(source for source in sources if source)
        by_id[account_id] = dataclass_replace(existing, active=False, extra=extra)
    if current is not None:
        by_id[current.account_id] = current
    return _accounts_with_identity_runs(
        list(by_id.values()), "codex", codex_identity_runs()
    )


def _official_codex_account() -> Account | None:
    auth = _read_json(codex_home() / "auth.json") or {}
    if not isinstance(auth, dict) or not auth:
        return None
    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    payload = _jwt_payload(str(tokens.get("id_token") or ""))
    openai_auth = payload.get("https://api.openai.com/auth") if isinstance(payload.get("https://api.openai.com/auth"), dict) else {}
    email = str(payload.get("email") or "")
    account_id = str(openai_auth.get("chatgpt_account_id") or tokens.get("account_id") or "")
    if not account_id and not email:
        return None
    return Account(
        provider="codex",
        account_id=account_id or email,
        email=email,
        label=_account_label(email, account_id),
        plan=_codex_plan_name(openai_auth.get("chatgpt_plan_type") or ""),
        auth_mode=str(auth.get("auth_mode") or ""),
        active=True,
        extra={
            "has_token": bool(tokens.get("access_token")),
            "account_source": "official_auth",
            "account_source_grade": "C",
            "account_source_official": True,
        },
    )


def grok_profiles(auth: Any) -> list[dict[str, Any]]:
    """Every logged-in profile in ~/.grok/auth.json, the one the CLI would use first.

    auth.json is keyed by issuer::principal, so more than one login is normal.
    Shared with quota.fetch_grok_quota so the live snapshot and the account list
    can never disagree about which profile is in front.
    """
    if not isinstance(auth, dict):
        return []
    profiles: list[dict[str, Any]] = []
    for auth_key, value in auth.items():
        if not isinstance(value, dict):
            continue
        profile = dict(value)
        if not (profile.get("user_id") or profile.get("principal_id")):
            _, separator, principal = str(auth_key).rpartition("::")
            if separator and principal:
                profile["principal_id"] = principal
        if profile.get("email") or profile.get("user_id") or profile.get("principal_id") or profile.get("key"):
            profiles.append(profile)
    profiles.sort(key=lambda v: 0 if v.get("auth_mode") in {"oidc", "oauth", "session"} else 1)
    return profiles


def grok_account_id(profile: dict[str, Any]) -> str:
    return str(profile.get("user_id") or profile.get("principal_id") or profile.get("email") or "")


def _later_stamp(*values: Any) -> str:
    best = ""
    best_dt = None
    for value in values:
        dt = parse_ts(value)
        if dt is None:
            continue
        if best_dt is None or dt > best_dt:
            best, best_dt = (value if isinstance(value, str) and value else dt.isoformat()), dt
    return str(best or "")


def grok_auth_sources() -> list[dict[str, Any]]:
    """Unique Grok logins present in the official CLI auth file."""
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_emails: set[str] = set()

    def _add(
        profile: dict[str, Any],
        auth_path: Path,
        *,
        updated_at: str = "",
    ) -> None:
        account_id = grok_account_id(profile)
        email = str(profile.get("email") or "").strip()
        if not account_id:
            return
        email_key = email.lower()
        stamp = _later_stamp(updated_at, profile.get("create_time"), profile.get("expires_at"))
        if account_id in seen_ids or (email_key and email_key in seen_emails):
            for row in out:
                if row["account_id"] == account_id or (email_key and row["email"].lower() == email_key):
                    row["updated_at"] = _later_stamp(row.get("updated_at"), stamp)
                    row["create_time"] = _later_stamp(row.get("create_time"), profile.get("create_time"))
                    row["has_token"] = row.get("has_token") or bool(profile.get("key") or profile.get("refresh_token"))
                    break
            return
        if account_id:
            seen_ids.add(account_id)
        if email_key:
            seen_emails.add(email_key)
        out.append(
            {
                "account_id": account_id,
                "email": email,
                "auth_path": str(auth_path),
                "cli": True,
                "auth_mode": str(profile.get("auth_mode") or ""),
                "team_id": profile.get("team_id") or "",
                "principal_type": profile.get("principal_type") or "",
                "has_token": bool(profile.get("key") or profile.get("refresh_token")),
                "display_name": str(profile.get("first_name") or profile.get("displayName") or ""),
                "create_time": str(profile.get("create_time") or ""),
                "updated_at": stamp,
            }
        )

    cli_path = grok_home() / "auth.json"
    for profile in grok_profiles(_read_json(cli_path)):
        _add(profile, cli_path, updated_at=str(profile.get("create_time") or ""))
    return out


def load_grok_accounts() -> list[Account]:
    out: list[Account] = []
    for row in grok_auth_sources():
        email = str(row.get("email") or "")
        account_id = str(row.get("account_id") or "")
        last_used = str(row.get("updated_at") or row.get("create_time") or "")
        if row.get("cli"):
            try:
                mtime = Path(str(row.get("auth_path") or "")).stat().st_mtime
                last_used = _later_stamp(last_used, mtime)
            except OSError:
                pass
        extra = {
            "team_id": row.get("team_id") or "",
            "principal_type": row.get("principal_type") or "",
            "has_token": bool(row.get("has_token")),
            "auth_path": row.get("auth_path") or "",
            "account_source": "official_grok_cli",
            "account_source_grade": "C",
            "account_source_official": True,
            "last_used_at": last_used,
            "activated_at_ms": row.get("create_time") or last_used,
            "clients": [],
        }
        if row.get("cli"):
            extra["clients"].append("cli")
        out.append(
            Account(
                provider="grok",
                account_id=account_id,
                email=email,
                label=_account_label(email, account_id),
                plan="",
                auth_mode=str(row.get("auth_mode") or ""),
                active=bool(row.get("cli")) and not any(a.active for a in out),
                extra=extra,
            )
        )
    if out and not any(a.active for a in out):
        out[0] = Account(**{**out[0].as_dict(), "active": True})
    return _accounts_with_identity_runs(out, "grok", grok_identity_runs())


def load_grok_account() -> Account | None:
    accounts = load_grok_accounts()
    return accounts[0] if accounts else None


def load_user_source_accounts() -> list[Account]:
    """Accounts discovered by an admitted user adapter, never provider credentials."""

    from sandglass.user_sources import UserSourceStore

    store = UserSourceStore()
    settings = store.settings()
    quotas = {
        (
            str(row.get("source") or ""),
            str(row.get("provider") or ""),
            str(row.get("account_id") or ""),
        ): row
        for row in store.quotas()
    }
    out: list[Account] = []
    for row in store.accounts():
        source = str(row.get("source") or "")
        setting = settings.get(source) or {}
        if setting.get("accounts_enabled") is False:
            continue
        provider = str(row.get("provider") or "")
        account_id = str(row.get("account_id") or "")
        if not provider or not account_id:
            continue
        email = str(row.get("email") or "")
        quota = quotas.get((source, provider, account_id)) or {}
        extra = {
            "account_source": f"user_adapter:{source}",
            "account_source_grade": "U",
            "account_source_official": False,
            "adapter_source": source,
            "source_version": str(row.get("source_version") or ""),
            "first_observed_at": str(row.get("first_observed_at") or ""),
            "last_observed_at": str(row.get("last_observed_at") or ""),
        }
        if quota:
            extra.update(
                {
                    "windows": list(quota.get("windows") or []),
                    "quota_source": f"user_adapter:{source}",
                    "quota_fetched_at": str(quota.get("fetched_at") or ""),
                    "quota_source_version": str(quota.get("source_version") or ""),
                }
            )
        out.append(
            Account(
                provider=provider,
                account_id=account_id,
                email=email,
                label=str(row.get("label") or "")
                or _account_label(email, account_id),
                plan=str(quota.get("plan") or row.get("plan") or ""),
                auth_mode="user_adapter",
                active=False,
                extra=extra,
            )
        )
    return out


def load_accounts() -> list[Account]:
    official: list[Account] = []
    official.extend(load_claude_accounts())
    official.extend(load_codex_accounts())
    official.extend(load_grok_accounts())
    by_key = {(account.provider, account.account_id): account for account in official}
    for adapter_account in load_user_source_accounts():
        key = (adapter_account.provider, adapter_account.account_id)
        current = by_key.get(key)
        if current is None:
            by_key[key] = adapter_account
            continue
        extra = dict(current.extra)
        if current.extra.get("account_source_official") is False:
            sources = set(extra.get("account_sources") or [])
            sources.add(str(current.extra.get("account_source") or ""))
            sources.add(str(adapter_account.extra.get("account_source") or ""))
            sources = {source for source in sources if source and source != "user_adapter:mixed"}
            current_quota_at = parse_ts(current.extra.get("quota_fetched_at"))
            new_quota_at = parse_ts(adapter_account.extra.get("quota_fetched_at"))
            base = (
                adapter_account
                if new_quota_at is not None
                and (current_quota_at is None or new_quota_at > current_quota_at)
                else current
            )
            merged_extra = dict(base.extra)
            merged_extra["account_sources"] = sorted(sources)
            merged_extra["account_source"] = (
                next(iter(sources)) if len(sources) == 1 else "user_adapter:mixed"
            )
            by_key[key] = dataclass_replace(base, extra=merged_extra)
            continue
        supplemental = set(extra.get("supplemental_account_sources") or [])
        supplemental.add(str(adapter_account.extra.get("account_source") or ""))
        extra["supplemental_account_sources"] = sorted(source for source in supplemental if source)
        if not extra.get("windows") and adapter_account.extra.get("windows"):
            for field in (
                "windows",
                "quota_source",
                "quota_fetched_at",
                "quota_source_version",
            ):
                if field in adapter_account.extra:
                    extra[field] = adapter_account.extra[field]
        metadata_incomplete = bool(extra.get("account_metadata_incomplete"))
        supplemented_email = current.email or (
            adapter_account.email if metadata_incomplete else ""
        )
        supplemented_label = current.label
        if metadata_incomplete and adapter_account.email:
            supplemented_label = adapter_account.label or _account_label(
                adapter_account.email, current.account_id
            )
            extra["account_metadata_incomplete"] = False
        by_key[key] = dataclass_replace(
            current,
            email=supplemented_email,
            label=supplemented_label,
            plan=current.plan or adapter_account.plan,
            extra=extra,
        )
    order = {provider: index for index, provider in enumerate(PROVIDERS)}
    return sorted(
        by_key.values(),
        key=lambda account: (
            order.get(account.provider, len(order)),
            account.provider,
            not account.active,
            account.label,
            account.account_id,
        ),
    )


_GROK_RUNS: list[tuple[str, str]] | None = None
_GROK_RUNS_SRC: tuple | None = None


def _path_stamp(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return 0, 0


def _owned_path_stamp(path: Path) -> tuple[int, int, str]:
    """Fingerprint Sandglass state by content, not metadata alone."""
    try:
        with path.open("rb") as handle:
            payload = handle.read()
            stat = os.fstat(handle.fileno())
    except OSError:
        return 0, 0, ""
    return stat.st_mtime_ns, len(payload), hashlib.sha256(payload).hexdigest()


_LOCKFILE_EXCLUSIVE_LOCK = 0x0002
_LOCKED_BYTES = 1
_WINDOWS_LOCK_API: tuple | None = None


def _windows_lock_api():
    """LockFileEx/UnlockFileEx, prepared once and kept."""
    global _WINDOWS_LOCK_API
    if _WINDOWS_LOCK_API is None:
        import ctypes
        from ctypes import wintypes

        class Overlapped(ctypes.Structure):
            _fields_ = [
                ("Internal", ctypes.c_void_p),
                ("InternalHigh", ctypes.c_void_p),
                ("Offset", wintypes.DWORD),
                ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LockFileEx.restype = wintypes.BOOL
        kernel.LockFileEx.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped),
        ]
        kernel.UnlockFileEx.restype = wintypes.BOOL
        kernel.UnlockFileEx.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, ctypes.POINTER(Overlapped),
        ]
        _WINDOWS_LOCK_API = (ctypes, kernel, Overlapped)
    return _WINDOWS_LOCK_API


def _windows_lock(fileno: int, *, take: bool) -> None:
    """Hold or release byte 0 of an open file, waiting for as long as it takes.

    msvcrt.locking(LK_LOCK) reads as a blocking lock and is not one: the CRT
    retries once a second and raises OSError(36, "Resource deadlock avoided")
    on the tenth failure. Measured here, a holder that keeps the byte for
    thirteen seconds made the next acquirer fail at 9.07 -- while the POSIX
    branch below waits indefinitely. The two platforms did not promise the same
    thing, and the one that ships is the one that gives up.

    Nothing catches it either. Every caller treats this as a lock that waits,
    so the identity ledgers drop the write, and the observer takes the same lock
    inside its heartbeat loop with no handler above it: the observation process
    exits, leaving the coverage ledger describing a run that is still open.

    LockFileEx without LOCKFILE_FAIL_IMMEDIATELY waits in the kernel until the
    holder releases. There is no retry count and no interval to tune.
    """
    ctypes, kernel, overlapped_type = _windows_lock_api()
    import msvcrt

    region = overlapped_type()
    handle = msvcrt.get_osfhandle(fileno)
    if take:
        ok = kernel.LockFileEx(handle, _LOCKFILE_EXCLUSIVE_LOCK, 0,
                               _LOCKED_BYTES, 0, ctypes.byref(region))
    else:
        ok = kernel.UnlockFileEx(handle, 0, _LOCKED_BYTES, 0, ctypes.byref(region))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def state_file_lock(path: Path, *, lock_name: str = ".identity-ledgers.lock"):
    """Serialize read/modify/write of a Sandglass state file across processes.

    Identity ledgers were the first files to need this; the observer's coverage
    ledger needs it for the same reason, and named it after the first caller
    would have meant either a misleading name or a second implementation.

    Acquiring waits. On neither platform does it give up -- so a caller that
    reports a failure must not queue behind the write that is failing.
    ``lock_name`` exists for exactly that: the runtime diagnostics file is
    written from except handlers anywhere in the program, including ones
    reached while another state file is held, and it takes its own lock so the
    report never waits on the thing it is reporting. Every ledger that shares a
    consistency requirement keeps sharing the default lock.
    """
    lock_path = path.parent / lock_name
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            _windows_lock(handle.fileno(), take=True)
            try:
                yield
            finally:
                _windows_lock(handle.fileno(), take=False)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_json_if_changed(path: Path, payload: Any) -> bool:
    """Write only when the content differs. False means it already matched.

    False used to mean that *or* that the write failed, and callers cannot
    tell those apart -- so on a full disk, or with this one file locked while
    the state directory stays writable, every identity ledger stopped being
    written and each caller read it as "nothing to record". The reporting
    added above this function could not see it either: there was no exception
    to catch. Measured: with tempfile raising ENOSPC, note_current_identity
    returned False, recorded no component failure, and wrote nothing.

    A failure is therefore raised. The one caller that must keep answering --
    the CLI merge inside grok_identity_runs(), which is a read -- records it
    and carries on.
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except OSError:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return True


def _identity_writer_path(path: Path) -> Path:
    return path.parent / "identity-ledger-last-writes.json"


def _record_identity_write(path: Path, reason: str) -> None:
    """Record who wrote a ledger without storing account identities."""
    try:
        payload = path.read_bytes()
        stat = path.stat()
        source_payload = Path(__file__).read_bytes()
    except OSError:
        return
    writer_path = _identity_writer_path(path)
    stored = _read_owned_json(writer_path)
    if stored is _OWNED_JSON_MISSING:
        rows: dict[str, dict] = {}
    elif isinstance(stored, dict):
        rows = dict(stored)
    else:
        return
    rows[path.name] = {
        "written_at": datetime.now(timezone.utc).isoformat(),
        "process_started_at": _IDENTITY_PROCESS_STARTED_AT,
        "process_id": os.getpid(),
        "reason": reason,
        "file_sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "mtime_ns": stat.st_mtime_ns,
        "accounts_source_sha256": hashlib.sha256(source_payload).hexdigest(),
    }
    try:
        _write_json_if_changed(writer_path, rows)
    except OSError:
        # The ledger write this describes already succeeded. Losing its
        # receipt is worth nothing to a caller that is about to report a
        # failed ledger write, so it must not be raised as one.
        return


def identity_write_evidence(path: Path) -> dict:
    """Return the non-identifying last-writer record for one ledger."""
    stored = _read_owned_json(_identity_writer_path(path))
    if not isinstance(stored, dict):
        return {}
    item = stored.get(path.name)
    if not isinstance(item, dict):
        return {}
    allowed = {
        "written_at",
        "process_started_at",
        "process_id",
        "reason",
        "file_sha256",
        "bytes",
        "mtime_ns",
        "accounts_source_sha256",
    }
    return {key: item.get(key) for key in allowed if key in item}


def _valid_identity_rows(rows: object, key: str) -> bool:
    return isinstance(rows, list) and all(
        isinstance(item, dict)
        and parse_ts(item.get("at")) is not None
        and bool(str(item.get(key) or ""))
        for item in rows
    )


def _read_identity_rows(path: Path, key: str) -> tuple[list, bool]:
    """Read a Sandglass-owned identity ledger without caching read failures."""
    stored = _read_owned_json(path)
    if _valid_identity_rows(stored, key):
        return stored, True
    return [], stored is _OWNED_JSON_MISSING


def _grok_runs_path() -> Path:
    # The legacy filename mixed official CLI events with a community Grok App's
    # profile-switch log. It cannot be separated safely, so never migrate it.
    return meter_home() / "grok-official-identity-runs.json"


def _grok_runs_source_stamp() -> tuple:
    """Every direct or retained source that can change the Grok identity timeline."""
    auth = _read_json(grok_home() / "auth.json")
    profiles = grok_profiles(auth)
    principal = grok_account_id(profiles[0]) if profiles else ""
    return (
        principal,
        _path_stamp(grok_home() / "logs" / "unified.jsonl"),
        _owned_path_stamp(_grok_runs_path()),
    )


def grok_identity_runs() -> list[tuple[str, str]]:
    """When each identity took over ~/.grok, oldest first.

    The CLI log records `auth init user_info check` with the account's user_id, which
    is the only hard evidence on disk of who was signed in when. The log rotates, so
    every run ever seen is merged into a ledger and kept.
    """
    global _GROK_RUNS, _GROK_RUNS_SRC
    with _IDENTITY_LOCK:
        src = _grok_runs_source_stamp()
        if _GROK_RUNS is not None and _GROK_RUNS_SRC == src:
            return _GROK_RUNS
        cli: dict[str, str] = {}
        log = grok_home() / "logs" / "unified.jsonl"
        try:
            with log.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if "user_info check" not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict) or row.get("msg") != "auth init user_info check":
                        continue
                    user_id = str((row.get("ctx") or {}).get("user_id") or "")
                    stamp = parse_ts(row.get("ts"))
                    if user_id and stamp is not None:
                        cli[stamp.isoformat()] = user_id
        except OSError:
            pass
        path = _grok_runs_path()
        with state_file_lock(path):
            seen: dict[str, str] = {}
            stored, ledger_read = _read_identity_rows(path, "account_id")
            if not ledger_read:
                return _GROK_RUNS if _GROK_RUNS is not None else []
            for item in stored:
                if isinstance(item, dict) and item.get("at") and item.get("account_id"):
                    seen[str(item["at"])] = str(item["account_id"])
            runs = _merge_runs(seen, cli)
            if runs:
                try:
                    changed = _write_json_if_changed(
                        path,
                        [{"at": at, "account_id": acc} for at, acc in runs],
                    )
                except OSError as exc:
                    # This is a read. The merge is already in memory and the
                    # answer is the same either way; a panel request must not
                    # die because the merged ledger could not be persisted.
                    from sandglass.diagnostics import record_component_failure

                    record_component_failure("grok_identity_merge_write", exc)
                else:
                    if changed:
                        _record_identity_write(path, "official_grok_cli_merge")
        _GROK_RUNS = runs
        # The stamp taken before the read, not a fresh one taken after it.
        # These ledgers have a second writer: note_current_identity() appends a
        # switch from the observer process while the panel is reading here. A
        # stamp taken afterwards describes the file that writer left behind, so
        # the run list parsed from the file before it gets stored under the new
        # file's identity and every later call is a hit -- the switch never
        # arrives, and every minute after it keeps being credited to the account
        # that came before, for as long as the ledger stays untouched. Stamping
        # first turns that into one wasted re-read instead.
        _GROK_RUNS_SRC = src
        return runs


def _collapse(entries) -> list[tuple[str, str]]:
    """Sorted entries into runs: one per change of identity."""
    runs: list[tuple[str, str]] = []
    for at, who in sorted(entries):
        if runs and runs[-1][1] == who:
            continue
        runs.append((at, who))
    return runs


def _merge_runs(other: dict, cli: dict) -> list[tuple[str, str]]:
    """The CLI's own log decides inside its coverage.

    A request bills whichever account the CLI is holding credentials for, so a run
    begins where the CLI's log says the identity changed. Sandglass's retained
    observations may cover an earlier interval, but can never override a CLI event.
    """
    cli_runs = _collapse(cli.items())
    if not cli_runs:
        return _collapse(other.items())
    # Only the stretch the CLI log cannot speak about. Where a run spans that
    # edge -- same account before and after -- the earlier start is the true one,
    # so collapsing keeps it and drops the CLI line that merely repeats it.
    first = cli_runs[0][0]
    earlier = [(at, who) for at, who in other.items() if at < first]
    return _collapse(earlier + cli_runs)


def _grok_owner_at(runs: list[tuple[str, str]], moment) -> str:
    owner = ""
    for at, account_id in runs:
        stamp = parse_ts(at)
        if stamp is None or stamp > moment:
            break
        owner = account_id
    return owner


def _grok_owner_from_runs(record: SessionRecord, runs: list[tuple[str, str]]) -> str:
    """The identity that produced most of this session's tokens."""
    if not runs or not record.timeline:
        return ""
    weights: dict[str, int] = {}
    for at, delta in record.timeline:
        moment = parse_ts(at)
        if moment is None:
            continue
        owner = _grok_owner_at(runs, moment)
        if owner:
            weights[owner] = weights.get(owner, 0) + delta.total_tokens + 1
    if not weights:
        return ""
    return max(weights.items(), key=lambda item: item[1])[0]


def assign_grok_account(record: SessionRecord, accounts: list[Account]) -> SessionRecord:
    """Assign Grok only when a first-party identity event proves ownership.

    Sessions before the first verifiable official CLI identity event stay unassigned.
    The currently signed-in account is never used to guess or backfill history.
    """
    if not accounts:
        return record
    # Hard evidence first: the CLI log says who was signed in minute by minute.
    from_log = _grok_owner_from_runs(record, grok_identity_runs())
    if from_log:
        hit = next((account for account in accounts if account.account_id == from_log), None)
        if hit is not None:
            return replace_account(record, hit)
        return dataclass_replace(record, account_id=from_log, account_label=from_log[:12])
    return record


_CLAUDE_RUNS: list[tuple[str, str]] | None = None
_CLAUDE_RUNS_SRC: tuple | None = None


def _claude_runs_path() -> Path:
    return meter_home() / "claude-official-identity-runs.json"


def _claude_accounts_path() -> Path:
    return meter_home() / "claude-observed-accounts.json"


def _claude_runs_source_stamp() -> tuple:
    return (
        tuple(
            (_path_stamp(home / ".credentials.json"), _path_stamp(home / ".claude.json"))
            for home in claude_homes()
        ),
        _owned_path_stamp(_claude_runs_path()),
        _owned_path_stamp(_claude_accounts_path()),
    )


def claude_identity_runs() -> list[tuple[str, str]]:
    """When each observed Claude Code identity became current after intervention."""
    global _CLAUDE_RUNS, _CLAUDE_RUNS_SRC
    with _IDENTITY_LOCK:
        src = _claude_runs_source_stamp()
        if _CLAUDE_RUNS is not None and _CLAUDE_RUNS_SRC == src:
            return _CLAUDE_RUNS
        stored, ledger_read = _read_identity_rows(_claude_runs_path(), "account_id")
        if not ledger_read:
            return _CLAUDE_RUNS if _CLAUDE_RUNS is not None else []
        runs = _collapse(
            (str(item["at"]), str(item["account_id"]))
            for item in stored
            if isinstance(item, dict) and item.get("at") and item.get("account_id")
        )
        _CLAUDE_RUNS = runs
        # The stamp taken before the read, not a fresh one taken after it.
        # These ledgers have a second writer: note_current_identity() appends a
        # switch from the observer process while the panel is reading here. A
        # stamp taken afterwards describes the file that writer left behind, so
        # the run list parsed from the file before it gets stored under the new
        # file's identity and every later call is a hit -- the switch never
        # arrives, and every minute after it keeps being credited to the account
        # that came before, for as long as the ledger stays untouched. Stamping
        # first turns that into one wasted re-read instead.
        _CLAUDE_RUNS_SRC = src
        return runs


def _claude_owner_at(runs: list[tuple[str, str]], moment) -> str:
    owner = ""
    for at, account_id in runs:
        stamp = parse_ts(at)
        if stamp is None or stamp > moment:
            break
        owner = account_id
    return owner


_CODEX_RUNS: list[tuple[str, str, str, str]] | None = None
_CODEX_RUNS_SRC: tuple | None = None
_CODEX_MALFORMED_CUTOFF: str | None = None


def _codex_runs_path() -> Path:
    # The legacy filename may contain timestamps copied from a third-party
    # switcher. Never promote those rows into the official-only public product.
    return meter_home() / "codex-official-identity-runs.json"


def _codex_legacy_roster_ids() -> list[str]:
    """Read v1 names for roster discovery without exposing them to ownership."""
    stored = _read_owned_json(_codex_runs_path())
    if not _valid_identity_rows(stored, "to"):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in stored:
        account_id = str(item["to"]).strip()
        if account_id and account_id not in seen:
            seen.add(account_id)
            out.append(account_id)
    return out


def _codex_identity_events_path() -> Path:
    """Return the v2 coverage-aware Codex identity stream."""
    return meter_home() / "codex-official-identity-events-v2.json"


def _codex_accounts_path() -> Path:
    return meter_home() / "codex-observed-accounts.json"


def _codex_runs_source_stamp() -> tuple:
    return (
        _owned_path_stamp(_codex_runs_path()),
        _owned_path_stamp(_codex_identity_events_path()),
        _owned_path_stamp(_codex_accounts_path()),
    )


def identity_source_stamp() -> tuple:
    """Bust long-lived serve caches when a provider identity switch lands."""
    return (
        _claude_runs_source_stamp(),
        _codex_runs_source_stamp(),
        _grok_runs_source_stamp(),
    )


def _strict_utc_at(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return stamp.tzinfo is not None and stamp.utcoffset() == timezone.utc.utcoffset(stamp)


def _valid_codex_identity_events(value: object) -> bool:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "events"}
        or value.get("schema") != _CODEX_IDENTITY_EVENTS_SCHEMA
    ):
        return False
    events = value.get("events")
    if not isinstance(events, list):
        return False
    previous = None
    for event in events:
        if not isinstance(event, dict) or not _strict_utc_at(event.get("at")):
            return False
        at = parse_ts(event["at"])
        if at is None or (previous is not None and at <= previous):
            return False
        previous = at
        kind = event.get("kind")
        if kind == _CODEX_EVENT_OBSERVED:
            if set(event) != {"at", "kind", "account_id"}:
                return False
            if not isinstance(event.get("account_id"), str) or not event["account_id"].strip():
                return False
        elif kind == _CODEX_EVENT_UNASSIGNED:
            if set(event) != {"at", "kind", "reason"}:
                return False
            if not isinstance(event.get("reason"), str) or not event["reason"].strip():
                return False
        else:
            return False
    return True


def codex_identity_events() -> list[dict[str, str]]:
    """Read the v2 stream; malformed existing state fails closed."""
    global _CODEX_RUNS, _CODEX_RUNS_SRC, _CODEX_MALFORMED_CUTOFF
    with _IDENTITY_LOCK:
        src = _codex_runs_source_stamp()
        if _CODEX_RUNS is not None and _CODEX_RUNS_SRC == src:
            events = []
            for at, who, kind, reason in _CODEX_RUNS:
                event = {"at": at, "kind": kind}
                if kind == _CODEX_EVENT_OBSERVED:
                    event["account_id"] = who
                else:
                    event["reason"] = reason
                events.append(event)
            return events
        stored = _read_owned_json(_codex_identity_events_path())
        if stored is _OWNED_JSON_MISSING:
            events: list[dict[str, str]] = []
            # A missing/empty baseline starts a new evidence stream.  Do not
            # carry a synthetic invalid-ledger boundary from an older source
            # incarnation into this clean state.
            _CODEX_MALFORMED_CUTOFF = None
        elif _valid_codex_identity_events(stored):
            events = [dict(event) for event in stored["events"]]
            _CODEX_MALFORMED_CUTOFF = None
        else:
            # A torn external replacement must not erase the last complete
            # timeline already held by this process. Keep the cache usable,
            # but leave its source stamp unchanged so a later valid replace is
            # re-read immediately.
            if _CODEX_RUNS is None:
                _CODEX_MALFORMED_CUTOFF = None
                return []
            if _CODEX_MALFORMED_CUTOFF is None:
                last = parse_ts(_CODEX_RUNS[-1][0]) if _CODEX_RUNS else None
                now = datetime.now(timezone.utc)
                boundary = last + timedelta(microseconds=1) if last else now
                _CODEX_MALFORMED_CUTOFF = max(now, boundary).isoformat()
            events = [
                {
                    "at": at,
                    "kind": kind,
                    **(
                        {"account_id": who}
                        if kind == _CODEX_EVENT_OBSERVED
                        else {"reason": reason}
                    ),
                }
                for at, who, kind, reason in _CODEX_RUNS
            ]
            events.append({
                "at": _CODEX_MALFORMED_CUTOFF,
                "kind": _CODEX_EVENT_UNASSIGNED,
                "reason": "identity_ledger_invalid",
            })
            return events
        _CODEX_RUNS = [
            (
                event["at"],
                event.get("account_id", ""),
                event["kind"],
                event.get("reason", ""),
            )
            for event in events
        ]
        # The stamp taken before the read, not a fresh one taken after it.
        # These ledgers have a second writer: note_current_identity() appends a
        # switch from the observer process while the panel is reading here. A
        # stamp taken afterwards describes the file that writer left behind, so
        # the run list parsed from the file before it gets stored under the new
        # file's identity and every later call is a hit -- the switch never
        # arrives, and every minute after it keeps being credited to the account
        # that came before, for as long as the ledger stays untouched. Stamping
        # first turns that into one wasted re-read instead.
        _CODEX_RUNS_SRC = src
        return events


def codex_identity_runs() -> list[tuple[str, str]]:
    """Compatibility projection; v1 rows are deliberately not consulted."""
    return [
        (event["at"], event.get("account_id", ""))
        for event in codex_identity_events()
    ]


def _codex_owner_at(runs: list[tuple[str, str]], moment) -> str:
    owner = ""
    for at, who in runs:
        stamp = parse_ts(at)
        if stamp is None or stamp > moment:
            break
        owner = who
    return owner


def _codex_owner_from_runs(record: SessionRecord, runs: list[tuple[str, str]]) -> str:
    """The identity that produced most of this session, by the switch timeline."""
    if not runs or not record.timeline:
        return ""
    weights: dict[str, int] = {}
    for at, delta in record.timeline:
        moment = parse_ts(at)
        if moment is None:
            continue
        owner = _codex_owner_at(runs, moment)
        if owner:
            weights[owner] = weights.get(owner, 0) + delta.total_tokens + 1
    if not weights:
        return ""
    return max(weights.items(), key=lambda item: item[1])[0]


def assign_codex_account(record: SessionRecord, accounts: list[Account]) -> SessionRecord:
    """Assign only when a local identity event or session link proves ownership."""
    if not accounts:
        return record
    # The switch timeline beats the rollout path: a rollout file outlives account
    # switches, so its last claimant can be a day stale, while a switch record says
    # exactly who was signed in at the minute the work happened.
    who = _codex_owner_from_runs(record, codex_identity_runs())
    if who:
        hit = next((a for a in accounts if a.email and a.email == who), None)
        if hit is None:
            hit = next((a for a in accounts if a.account_id == who), None)
        if hit is not None:
            # This label summarizes which observed identity owns most of the
            # session timeline.  A rollout can span account switches, so it is
            # not direct identity evidence for every minute in the session.
            # Keep the projection for legacy/session views, but mark its scope
            # so exact minute evidence can supplement it instead of being
            # rejected as a conflicting first-party identity.
            extra = dict(record.extra)
            extra["account_identity_scope"] = "session_timeline_majority"
            extra["account_identity_source"] = "sandglass_official_observation_runs"
            return replace_account(dataclass_replace(record, extra=extra), hit)
    # Current auth is only a snapshot. Without a Sandglass observation interval or
    # identity-bearing official telemetry, older rollout history stays unassigned.
    return record


def _claude_signed_in() -> tuple[str, str, Account | None]:
    """Current official Claude Code identity stamped when Observer sees it."""
    account = next(
        (
            found
            for index, home in enumerate(claude_homes())
            if (found := _claude_account_at(home, active=index == 0)) is not None
            and found.active
        ),
        None,
    )
    if account is None:
        return "", "", None
    return datetime.now(timezone.utc).isoformat(), account.account_id, account


def _remember_claude_account(account: Account, observed_at: str) -> bool:
    """Persist a secret-free Claude account observed in official local state."""
    path = _claude_accounts_path()
    with state_file_lock(path):
        raw = _read_owned_json(path)
        if raw is _OWNED_JSON_MISSING:
            rows = []
        elif isinstance(raw, list) and all(
            isinstance(item, dict) and bool(str(item.get("account_id") or ""))
            for item in raw
        ):
            rows = raw
        else:
            return False
        by_id = {
            str(item.get("account_id")): dict(item)
            for item in rows
            if isinstance(item, dict) and item.get("account_id")
        }
        previous = by_id.get(account.account_id, {})
        by_id[account.account_id] = {
            "account_id": account.account_id,
            "email": account.email,
            "plan": account.plan,
            "auth_mode": account.auth_mode,
            "first_observed_at": previous.get("first_observed_at") or observed_at,
            "last_observed_at": observed_at,
        }
        changed = _write_json_if_changed(
            path, sorted(by_id.values(), key=lambda item: item["account_id"])
        )
        if changed:
            _record_identity_write(path, "official_claude_account_observation")
        return changed


def _codex_signed_in() -> tuple[str, str]:
    """Current official Codex identity and the successful observation time."""
    account = _official_codex_account()
    if account is None:
        return datetime.now(timezone.utc).isoformat(), ""
    return datetime.now(timezone.utc).isoformat(), account.account_id


def _remember_codex_account(account: Account, observed_at: str) -> bool:
    """Persist only a secret-free snapshot of an officially observed account."""
    path = _codex_accounts_path()
    with state_file_lock(path):
        raw = _read_owned_json(path)
        if raw is _OWNED_JSON_MISSING:
            rows = []
        elif isinstance(raw, list) and all(
            isinstance(item, dict) and bool(str(item.get("account_id") or ""))
            for item in raw
        ):
            rows = raw
        else:
            return False
        by_id = {
            str(item.get("account_id")): dict(item)
            for item in rows
            if isinstance(item, dict) and item.get("account_id")
        }
        previous = by_id.get(account.account_id, {})
        by_id[account.account_id] = {
            "account_id": account.account_id,
            "email": account.email,
            "plan": account.plan,
            "auth_mode": account.auth_mode,
            "first_observed_at": previous.get("first_observed_at") or observed_at,
            "last_observed_at": previous.get("last_observed_at") or observed_at,
        }
        changed = _write_json_if_changed(
            path, sorted(by_id.values(), key=lambda item: item["account_id"])
        )
        if changed:
            _record_identity_write(path, "official_codex_account_observation")
        return changed


def _grok_signed_in() -> tuple[str, str]:
    """Current official Grok CLI identity stamped when Sandglass observes it."""
    profiles = grok_profiles(_read_json(grok_home() / "auth.json"))
    who = grok_account_id(profiles[0]) if profiles else ""
    return (datetime.now(timezone.utc).isoformat(), who) if who else ("", "")


def _append_run(path: Path, key: str, at: str, who: str, runs: list[tuple[str, str]]) -> bool:
    """Add one entry using the locked disk ledger as the sole authority."""
    if not who or not at:
        return False
    del runs  # A stale process cache must never resurrect rows absent from disk.
    with state_file_lock(path):
        stored = _read_owned_json(path)
        if stored is not _OWNED_JSON_MISSING and not _valid_identity_rows(stored, key):
            return False
        merged: dict[str, str] = {}
        if isinstance(stored, list):
            for item in stored:
                if isinstance(item, dict) and item.get("at") and item.get(key):
                    merged[str(item["at"])] = str(item[key])
        current = _collapse(merged.items())
        if current and (current[-1][1] == who or at <= current[-1][0]):
            return False
        entries = [{"at": stamp, key: name} for stamp, name in current]
        entries.append({"at": at, key: who})
        changed = _write_json_if_changed(path, entries)
        if changed:
            reason = (
                "official_codex_identity_observation"
                if key == "to"
                else (
                    "official_claude_identity_observation"
                    if path == _claude_runs_path()
                    else "official_grok_identity_observation"
                )
            )
            _record_identity_write(path, reason)
        return changed


def _append_codex_identity_event(
    at: str,
    *,
    account_id: str = "",
    kind: str = _CODEX_EVENT_OBSERVED,
    reason: str = "",
) -> bool:
    """Serialize Codex observed/gap events within this process as well as on disk."""
    with _IDENTITY_LOCK:
        return _append_codex_identity_event_unlocked(
            at, account_id=account_id, kind=kind, reason=reason
        )


def _append_codex_identity_event_unlocked(
    at: str,
    *,
    account_id: str = "",
    kind: str = _CODEX_EVENT_OBSERVED,
    reason: str = "",
) -> bool:
    """Atomically append one validated v2 event, with same-state dedupe."""
    if not _strict_utc_at(at) or kind not in {
        _CODEX_EVENT_OBSERVED,
        _CODEX_EVENT_UNASSIGNED,
    }:
        return False
    account_id = str(account_id or "").strip()
    reason = str(reason or "").strip()
    if kind == _CODEX_EVENT_OBSERVED and not account_id:
        return False
    if kind == _CODEX_EVENT_UNASSIGNED and not reason:
        return False
    path = _codex_identity_events_path()
    with state_file_lock(path):
        stored = _read_owned_json(path)
        if stored is _OWNED_JSON_MISSING:
            events: list[dict[str, str]] = []
        elif _valid_codex_identity_events(stored):
            events = [dict(event) for event in stored["events"]]
        else:
            # Preserve damaged state; extending it would destroy the evidence
            # that the reader must report as untrusted.
            return False
        if events:
            last = events[-1]
            last_kind = last["kind"]
            last_state = (
                account_id if kind == _CODEX_EVENT_OBSERVED else ""
            )
            previous_state = (
                str(last.get("account_id") or "")
                if last_kind == _CODEX_EVENT_OBSERVED
                else ""
            )
            if last_kind == kind and previous_state == last_state:
                return False
            if parse_ts(at) <= parse_ts(last["at"]):
                return False
        event: dict[str, str] = {"at": at, "kind": kind}
        if kind == _CODEX_EVENT_OBSERVED:
            event["account_id"] = account_id
        else:
            event["reason"] = reason
        changed = _write_json_if_changed(
            path,
            {"schema": _CODEX_IDENTITY_EVENTS_SCHEMA, "events": [*events, event]},
        )
        if changed:
            _record_identity_write(path, "official_codex_identity_event")
            global _CODEX_RUNS, _CODEX_RUNS_SRC, _CODEX_MALFORMED_CUTOFF
            _CODEX_RUNS = _CODEX_RUNS_SRC = None
            _CODEX_MALFORMED_CUTOFF = None
        return changed


def note_codex_identity_gap(
    at: str | None = None,
    reason: str = "observer_coverage_gap",
) -> bool:
    """Record an explicit unassigned boundary after coverage is lost."""
    return _append_codex_identity_event(
        at or datetime.now(timezone.utc).isoformat(),
        kind=_CODEX_EVENT_UNASSIGNED,
        reason=reason,
    )


def note_codex_identity(observed_at: str | None = None) -> bool:
    """Observe Codex in isolation so another provider cannot close its interval.

    One except used to cover both the vendor read and Sandglass's own writes,
    and wrote the same coverage gap for either. Those are different facts. Not
    being able to see who is signed in means the minutes that follow have no
    owner anyone could name -- an honest gap. Not being able to record what we
    did see is Sandglass being broken, and a gap says nothing about that; worse,
    the gap is written through the same path that just failed, so it usually
    does not land either, and the whole event goes by in silence.

    So the two are separated. The read still becomes a gap. A failure of our own
    is recorded where a broken state directory cannot swallow it, because
    otherwise a permissions error or a redirected state home takes Codex
    attribution to zero and nothing anywhere says so.
    """
    from sandglass.diagnostics import (clear_component_failure,
                                       record_component_failure)

    with _IDENTITY_LOCK:
        try:
            at, who = _codex_signed_in()
            current = _official_codex_account()
        except Exception:  # noqa: BLE001 - an unreadable provider is a real gap
            return note_codex_identity_gap(
                observed_at, reason="identity_read_failed"
            )
        if observed_at:
            at = observed_at
        try:
            changed = False
            if current is not None and current.account_id == who and at:
                changed = _remember_codex_account(current, at)
            if who:
                changed = _append_codex_identity_event(
                    at,
                    account_id=who,
                    kind=_CODEX_EVENT_OBSERVED,
                ) or changed
            else:
                changed = note_codex_identity_gap(
                    at, reason="identity_read_empty"
                ) or changed
        except Exception as exc:  # noqa: BLE001 - ours to report, not to bury
            record_component_failure("codex_identity_write", exc)
            raise
        clear_component_failure("codex_identity_write")
        return changed


def note_current_identity(
    observed_at: str | None = None,
    *,
    record_codex_attribution: bool = True,
    observe_codex: bool | None = None,
) -> bool:
    """Copy first-party sign-in stamps into Sandglass's attribution ledgers.

    Desktop startup may persist a secret-free Codex roster snapshot while the
    observer remains the owner of Codex attribution events.

    ``observe_codex`` remains as a compatibility alias for callers from the
    v1 wiring; new callers should use the positive ``record_codex_attribution``
    name.
    """
    from sandglass.diagnostics import (clear_component_failure,
                                       record_component_failure)

    global _CLAUDE_RUNS, _CLAUDE_RUNS_SRC
    global _CODEX_RUNS, _CODEX_RUNS_SRC, _GROK_RUNS, _GROK_RUNS_SRC
    with _IDENTITY_LOCK:
        if observe_codex is not None:
            record_codex_attribution = observe_codex
        changed = False
        write_error: Exception | None = None
        # Each provider owns its own evidence stream. One unavailable vendor
        # must not prevent another provider's observation from being recorded.
        # A vendor we cannot read is isolated. A ledger we cannot write is
        # ours, and must not be swallowed as if the vendor had said nothing.
        try:
            at, who, current_claude = _claude_signed_in()
        except Exception:  # noqa: BLE001 - an unreadable provider is isolated
            pass
        else:
            try:
                if current_claude is not None and at:
                    changed = _remember_claude_account(current_claude, at) or changed
                if _append_run(
                    _claude_runs_path(), "account_id", at, who, claude_identity_runs()
                ):
                    _CLAUDE_RUNS = _CLAUDE_RUNS_SRC = None
                    changed = True
            except Exception as exc:  # noqa: BLE001 - ours to report
                record_component_failure("claude_identity_write", exc)
                write_error = exc
            else:
                clear_component_failure("claude_identity_write")
        if record_codex_attribution:
            try:
                changed = note_codex_identity(observed_at=observed_at) or changed
            except Exception as exc:  # noqa: BLE001 - already recorded inside
                write_error = exc
        else:
            try:
                at, who = _codex_signed_in()
                current = _official_codex_account()
            except Exception:  # noqa: BLE001 - an unreadable provider is isolated
                pass
            else:
                try:
                    if current is not None and current.account_id == who and at:
                        changed = _remember_codex_account(current, at) or changed
                except Exception as exc:  # noqa: BLE001 - ours to report
                    record_component_failure("codex_identity_write", exc)
                    write_error = exc
                else:
                    clear_component_failure("codex_identity_write")
        try:
            at, who = _grok_signed_in()
        except Exception:  # noqa: BLE001 - an unreadable provider is isolated
            pass
        else:
            try:
                if _append_run(
                    _grok_runs_path(), "account_id", at, who, grok_identity_runs()
                ):
                    _GROK_RUNS = _GROK_RUNS_SRC = None
                    changed = True
            except Exception as exc:  # noqa: BLE001 - ours to report
                record_component_failure("grok_identity_write", exc)
                write_error = exc
            else:
                clear_component_failure("grok_identity_write")
        if write_error is not None:
            raise write_error
        return changed
