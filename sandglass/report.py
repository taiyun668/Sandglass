from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sandglass.accounts import load_accounts
from sandglass.attribution import (
    full_window_inference_allowed,
    single_official_account_id,
    sessions_for_account,
    sessions_unclaimed_by_accounts,
    switch_runs_for,
)
from sandglass.models import (
    Account,
    SessionRecord,
    TokenUsage,
    conversation_id,
    evidence_sources,
    parse_ts,
)
from sandglass.pace import annotate_windows, five_hour_blocks
from sandglass.quota import (
    attach_cached_quota,
    attach_live_quota,
    quota_anchor,
    quota_anchor_source,
)


def build_report(
    sessions: list[SessionRecord],
    since: datetime | None = None,
    until: datetime | None = None,
    accounts: list[Account] | None = None,
    live_quota: bool | None = None,
    attribution_mode: str = "",
) -> dict[str, Any]:
    filtered = [clipped for s in sessions if (clipped := _clip_session(s, since, until))]
    own_accounts = accounts is None
    accounts = accounts if accounts is not None else load_accounts()
    if live_quota is None:
        live_quota = own_accounts
    if live_quota:
        accounts = attach_live_quota(accounts)
    elif own_accounts:
        accounts = attach_cached_quota(accounts)
    # These two partition every record and are reported side by side, so unlike
    # the per-bucket counts they are not answering "which conversations were
    # active in this stretch" and must not use conversation_id(). Measured over
    # this machine's 797 records: 380 roots and 417 subagents becomes 611 and
    # 417 under that rule, because the 296 Codex subagents whose parent the
    # vendor never wrote each become their own conversation -- counted in both
    # columns of a line that reads as a split.
    roots = [s for s in filtered if not s.extra.get("is_subagent")]
    subagents = [s for s in filtered if s.extra.get("is_subagent")]
    totals = _bucket_usage(filtered, lambda _: "all")
    all_bucket = totals.get("all") or _empty_bucket("all")
    all_bucket["sessions"] = len(roots)
    all_bucket["subagents"] = len(subagents)
    by_provider = _bucket_usage(filtered, lambda s: s.provider or "unknown")
    for row in by_provider.values():
        key = row["key"]
        row["sessions"] = len([s for s in roots if (s.provider or "unknown") == key])
        row["subagents"] = len([s for s in subagents if (s.provider or "unknown") == key])
    account_slices: dict[tuple[str, str], list[SessionRecord]] = {}
    unassigned_by_provider: dict[str, dict[str, Any]] = {}
    single_owner_by_provider: dict[str, str] = {}
    if accounts:
        peers_by_provider: dict[str, list[Account]] = defaultdict(list)
        for account in accounts:
            peers_by_provider[account.provider].append(account)
        providers = set(peers_by_provider)
        providers.update(
            session.provider for session in filtered if session.provider
        )
        for provider in sorted(providers):
            peers = peers_by_provider.get(provider, [])
            single_owner = single_official_account_id(peers, attribution_mode)
            single_owner_by_provider[provider] = single_owner
            runs = switch_runs_for(provider)
            for account in peers:
                account_slices[(provider, account.account_id)] = sessions_for_account(
                    account, peers, filtered, runs, single_owner
                )
            unclaimed = sessions_unclaimed_by_accounts(
                provider, peers, filtered, runs, single_owner
            )
            if unclaimed:
                row = _rollup("未归属", unclaimed)
                row["provider"] = provider
                unassigned_by_provider[provider] = row
        by_account = {
            account.label: _rollup(
                account.label, account_slices[(account.provider, account.account_id)]
            )
            for account in accounts
            if account_slices[(account.provider, account.account_id)]
        }
        for provider, row in unassigned_by_provider.items():
            by_account[f"{provider} · 未归属"] = row
        known_keys = {(account.provider, account.account_id) for account in accounts}
        unknown = [
            session
            for session in filtered
            if session.account_id and (session.provider, session.account_id) not in known_keys
        ]
        by_account.update(
            _bucket_usage(unknown, lambda s: s.account_label or s.account_id or "unassigned")
        )
    else:
        by_account = _bucket_usage(filtered, lambda s: s.account_label or s.account_id or "unassigned")
        unidentified = [session for session in filtered if not session.account_id]
        for provider in sorted({session.provider for session in unidentified if session.provider}):
            unclaimed = sessions_unclaimed_by_accounts(
                provider,
                [],
                unidentified,
                switch_runs_for(provider),
            )
            if unclaimed:
                row = _rollup("未归属", unclaimed)
                row["provider"] = provider
                unassigned_by_provider[provider] = row
    by_client = _bucket_usage(filtered, lambda s: s.client or "unknown")
    by_day = _bucket_daily(filtered)
    by_provider_day = _bucket_provider_daily(filtered)
    ownership_note = (
        "已选择单账号官方工具：每个平台的官方本机记录归到该平台当前账号；"
        "扫描到旧账号不会自动改变选择，模式可随时切回且不改写账本。"
        if attribution_mode == "single_official"
        else "多账号、多工具或补账模式只采用直接身份事件与已验证适配证据；"
        "缺少身份证据的部分保持未归属。"
    )
    notes = [
        "账号额度条（5h / 7d）就是该账号当前的套餐用量；带 live 的是刚从官方接口拉的。",
        ownership_note,
        "额度条可能包含其他设备；本机 token 只含这台电脑。",
        "Sandglass 持续监测 Grok 账号的官方额度、重置时间和本机用量；归属遵循当前选择的账号模式。",
        "Codex / Claude 子 Agent 的 token 会计入总量；分时段的会话数按所属对话计，"
        "子 Agent 归入派它出来的那段对话；总计一行的会话数与子 Agent 数分开给出。",
        "没有本机会话文件的工具（例如纯网页）不会出现在 token 明细里。",
    ]
    for account in accounts:
        err = account.extra.get("quota_error")
        if err:
            notes.append(f"{account.provider} 额度：{err}")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "this-machine",
        "attribution_mode": attribution_mode,
        "filters": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
        "totals": all_bucket,
        "by_provider": _sorted_buckets(by_provider),
        "by_account": _sorted_buckets(by_account),
        "by_client": _sorted_buckets(by_client),
        "by_day": [by_day[day] for day in sorted(by_day)],
        "by_provider_day": by_provider_day,
        "unassigned_by_provider": [
            unassigned_by_provider[key] for key in sorted(unassigned_by_provider)
        ],
        "by_block": five_hour_blocks(filtered),
        "accounts": [
            _account_view(
                account,
                account_slices.get((account.provider, account.account_id), []),
            )
            for account in accounts
        ],
        "sessions": [_session_view(s) for s in sorted(filtered, key=lambda s: s.ended_at or s.started_at or "", reverse=True)[:200]],
        "session_count": len(roots),
        "subagent_count": len(subagents),
        "notes": notes,
    }


