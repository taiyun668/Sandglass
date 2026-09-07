from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote

from sandglass.accounts import (
    assign_codex_account,
    assign_grok_account,
    load_accounts,
)
from sandglass.cache import SessionCache
from sandglass.models import (
    Account,
    SessionRecord,
    TokenUsage,
    local_day,
    merge_usage_map,
    parse_ts,
)
from sandglass.paths import claude_projects, codex_home, grok_home


Progress = Callable[[str, int, int], None]


def collect_all(cache: SessionCache | None = None, progress: Progress | None = None) -> list[SessionRecord]:
    own_cache = cache is None
    cache = cache or SessionCache()
    try:
        accounts = load_accounts()
        sessions: list[SessionRecord] = []
        sessions.extend(_collect_claude(cache, progress))
        sessions.extend(_collect_codex(cache, accounts, progress))
        sessions.extend(_collect_grok(cache, accounts, progress))
        cache.commit()
        return sessions
    finally:
        if own_cache:
            cache.close()


def _collect_claude(cache: SessionCache, progress: Progress | None) -> list[SessionRecord]:
    root = claude_projects()
    files = sorted(_iter_files(root, ".jsonl"), key=lambda p: str(p).lower())
    return _parse_files(
        "claude",
        files,
        cache,
        progress,
        _parse_claude,
    )


def _collect_codex(cache: SessionCache, accounts: list[Account], progress: Progress | None) -> list[SessionRecord]:
    home = codex_home()
    files: list[Path] = []
    for folder in (home / "sessions", home / "archived_sessions"):
        files.extend(_iter_files(folder, ".jsonl"))
    files = [p for p in files if p.name.startswith("rollout-")]
    files.sort(key=lambda p: str(p).lower())
    codex_accounts = [a for a in accounts if a.provider == "codex"]

    return _parse_files(
        "codex",
        files,
        cache,
        progress,
        _parse_codex,
        attach=lambda record: assign_codex_account(record, codex_accounts),
    )


def _collect_grok(cache: SessionCache, accounts: list[Account], progress: Progress | None) -> list[SessionRecord]:
    files: list[Path] = list(_iter_files(grok_home() / "sessions", "updates.jsonl"))
    seen: set[str] = set()
    unique: list[Path] = []
    for path in files:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    files = sorted(unique, key=lambda p: str(p).lower())
    grok_accounts = [a for a in accounts if a.provider == "grok"]
    return _parse_files(
        "grok",
        files,
        cache,
        progress,
        _parse_grok,
        attach=lambda record: assign_grok_account(record, grok_accounts),
    )


def _parse_files(
    provider: str,
    files: list[Path],
    cache: SessionCache,
    progress: Progress | None,
    parse: Callable[[Path], SessionRecord | None],
    attach: Callable[[SessionRecord], SessionRecord] | None = None,
) -> list[SessionRecord]:
    out: list[SessionRecord] = []
    total = len(files)
    for index, path in enumerate(files, start=1):
        record = cache.get(path)
        if record is None:
            if progress:
                progress(provider, index, total)
            # Stamp from before the read, not after: these files are appended
            # to while they are parsed, and a stamp taken afterwards would mark
            # the shorter content as matching the longer file.
            try:
                stamp: os.stat_result | None = path.stat()
            except OSError:
                stamp = None
            record = parse(path)
            if record is not None and stamp is not None:
                # No stamp, no row. Storing this parse under a stat taken now
                # would date it to whatever the file has become since, which is
                # the mistake the stamp exists to avoid.
                cache.put(record, stat=stamp)
        if record is None:
            continue
        if attach:
            record = attach(record)
        out.append(record)
    return out


def _iter_files(root: Path, suffix: str) -> Iterable[Path]:
    if not root.exists():
        return []
    out: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    name = entry.name
                    if suffix.startswith(".") and name.endswith(suffix):
                        out.append(Path(entry.path))
                    elif name == suffix:
                        out.append(Path(entry.path))
            except OSError:
                continue
    return out


