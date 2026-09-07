from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import timezone

from sandglass.models import Account, SessionRecord, TokenUsage, evidence_sources, parse_ts
from sandglass.product_mode import SINGLE_OFFICIAL


SINGLE_ACCOUNT_POLICY_SOURCE = "sandglass_policy:single_official_account"


def _minute_key(moment) -> str:
    return (
        moment.astimezone(timezone.utc)
        .replace(second=0, microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def supplemental_identity(session: SessionRecord, moment) -> dict:
    raw = session.extra.get("minute_identity_evidence")
    if not isinstance(raw, dict):
        return {}
    item = raw.get(_minute_key(moment))
    return dict(item) if isinstance(item, dict) else {}


def supplemental_sources_for_account(
    account: Account,
    provider_runs,
    session: SessionRecord,
    moment,
    single_account_owner_id: str = "",
) -> list[str]:
    """Sources that are necessary for this minute's account ownership."""
    if single_account_owner_id == account.account_id and _official_builtin_only(session):
        return [SINGLE_ACCOUNT_POLICY_SOURCE]
    supplement = supplemental_identity(session, moment)
    if str(supplement.get("account_id") or "") == account.account_id:
        return sorted(
            {
                str(source).strip()
                for source in supplement.get("evidence_sources") or []
                if str(source).strip()
            }
        )
    if minute_owner(session, provider_runs, moment):
        return []
    return []


def full_window_inference_allowed(session: SessionRecord, moment) -> bool:
    """Whether every user source needed by this minute authorized inference."""
    direct_sources = [
        source
        for source in evidence_sources(session)
        if source.startswith("user_adapter:")
    ]
    if session.extra.get("evidence_class") == "B_token" and direct_sources and not bool(
        session.extra.get("full_window_inference_allowed", False)
    ):
        return False
    supplement = supplemental_identity(session, moment)
    supplemental_sources = [
        str(source)
        for source in supplement.get("evidence_sources") or []
        if str(source).startswith("user_adapter:")
    ]
    if supplemental_sources and not bool(
        supplement.get("full_window_inference_allowed", False)
    ):
        return False
    return True


def switch_runs_for(provider: str):
    """Return the first-party identity timeline for providers that can switch."""
    from sandglass.accounts import (
        claude_identity_runs,
        codex_identity_runs,
        grok_identity_runs,
    )

    if provider == "claude":
        return claude_identity_runs()
    if provider == "codex":
        return codex_identity_runs()
    if provider == "grok":
        return grok_identity_runs()
    return None


def known_identities(peers: list[Account]) -> set[str]:
    """Names a provider timeline may use for accounts Sandglass can display."""
    names: set[str] = set()
    for peer in peers:
        if peer.email:
            names.add(peer.email)
        if peer.account_id:
            names.add(peer.account_id)
    return names


def single_official_account_id(peers: list[Account], mode: str = "") -> str:
    """Return the current account selected by the official client for this provider."""
    if mode != SINGLE_OFFICIAL:
        return ""
    active = [peer for peer in peers if peer.active]
    if len(active) == 1:
        return active[0].account_id
    return ""


def _official_builtin_only(session: SessionRecord) -> bool:
    sources = evidence_sources(session)
    return bool(sources) and all(source.startswith("official_builtin:") for source in sources)


def _explicit_user_owner(session: SessionRecord, account: Account) -> bool:
    return (
        session.account_id == account.account_id
        and any(source.startswith("user_adapter:") for source in evidence_sources(session))
    )


def minute_owner(session: SessionRecord, runs, moment) -> str:
    """Resolve one minute against the provider's authoritative switch timeline."""
    from sandglass.accounts import _claude_owner_at, _codex_owner_at, _grok_owner_at

    if not runs:
        return ""
    if session.provider == "claude":
        return _claude_owner_at(runs, moment)
    if session.provider == "codex":
        return _codex_owner_at(runs, moment)
    if session.provider == "grok":
        return _grok_owner_at(runs, moment)
    return ""


def owns_minute(
    account: Account,
    provider_runs,
    session: SessionRecord,
    moment,
    known: set[str] | None = None,
    single_account_owner_id: str = "",
) -> bool:
    """Whether direct evidence or the selected single-account policy owns a minute."""
    if single_account_owner_id and _official_builtin_only(session):
        return account.account_id == single_account_owner_id
    if _explicit_user_owner(session, account):
        return True
    supplement = supplemental_identity(session, moment)
    owner = str(supplement.get("account_id") or "")
    if owner:
        return owner == account.account_id
    who = minute_owner(session, provider_runs, moment)
    if who:
        if known is not None and who not in known:
            return False
        return who == (account.email or "") or who == account.account_id
    return False


def sessions_for_account(
    account: Account,
    peers: list[Account],
    sessions: list[SessionRecord],
    runs=None,
    single_account_owner_id: str = "",
) -> list[SessionRecord]:
    """Slice sessions into only the per-minute deltas owned by one account.

    User-adapter identity remains evidence-owned. In the explicit single-account
    mode, official built-in records are projected onto the current official account
    at read time; no SessionRecord is rewritten.
    """
    if runs is None:
        runs = switch_runs_for(account.provider)
    known = known_identities(peers)
    single_owner = str(single_account_owner_id or "")
    out: list[SessionRecord] = []
    for session in sessions:
        if session.provider != account.provider:
            continue
        if session.timeline is None:
            moment = parse_ts(session.ended_at) or parse_ts(session.started_at)
            directly_owned = _explicit_user_owner(session, account)
            owned = (
                directly_owned
                or (
                    single_owner == account.account_id
                    and _official_builtin_only(session)
                )
                or (
                    moment is not None
                    and owns_minute(account, runs, session, moment, known, single_owner)
                )
            )
            if owned:
                sources = set(evidence_sources(session))
                if (
                    single_owner == account.account_id
                    and _official_builtin_only(session)
                    and not directly_owned
                ):
                    sources.add(SINGLE_ACCOUNT_POLICY_SOURCE)
                out.append(
                    replace(
                        session,
                        account_id=account.account_id,
                        account_label=account.label,
                        extra={**session.extra, "evidence_sources": sorted(sources)},
                    )
                )
            continue

        selected: list[tuple[str, TokenUsage]] = []
        usage = TokenUsage()
        daily: dict[str, TokenUsage] = defaultdict(TokenUsage)
        source_labels = set(evidence_sources(session))
        for at, delta in session.timeline:
            moment = parse_ts(at)
            if moment is None or not owns_minute(
                account, runs, session, moment, known, single_owner
            ):
                continue
            selected.append((at, delta))
            usage = usage.add(delta)
            source_labels.update(
                supplemental_sources_for_account(
                    account, runs, session, moment, single_owner
                )
            )
            day = moment.astimezone().date().isoformat()
            daily[day] = daily[day].add(delta)
        if not selected:
            continue

        out.append(
            replace(
                session,
                account_id=account.account_id,
                account_label=account.label,
                started_at=selected[0][0],
                ended_at=selected[-1][0],
                usage=usage,
                daily=dict(daily),
                timeline=selected,
                extra={**session.extra, "evidence_sources": sorted(source_labels)},
            )
        )
    return out


def sessions_unclaimed_by_accounts(
    provider: str,
    peers: list[Account],
    sessions: list[SessionRecord],
    runs=None,
    single_account_owner_id: str = "",
) -> list[SessionRecord]:
    """Return usage claimed by zero currently discoverable accounts.

    The result is provider-level, never a synthetic account. Skill mode keeps
    identity gaps here. Single-account mode removes only the official built-in
    records projected onto the unambiguous current official account.
    """
    if runs is None:
        runs = switch_runs_for(provider)
    known = known_identities(peers)
    single_owner = str(single_account_owner_id or "")
    out: list[SessionRecord] = []
    for session in sessions:
        if session.provider != provider:
            continue
        if session.timeline is None:
            moment = parse_ts(session.ended_at) or parse_ts(session.started_at)
            if not any(
                _explicit_user_owner(session, account)
                or (
                    single_owner == account.account_id
                    and _official_builtin_only(session)
                )
                or (
                    moment is not None
                    and owns_minute(account, runs, session, moment, known, single_owner)
                )
                for account in peers
            ):
                out.append(session)
            continue
        selected: list[tuple[str, TokenUsage]] = []
        usage = TokenUsage()
        daily: dict[str, TokenUsage] = defaultdict(TokenUsage)
        for at, delta in session.timeline:
            moment = parse_ts(at)
            if moment is None:
                continue
            if any(
                owns_minute(account, runs, session, moment, known, single_owner)
                for account in peers
            ):
                continue
            selected.append((at, delta))
            usage = usage.add(delta)
            day = moment.astimezone().date().isoformat()
            daily[day] = daily[day].add(delta)
        if selected:
            out.append(
                replace(
                    session,
                    account_id="",
                    account_label="未归属",
                    started_at=selected[0][0],
                    ended_at=selected[-1][0],
                    usage=usage,
                    daily=dict(daily),
                    timeline=selected,
                )
            )
    return out