def _ago(now: datetime, **span) -> datetime | None:
    """`now` minus a span, or None when the span reaches past the calendar.

    isdigit() bounds the characters, not the number: "99999999d" builds a
    timedelta that cannot be subtracted from a real date, and the OverflowError
    came out of the HTTP handler unhandled, dropping the connection with no
    response at all. A window longer than the calendar asks for everything,
    which is what None already means here.
    """
    try:
        return now - timedelta(**span)
    except OverflowError:
        return None


def parse_since(text: str | None) -> datetime | None:
    if not text:
        return None
    now = datetime.now(timezone.utc)
    raw = text.strip().lower()
    if raw.endswith("d") and raw[:-1].isdigit():
        return _ago(now, days=int(raw[:-1]))
    if raw.endswith("h") and raw[:-1].isdigit():
        return _ago(now, hours=int(raw[:-1]))
    dt = parse_ts(text)
    return dt


def _clip_session(session: SessionRecord, since: datetime | None, until: datetime | None) -> SessionRecord | None:
    if since is None and until is None:
        return session
    if session.timeline:
        selected: list[tuple[str, TokenUsage]] = []
        usage = TokenUsage()
        daily: dict[str, TokenUsage] = {}
        for at, delta in session.timeline:
            moment = parse_ts(at)
            if moment is None:
                continue
            if since and moment < since:
                continue
            if until and moment > until:
                continue
            selected.append((at, delta))
            usage = usage.add(delta)
            day = moment.astimezone().date().isoformat()
            daily[day] = (daily.get(day) or TokenUsage()).add(delta)
        if not selected:
            return None
        return replace(
            session,
            usage=usage,
            daily=daily,
            timeline=selected,
            started_at=selected[0][0],
            ended_at=selected[-1][0],
        )
    if session.daily:
        daily = {}
        usage = TokenUsage()
        for day, part in session.daily.items():
            if not _day_in_range(day, since, until):
                continue
            daily[day] = part
            usage = usage.add(part)
        if not daily:
            return None
        return replace(session, usage=usage, daily=daily)
    return session if _in_range(session, since, until) else None