def _parse_claude(path: Path) -> SessionRecord | None:
    session_id = path.stem
    project = _project_from_claude_path(path)
    started = None
    ended = None
    born_at = None
    events: list[tuple[Any, str, TokenUsage, str]] = []
    client = "Claude Code"
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"type"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                sid = row.get("sessionId")
                if isinstance(sid, str) and sid:
                    session_id = sid
                ts = parse_ts(row.get("timestamp"))
                if ts:
                    born_at = born_at or ts
                    started = started or ts
                    ended = ts
                kind = row.get("type")
                if kind != "assistant":
                    continue
                if row.get("isSidechain") and "subagents" not in path.parts:
                    continue
                message = row.get("message") if isinstance(row.get("message"), dict) else {}
                usage_raw = message.get("usage") if isinstance(message.get("usage"), dict) else row.get("usage")
                if not isinstance(usage_raw, dict):
                    continue
                usage = _claude_usage(usage_raw)
                if usage.total_tokens == 0 and usage.calls == 0:
                    continue
                # One user request can contain multiple billed assistant calls
                # (for example across tool use). message.id identifies the
                # provider response; requestId identifies the wider user turn.
                event_id = str(
                    message.get("id")
                    or row.get("requestId")
                    or row.get("uuid")
                    or f"line-{len(events)}"
                )
                model = str(message.get("model") or row.get("model") or "")
                events.append((ts, event_id, usage, model))
    except OSError:
        return None
    cutoff = _claude_replay_cutoff(born_at, events)
    if cutoff is not None:
        events = [(ts, rid, usage, model) for ts, rid, usage, model in events if ts is None or ts >= cutoff]
        started = cutoff
        ended = None
        for ts, _, _, _ in events:
            if ts:
                ended = ts
    models: list[str] = []
    by_event: dict[str, TokenUsage] = {}
    daily: dict[str, TokenUsage] = {}
    timeline: dict[str, TokenUsage] = {}
    for ts, event_id, usage, model in events:
        previous = by_event.get(event_id)
        by_event[event_id] = usage
        if model and model not in models:
            models.append(model)
        if ts:
            delta = usage if previous is None else usage.sub(previous)
            merge_usage_map(daily, local_day(ts), delta)
            _timeline_add(timeline, ts, delta)
    total = TokenUsage()
    for usage in by_event.values():
        total = total.add(usage)
    if total.total_tokens == 0 and not session_id:
        return None
    is_subagent = "subagents" in path.parts
    record = SessionRecord(
        provider="claude",
        session_id=session_id,
        path=str(path),
        started_at=_iso(started),
        ended_at=_iso(ended),
        project=project,
        client="Claude subagent" if is_subagent else client,
        models=models,
        usage=total,
        daily=_finalize_daily(daily, total, ended),
        timeline=_finalize_timeline(timeline),
        extra={"is_subagent": is_subagent, "root_session_id": session_id if is_subagent else ""},
    )
    return record


def _claude_usage(raw: dict[str, Any]) -> TokenUsage:
    iterations = raw.get("iterations")
    if isinstance(iterations, list) and iterations:
        total = TokenUsage()
        for item in iterations:
            if isinstance(item, dict):
                total = total.add(_claude_usage_leaf(item))
        if total.total_tokens or total.calls:
            # An iteration carries no output_tokens_details, so summing them loses
            # the thinking count entirely -- 41% of this machine's Claude output.
            # The top level describes the whole message; it is what knows.
            return replace(total, reasoning_tokens=_claude_thinking(raw))
    return _claude_usage_leaf(raw)


def _claude_thinking(raw: dict[str, Any]) -> int:
    details = raw.get("output_tokens_details")
    details = details if isinstance(details, dict) else {}
    return _int(details.get("thinking_tokens") or raw.get("reasoning_tokens"))


