from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sandglass.models import (
    SessionRecord,
    TokenUsage,
    conversation_id,
    evidence_sources,
    parse_ts,
)

BLOCK_SECONDS = 5 * 3600


def annotate_windows(windows: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    return [annotate_window(window, now) for window in windows]


def annotate_window(window: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    out = dict(window)
    resets = parse_ts(out.get("resets_at"))
    minutes = out.get("window_minutes")
    if isinstance(minutes, (int, float)):
        minutes = float(minutes)
    else:
        minutes = None
    if resets is not None:
        remaining = (resets - now).total_seconds()
        out["resets_in_seconds"] = max(0, int(remaining))
        out["resets_in"] = format_duration(remaining)
        if minutes:
            out["window_start"] = (resets - timedelta(minutes=minutes)).isoformat()
    return out


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "now"
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and len(parts) < 2:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{total}s")
    return " ".join(parts)


def five_hour_blocks(sessions: Iterable[SessionRecord], now: datetime | None = None, limit: int = 8) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    grouped: dict[int, list[tuple[SessionRecord, TokenUsage]]] = defaultdict(list)
    for session in sessions:
        ts = parse_ts(session.ended_at) or parse_ts(session.started_at)
        if ts is None:
            continue
        start = int(ts.timestamp()) // BLOCK_SECONDS * BLOCK_SECONDS
        grouped[start].append((session, session.usage))
    current = int(now.timestamp()) // BLOCK_SECONDS * BLOCK_SECONDS
    keys = sorted(grouped)
    if current not in grouped:
        keys.append(current)
        keys.sort()
    keys = keys[-limit:]
    out: list[dict[str, Any]] = []
    for start in keys:
        items = grouped.get(start, [])
        usage = TokenUsage()
        providers: set[str] = set()
        # One block holds every provider at once, and the vendors number their
        # sessions independently, so the provider belongs in the key here.
        conversations: set[tuple[str, str]] = set()
        sources: set[str] = set()
        for session, part in items:
            usage = usage.add(part)
            providers.add(session.provider)
            sources.update(evidence_sources(session))
            conversations.add((session.provider, conversation_id(session)))
        start_dt = datetime.fromtimestamp(start, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(start + BLOCK_SECONDS, tz=timezone.utc)
        out.append(
            {
                "key": start_dt.isoformat(),
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "current": start == current,
                "usage": usage.as_dict(),
                "sessions": len(conversations),
                "providers": sorted(providers),
                "evidence_sources": sorted(sources),
            }
        )
    return out