def _day_in_range(day: str, since: datetime | None, until: datetime | None) -> bool:
    try:
        day_date = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        return False
    if since and day_date < since.astimezone().date():
        return False
    if until and day_date > until.astimezone().date():
        return False
    return True


def _in_range(session: SessionRecord, since: datetime | None, until: datetime | None) -> bool:
    ts = parse_ts(session.ended_at) or parse_ts(session.started_at)
    if ts is None:
        return since is None
    if since and ts < since:
        return False
    if until and ts > until:
        return False
    return True


def _bucket_usage(sessions: Iterable[SessionRecord], key_fn) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[SessionRecord]] = defaultdict(list)
    for session in sessions:
        groups[key_fn(session)].append(session)
    return {key: _rollup(key, items) for key, items in groups.items()}


def _bucket_daily(sessions: Iterable[SessionRecord]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[tuple[SessionRecord, TokenUsage]]] = defaultdict(list)
    for session in sessions:
        if session.daily:
            for day, usage in session.daily.items():
                grouped[day].append((session, usage))
        elif session.usage.total_tokens or session.usage.calls:
            day = (session.ended_at or session.started_at or "unknown")[:10]
            grouped[day].append((session, session.usage))
    out = {}
    for day, items in grouped.items():
        usage = TokenUsage()
        providers: set[str] = set()
        # This row sits above the per-provider rows for the same day, so its
        # count has to be the same count. The vendors number their sessions
        # independently, and this is the one place both namespaces are poured
        # into one set, so the provider is part of the key here.
        conversations: set[tuple[str, str]] = set()
        sources: set[str] = set()
        for session, part in items:
            usage = usage.add(part)
            providers.add(session.provider)
            conversations.add((session.provider, conversation_id(session)))
            sources.update(evidence_sources(session))
        out[day] = {
            "key": day,
            "usage": usage.as_dict(),
            "sessions": len(conversations),
            "providers": sorted(providers),
            "evidence_sources": sorted(sources),
        }
    return out


def _bucket_provider_daily(sessions: Iterable[SessionRecord]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, dict[str, list[tuple[SessionRecord, TokenUsage]]]] = defaultdict(lambda: defaultdict(list))
    for session in sessions:
        provider = session.provider or "unknown"
        if session.daily:
            for day, usage in session.daily.items():
                grouped[provider][day].append((session, usage))
        elif session.usage.total_tokens or session.usage.calls:
            day = (session.ended_at or session.started_at or "unknown")[:10]
            grouped[provider][day].append((session, session.usage))
    out: dict[str, list[dict[str, Any]]] = {}
    for provider, days in grouped.items():
        rows = []
        for day in sorted(days):
            usage = TokenUsage()
            conversations: set[str] = set()
            sources: set[str] = set()
            for session, part in days[day]:
                usage = usage.add(part)
                sources.update(evidence_sources(session))
                conversations.add(conversation_id(session))
            rows.append(
                {
                    "key": day,
                    "usage": usage.as_dict(),
                    "sessions": len(conversations),
                    "evidence_sources": sorted(sources),
                }
            )
        out[provider] = rows
    return out


def _rollup(key: str, sessions: list[SessionRecord]) -> dict[str, Any]:
    usage = TokenUsage()
    models: set[str] = set()
    clients: set[str] = set()
    providers: set[str] = set()
    model_tokens: dict[str, int] = defaultdict(int)
    sources: set[str] = set()
    for session in sessions:
        usage = usage.add(session.usage)
        models.update(session.models)
        name = session.models[0] if session.models else ""
        if name:
            model_tokens[name] += session.usage.total_tokens
        if session.client:
            clients.add(session.client)
        providers.add(session.provider)
        sources.update(evidence_sources(session))
    top_model = max(model_tokens, key=model_tokens.get) if model_tokens else ""
    return {
        "key": key,
        "usage": usage.as_dict(),
        "sessions": len(sessions),
        "models": sorted(models),
        "top_model": top_model,
        "clients": sorted(clients),
        "providers": sorted(providers),
        "evidence_sources": sorted(sources),
    }


def _empty_bucket(key: str) -> dict[str, Any]:
    return {
        "key": key,
        "usage": TokenUsage().as_dict(),
        "sessions": 0,
        "models": [],
        "top_model": "",
        "clients": [],
        "providers": [],
        "evidence_sources": [],
    }