def _claude_usage_leaf(raw: dict[str, Any]) -> TokenUsage:
    cache_creation = raw.get("cache_creation") if isinstance(raw.get("cache_creation"), dict) else {}
    cache_1h = _int(cache_creation.get("ephemeral_1h_input_tokens"))
    cache_5m = _int(cache_creation.get("ephemeral_5m_input_tokens"))
    cache_write = _int(raw.get("cache_creation_input_tokens"))
    if cache_1h or cache_5m:
        cache_write = cache_5m
    output = _int(raw.get("output_tokens"))
    reasoning = _claude_thinking(raw)
    return TokenUsage(
        input_tokens=_int(raw.get("input_tokens")),
        output_tokens=output,
        cache_read_tokens=_int(raw.get("cache_read_input_tokens") or raw.get("cache_read_tokens")),
        cache_write_tokens=cache_write,
        cache_write_1h_tokens=cache_1h,
        reasoning_tokens=reasoning,
        calls=1 if (output or _int(raw.get("input_tokens")) or cache_write or _int(raw.get("cache_read_input_tokens"))) else 0,
    )


# Replay/inflation gates derived from the first-party rollout shape.
# Codex: a forked rollout replays the parent's entire token history into its own
# opening seconds, and a spawn burst can write thousands of events into a single
# minute. Claude Code resume copies the parent jsonl with original timestamps after
# a new session birth (queue-operation) -- same replay, different shape, so the
# cutoff is "older than birth" rather than "first 30 seconds". Counted naively
# that was 45% of this machine's Codex output and 44% of one Claude week's local output.
# Gates are decided inside one file, so a per-file parse cache stays coherent -- a
# cross-file signature rule would make a file's result depend on which files were
# scanned before it.
_REPLAY_WINDOW_SECONDS = 30.0
_REPLAY_MIN_EVENTS = 20
_STORM_EVENTS_PER_MINUTE = 1000


def _codex_replay_prefix(events: list, forked_from: str) -> int:
    """How many opening events are a replay of the parent, not new work."""
    if not forked_from or not events:
        return 0
    first = next((t for t, _ in events if t is not None), None)
    if first is None:
        return 0
    count = 0
    for ts, _ in events:
        if ts is not None and (ts - first).total_seconds() > _REPLAY_WINDOW_SECONDS:
            break
        count += 1
    return count if count >= _REPLAY_MIN_EVENTS else 0


def _claude_replay_cutoff(born_at, events: list):
    """Drop copied parent history in a resumed Claude jsonl.

    Codex needs forked_from_id and then trims the opening 30s burst. Claude Code
    resume has no such flag: the new file's first timestamp is the session birth
    (queue-operation), and the copied transcript keeps the original times, which
    are earlier. Same intra-file rule, same 20-event floor so a couple of skewed
    clocks do not wipe a real session.
    """
    if born_at is None or not events:
        return None
    older = 0
    for ts, *_ in events:
        if ts is not None and ts < born_at:
            older += 1
            if older >= _REPLAY_MIN_EVENTS:
                return born_at
    return None


def _codex_storm_minutes(events: list) -> set[str]:
    """Minutes holding an implausible number of events -- a spawn burst, not typing."""
    per_minute: dict[str, int] = {}
    for ts, _ in events:
        if ts is None:
            continue
        key = ts.strftime("%Y%m%d%H%M")
        per_minute[key] = per_minute.get(key, 0) + 1
    return {key for key, n in per_minute.items() if n >= _STORM_EVENTS_PER_MINUTE}


