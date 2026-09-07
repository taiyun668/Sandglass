from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any


PROVIDERS = ("claude", "codex", "grok")

# Bump whenever a collector changes what it extracts, so cached rows re-parse instead of
# serving numbers computed by the old logic.
RECORD_FORMAT = 12


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        if "." in text:
            head, rest = text.split(".", 1)
            digits = []
            tz = ""
            for ch in rest:
                if ch.isdigit() and not tz:
                    digits.append(ch)
                else:
                    tz += ch
            frac = "".join(digits)[:6].ljust(6, "0")
            text = f"{head}.{frac}{tz}"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def local_day(dt: datetime | None) -> str:
    if dt is None:
        return "unknown"
    return dt.astimezone().date().isoformat()


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cache_write_1h_tokens: int = 0
    reasoning_tokens: int = 0
    calls: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
            + self.cache_write_1h_tokens
        )

    def add(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cache_write_1h_tokens=self.cache_write_1h_tokens + other.cache_write_1h_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            calls=self.calls + other.calls,
        )

    def sub(self, other: "TokenUsage") -> "TokenUsage":
        def clamp(a: int, b: int) -> int:
            return max(0, a - b)

        return TokenUsage(
            input_tokens=clamp(self.input_tokens, other.input_tokens),
            output_tokens=clamp(self.output_tokens, other.output_tokens),
            cache_read_tokens=clamp(self.cache_read_tokens, other.cache_read_tokens),
            cache_write_tokens=clamp(self.cache_write_tokens, other.cache_write_tokens),
            cache_write_1h_tokens=clamp(self.cache_write_1h_tokens, other.cache_write_1h_tokens),
            reasoning_tokens=clamp(self.reasoning_tokens, other.reasoning_tokens),
            calls=clamp(self.calls, other.calls),
        )

    def as_dict(self) -> dict[str, int]:
        data = asdict(self)
        data["total_tokens"] = self.total_tokens
        return data


ZERO = TokenUsage()


@dataclass
class SessionRecord:
    provider: str
    session_id: str
    path: str
    started_at: str | None = None
    ended_at: str | None = None
    project: str = ""
    client: str = ""
    account_id: str = ""
    account_label: str = ""
    models: list[str] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    daily: dict[str, TokenUsage] = field(default_factory=dict)
    # Per-minute deltas, oldest first. Day buckets cannot answer how much usage fell
    # inside a rolling quota window; this can. None means "parsed before timelines existed" so
    # the cache can tell that apart from "parsed, genuinely empty" ([]).
    timeline: list[tuple[str, TokenUsage]] | None = None
    title: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": RECORD_FORMAT,
            "provider": self.provider,
            "session_id": self.session_id,
            "path": self.path,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "project": self.project,
            "client": self.client,
            "account_id": self.account_id,
            "account_label": self.account_label,
            "models": self.models,
            "usage": self.usage.as_dict(),
            "daily": {day: usage.as_dict() for day, usage in self.daily.items()},
            "timeline": None
            if self.timeline is None
            else [[at, usage.as_dict()] for at, usage in self.timeline],
            "title": self.title,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionRecord":
        usage = TokenUsage(**{k: int(data.get("usage", {}).get(k, 0) or 0) for k in _USAGE_FIELDS})
        daily = {}
        for day, raw in (data.get("daily") or {}).items():
            daily[day] = TokenUsage(**{k: int(raw.get(k, 0) or 0) for k in _USAGE_FIELDS})
        raw_timeline = data.get("timeline")
        timeline: list[tuple[str, TokenUsage]] | None = None
        if isinstance(raw_timeline, list):
            timeline = [
                (str(at), TokenUsage(**{k: int((raw or {}).get(k, 0) or 0) for k in _USAGE_FIELDS}))
                for at, raw in (item for item in raw_timeline if isinstance(item, (list, tuple)) and len(item) == 2)
            ]
        return cls(
            provider=data.get("provider", ""),
            session_id=data.get("session_id", ""),
            path=data.get("path", ""),
            started_at=data.get("started_at"),
            ended_at=data.get("ended_at"),
            project=data.get("project") or "",
            client=data.get("client") or "",
            account_id=data.get("account_id") or "",
            account_label=data.get("account_label") or "",
            models=list(data.get("models") or []),
            usage=usage,
            daily=daily,
            timeline=timeline,
            title=data.get("title") or "",
            extra=dict(data.get("extra") or {}),
        )


_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cache_write_1h_tokens",
    "reasoning_tokens",
    "calls",
)


@dataclass
class Account:
    provider: str
    account_id: str
    email: str = ""
    label: str = ""
    plan: str = ""
    auth_mode: str = ""
    active: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "account_id": self.account_id,
            "email": self.email,
            "label": self.label,
            "plan": self.plan,
            "auth_mode": self.auth_mode,
            "active": self.active,
            "extra": self.extra,
        }


@dataclass
class QuotaWindow:
    label: str
    used_percent: float | None = None
    resets_at: str | None = None
    window_minutes: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def merge_usage_map(target: dict[str, TokenUsage], day: str, usage: TokenUsage) -> None:
    current = target.get(day)
    target[day] = usage if current is None else current.add(usage)


def replace_account(session: SessionRecord, account: Account | None) -> SessionRecord:
    if account is None:
        return session
    return replace(
        session,
        account_id=account.account_id,
        account_label=account.label or mask_email(account.email) or account.account_id[:12],
    )


def conversation_id(session: SessionRecord) -> str:
    """Which conversation this record's work belongs to.

    Every "sessions" number the panel shows is a count of these. It used to be
    a count of records that were not subagents, which is a different question:
    a subagent's tokens were added to the block while its conversation was not,
    so a block whose root transcript happened to end elsewhere reported zero
    sessions beside a real token total. Two of the four call sites papered over
    that with `len(roots) or len(every session id)` -- a fallback to a second
    quantity, under the same column heading, in whichever rows the first one
    collapsed. On this machine that was 34 of 162 five-hour blocks.

    A subagent belongs to the conversation that spawned it, and both vendors
    say so where they can: a Claude subagent transcript carries the root's
    sessionId, and a Codex subagent carries parent_thread_id. Where Codex
    writes no parent -- 296 of 417 subagent records here -- there is nothing to
    attribute it to, and the run counts as itself rather than as nothing.
    """
    return str(session.extra.get("root_session_id") or "") or session.session_id


def evidence_sources(session: SessionRecord) -> list[str]:
    """Return stable source labels for every usage-bearing report projection."""
    raw = session.extra.get("evidence_sources")
    if isinstance(raw, (list, tuple, set)):
        labels = sorted({str(item).strip() for item in raw if str(item).strip()})
        if labels:
            return labels
    if session.provider in PROVIDERS:
        return [f"official_builtin:{session.provider}"]
    provider = session.provider or "unknown"
    return [f"unlabeled:{provider}"]


def mask_email(email: str) -> str:
    if not email or "@" not in email:
        return email
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        shown = local[:1] + "*"
    else:
        shown = local[:2] + "***"
    return f"{shown}@{domain}"