def _sorted_buckets(buckets: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(buckets.values(), key=lambda item: item["usage"]["total_tokens"], reverse=True)


def _session_view(session: SessionRecord) -> dict[str, Any]:
    return {
        "provider": session.provider,
        "session_id": session.session_id,
        "title": session.title,
        "project": session.project,
        "client": session.client,
        "account_id": session.account_id,
        "account_label": session.account_label,
        "models": session.models,
        "started_at": session.started_at,
        "ended_at": session.ended_at,
        "usage": session.usage.as_dict(),
        "is_subagent": bool(session.extra.get("is_subagent")),
        "root_session_id": session.extra.get("root_session_id") or "",
        "evidence_sources": evidence_sources(session),
    }


def _account_view(account: Account, owned: list[SessionRecord]) -> dict[str, Any]:
    roots = [s for s in owned if not s.extra.get("is_subagent")]
    rollup = _rollup(account.label, owned) if owned else _empty_bucket(account.label)
    rollup["sessions"] = len(roots)
    rollup["subagents"] = len(owned) - len(roots)
    windows = annotate_windows(list(account.extra.get("windows") or []))
    return {
        **account.as_dict(),
        "current_usage": windows,
        "current_usage_kind": "quota" if windows else "unknown",
        "quota_source": account.extra.get("quota_source") or ("quota" if windows else "unknown"),
        "quota_error": account.extra.get("quota_error") or "",
        "local_usage": rollup["usage"],
        "local_sessions": rollup["sessions"],
        "local_subagents": rollup["subagents"],
        "local_evidence_sources": rollup["evidence_sources"],
        "local_in_windows": _local_in_windows(account, owned, windows),
        "windows": windows,
    }


def _local_in_windows(
    account: Account,
    sessions: list[SessionRecord],
    windows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Tokens this machine spent inside each window.

    Summed from per-minute timeline deltas, not from whole-session totals: a session
    that ran for 32 hours and happened to end inside a 16-minute-old window must
    contribute only the minutes that actually fall in it.
    """
    out: list[dict[str, Any]] = []
    for window in windows:
        start = parse_ts(window.get("window_start"))
        if start is None:
            continue
        anchor_iso, anchor_kind, gap = quota_anchor(
            account.provider,
            account.account_id,
            str(window.get("label") or ""),
        )
        anchored = anchor_kind == "reset"
        trusted_anchor = anchored and quota_anchor_source(
            account.provider, account.account_id, str(window.get("label") or "")
        ).startswith("official_live")
        if account.extra.get("quota_source") == "stale" and not trusted_anchor:
            continue
        if anchored:
            anchor = parse_ts(anchor_iso)
            if anchor is not None and anchor > start:
                start = anchor
            else:
                anchored = False
        end = parse_ts(window.get("resets_at"))
        usage = TokenUsage()
        touched: set[str] = set()
        partial = False
        sources: set[str] = set()
        inference_allowed = True
        for session in sessions:
            if session.timeline is None:
                # Pre-timeline record: fall back to the old end-time rule and say so.
                ts = parse_ts(session.ended_at) or parse_ts(session.started_at)
                if ts is None or ts < start:
                    continue
                if end is not None and ts > end:
                    continue
                partial = True
                usage = usage.add(session.usage)
                sources.update(evidence_sources(session))
                inference_allowed = inference_allowed and bool(
                    session.extra.get("full_window_inference_allowed", True)
                )
                touched.add(conversation_id(session))
                continue
            for at, delta in session.timeline:
                moment = parse_ts(at)
                if moment is None or moment < start or (end is not None and moment > end):
                    continue
                usage = usage.add(delta)
                sources.update(evidence_sources(session))
                inference_allowed = inference_allowed and full_window_inference_allowed(
                    session, moment
                )
                touched.add(conversation_id(session))
        row = {
            "label": window.get("label"),
            "usage": usage.as_dict(),
            "sessions": len(touched),
            "evidence_sources": sorted(sources),
            "full_window_inference_allowed": inference_allowed,
            "counted_from": start.isoformat(),
            # Both edges, not just the near one: a reconciliation that has to
            # guess the far edge ends up testing its own guess.
            "counted_to": end.isoformat() if end is not None else "",
            "reset_anchored": anchored,
            "observation_gap": gap,
        }
        if partial:
            row["estimated"] = True
        out.append(row)
    return out