def _parse_codex(path: Path) -> SessionRecord | None:
    session_id = path.stem
    started = None
    ended = None
    project = ""
    client = "Codex CLI"
    models: list[str] = []
    snapshots: list[tuple[Any, TokenUsage]] = []
    current_model = ""
    prev_total = TokenUsage()
    forked_from = ""
    parent = ""
    extra: dict[str, Any] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if "session_meta" not in line and "turn_context" not in line and "token_count" not in line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                kind = row.get("type")
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                ts = parse_ts(row.get("timestamp") or payload.get("timestamp"))
                if kind == "session_meta":
                    forked_from = str(payload.get("forked_from_id") or forked_from)
                    own_id = str(payload.get("id") or "")
                    listed = str(payload.get("session_id") or "")
                    session_id = own_id or listed or session_id
                    parent = str(payload.get("parent_thread_id") or "")
                    thread_source = str(payload.get("thread_source") or "")
                    is_subagent = thread_source == "subagent" or bool(parent and parent != own_id)
                    extra = {
                        "is_subagent": is_subagent,
                        "parent_thread_id": parent,
                        "root_session_id": parent or listed or own_id,
                        "thread_source": thread_source,
                    }
                    project = str(payload.get("cwd") or project)
                    originator = str(payload.get("originator") or "")
                    source = payload.get("source") or ""
                    client = _codex_client(originator, source)
                    started = parse_ts(payload.get("timestamp")) or ts or started
                elif kind == "turn_context":
                    project = str(payload.get("cwd") or project)
                    model = str(payload.get("model") or "")
                    if model:
                        current_model = model
                        if model not in models:
                            models.append(model)
                elif kind == "event_msg" and payload.get("type") == "token_count":
                    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                    usage = _codex_turn_usage(info, prev_total)
                    prev_total = _codex_usage(info.get("total_token_usage") if isinstance(info.get("total_token_usage"), dict) else prev_total)
                    if ts is None:
                        ts = ended
                    if usage.total_tokens or usage.calls:
                        snapshots.append((ts, usage))
                    if ts:
                        ended = ts
                        started = started or ts
    except OSError:
        return None
    prefix = _codex_replay_prefix(snapshots, forked_from or parent)
    storms = _codex_storm_minutes(snapshots)
    if prefix or storms:
        kept: list[tuple[Any, TokenUsage]] = []
        for index, (ts, usage) in enumerate(snapshots):
            if index < prefix:
                continue
            if ts is not None and ts.strftime("%Y%m%d%H%M") in storms:
                continue
            kept.append((ts, usage))
        snapshots = kept
    if not snapshots:
        if session_id:
            return SessionRecord(
                provider="codex",
                session_id=session_id,
                path=str(path),
                started_at=_iso(started),
                ended_at=_iso(ended),
                project=project,
                client=client,
                models=models,
                timeline=[],
                extra=extra,
            )
        return None
    total = _sum_snapshots(snapshots)
    daily = _daily_from_increments(snapshots)
    if total.calls == 0:
        total.calls = 1
    return SessionRecord(
        provider="codex",
        session_id=session_id,
        path=str(path),
        started_at=_iso(started),
        ended_at=_iso(ended),
        project=project,
        client=client,
        models=models,
        usage=total,
        daily=_finalize_daily(daily, total, ended),
        timeline=_timeline_from_increments(snapshots),
        extra=extra,
    )


def _codex_turn_usage(info: dict[str, Any], prev_total: TokenUsage) -> TokenUsage:
    """One billed turn. Prefer last_token_usage; totals reset when a thread is compacted."""
    last_raw = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
    last = _codex_usage(last_raw)
    current = _codex_usage(
        info.get("total_token_usage") if isinstance(info.get("total_token_usage"), dict) else {}
    )
    if last.total_tokens:
        # Codex also emits TokenCount snapshots that repeat the same last
        # usage while total_token_usage has not moved. Those are not turns.
        if prev_total.total_tokens and current.total_tokens == prev_total.total_tokens:
            return TokenUsage()
        return last
    return current.sub(prev_total)


def _codex_usage(raw: dict[str, Any] | TokenUsage) -> TokenUsage:
    if isinstance(raw, TokenUsage):
        return raw
    input_tokens = _int(raw.get("input_tokens"))
    cached = _int(raw.get("cached_input_tokens") or raw.get("cache_read_input_tokens"))
    uncached = max(0, input_tokens - cached) if input_tokens else 0
    return TokenUsage(
        input_tokens=uncached,
        output_tokens=_int(raw.get("output_tokens")),
        cache_read_tokens=cached,
        cache_write_tokens=_int(raw.get("cache_write_input_tokens") or raw.get("cached_write_tokens")),
        reasoning_tokens=_int(raw.get("reasoning_output_tokens") or raw.get("reasoning_tokens")),
        calls=1,
    )


def _codex_client(originator: str, source: Any) -> str:
    if isinstance(source, dict):
        sub = source.get("subagent") if isinstance(source.get("subagent"), dict) else None
        if sub is not None:
            spawn = sub.get("thread_spawn") if isinstance(sub.get("thread_spawn"), dict) else sub
            nick = str((spawn or {}).get("agent_nickname") or "")
            return f"Codex subagent ({nick})" if nick else "Codex subagent"
        source = " ".join(str(key) for key in source)
    blob = f"{originator} {source}".lower()
    if "desktop" in blob:
        return "Codex Desktop"
    if "vscode" in blob or "vs code" in blob:
        return "VS Code / Cursor"
    if originator:
        return originator
    if source:
        return str(source)
    return "Codex CLI"


def _parse_grok(path: Path) -> SessionRecord | None:
    session_id = path.parent.name
    project = _project_from_grok_path(path)
    started = None
    ended = None
    models: list[str] = []
    snapshots: list[tuple[Any, TokenUsage]] = []
    title = ""
    summary_path = path.with_name("summary.json")
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = {}
        if isinstance(summary, dict):
            title = str(summary.get("generated_title") or "")
            model = str(summary.get("current_model_id") or "")
            if model:
                models.append(model)
            started = parse_ts(summary.get("created_at")) or started
            ended = parse_ts(summary.get("updated_at") or summary.get("last_active_at")) or ended
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if "turn_completed" not in line and "sessionUpdate" not in line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                ts = parse_ts(row.get("timestamp"))
                params = row.get("params") if isinstance(row.get("params"), dict) else row
                update = params.get("update") if isinstance(params.get("update"), dict) else params
                if update.get("sessionUpdate") != "turn_completed":
                    continue
                sid = params.get("sessionId") or update.get("sessionId")
                if isinstance(sid, str) and sid:
                    session_id = sid
                usage_raw = update.get("usage") if isinstance(update.get("usage"), dict) else {}
                usage = _grok_usage(usage_raw)
                model_usage = usage_raw.get("modelUsage") if isinstance(usage_raw.get("modelUsage"), dict) else {}
                for model_name in model_usage:
                    if model_name not in models:
                        models.append(str(model_name))
                snapshots.append((ts, usage))
                if ts:
                    started = started or ts
                    ended = ts
    except OSError:
        return None
    if not snapshots:
        return SessionRecord(
            provider="grok",
            session_id=session_id,
            path=str(path),
            started_at=_iso(started),
            ended_at=_iso(ended),
            project=project,
            client="Grok CLI",
            models=models,
            timeline=[],
            title=title,
        )
    # Grok reports each turn_completed as that turn's own totals (numTurns resets every
    # event), so these are increments to add up -- not a running counter to diff.
    total = _sum_snapshots(snapshots)
    daily = _daily_from_increments(snapshots)
    return SessionRecord(
        provider="grok",
        session_id=session_id,
        path=str(path),
        started_at=_iso(started),
        ended_at=_iso(ended),
        project=project,
        client="Grok CLI",
        models=models,
        usage=total,
        daily=_finalize_daily(daily, total, ended),
        timeline=_timeline_from_increments(snapshots),
        title=title,
    )


def _grok_usage(raw: dict[str, Any]) -> TokenUsage:
    return TokenUsage(
        input_tokens=max(0, _int(raw.get("inputTokens")) - _int(raw.get("cachedReadTokens"))),
        output_tokens=_int(raw.get("outputTokens")),
        cache_read_tokens=_int(raw.get("cachedReadTokens")),
        cache_write_tokens=_int(raw.get("cacheCreationTokens")),
        reasoning_tokens=_int(raw.get("reasoningTokens")),
        calls=_int(raw.get("modelCalls")) or (1 if raw else 0),
    )


def _minute_key(ts: Any) -> str | None:
    dt = parse_ts(ts)
    if dt is None:
        return None
    return dt.replace(second=0, microsecond=0).isoformat()


def _timeline_add(bucket: dict[str, TokenUsage], ts: Any, delta: TokenUsage) -> None:
    """Accumulate a usage delta into the minute it happened."""
    key = _minute_key(ts)
    if key is None or (delta.total_tokens == 0 and delta.calls == 0):
        return
    current = bucket.get(key)
    bucket[key] = delta if current is None else current.add(delta)


def _finalize_timeline(bucket: dict[str, TokenUsage]) -> list[tuple[str, TokenUsage]]:
    return [(key, bucket[key]) for key in sorted(bucket)]


def _timeline_from_increments(snapshots: list[tuple[Any, TokenUsage]]) -> list[tuple[str, TokenUsage]]:
    """For logs whose events are already per-turn totals (Grok): add, never diff."""
    bucket: dict[str, TokenUsage] = {}
    for ts, usage in snapshots:
        _timeline_add(bucket, ts, usage)
    return _finalize_timeline(bucket)


def _daily_from_increments(snapshots: list[tuple[Any, TokenUsage]]) -> dict[str, TokenUsage]:
    daily: dict[str, TokenUsage] = {}
    for ts, usage in snapshots:
        merge_usage_map(daily, local_day(ts) if ts is not None else "unknown", usage)
    return daily


def _sum_snapshots(snapshots: list[tuple[Any, TokenUsage]]) -> TokenUsage:
    total = TokenUsage()
    for _, usage in snapshots:
        total = total.add(usage)
    return total


def _timeline_from_cumulative(snapshots: list[tuple[Any, TokenUsage]]) -> list[tuple[str, TokenUsage]]:
    """Cumulative snapshots -> per-minute deltas, so any window can be summed exactly."""
    bucket: dict[str, TokenUsage] = {}
    prev = TokenUsage()
    for ts, usage in snapshots:
        delta = usage.sub(prev)
        prev = usage
        _timeline_add(bucket, ts, delta)
    return _finalize_timeline(bucket)


def _daily_from_cumulative(snapshots: list[tuple[Any, TokenUsage]]) -> dict[str, TokenUsage]:
    last_by_day: dict[str, TokenUsage] = {}
    ordered: list[tuple[str, TokenUsage]] = []
    for ts, usage in snapshots:
        day = local_day(ts) if ts is not None else "unknown"
        last_by_day[day] = usage
        if not ordered or ordered[-1][0] != day:
            ordered.append((day, usage))
        else:
            ordered[-1] = (day, usage)
    daily: dict[str, TokenUsage] = {}
    prev = TokenUsage()
    seen: list[str] = []
    for day, usage in ordered:
        if day in seen:
            continue
        seen.append(day)
        daily[day] = usage.sub(prev)
        prev = usage
    return daily


def _finalize_daily(daily: dict[str, TokenUsage], total: TokenUsage, ended: Any) -> dict[str, TokenUsage]:
    if daily:
        return daily
    if total.total_tokens or total.calls:
        return {local_day(ended): total}
    return {}


def _project_from_claude_path(path: Path) -> str:
    try:
        rel = path.relative_to(claude_projects())
        encoded = rel.parts[0] if rel.parts else ""
    except ValueError:
        encoded = path.parent.name
    return encoded.replace("-", "/") if encoded else ""


def _project_from_grok_path(path: Path) -> str:
    encoded = path.parent.parent.name
    try:
        return unquote(encoded)
    except Exception:
        return encoded


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    dt = parse_ts(value)
    return dt.isoformat() if dt else None


def _int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0
