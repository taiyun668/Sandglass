from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Collection, Iterable

from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
    ExportLogsServiceResponse,
)

from sandglass.dbschema import apply_schema
from sandglass.models import PROVIDERS, SessionRecord, TokenUsage, parse_ts
from sandglass.paths import telemetry_db


MAX_OTLP_BODY = 4 * 1024 * 1024
MAX_USER_SOURCE_RECORDS = 20_000
_CUSTOM_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_CUSTOM_SOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")

_USER_RECORD_FIELDS = {
    "provider",
    "event_id",
    "timestamp",
    "account_id",
    "session_id",
    "model",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cache_write_1h_tokens",
    "total_tokens",
    "calls",
    "source_version",
    "schema_version",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry_usage (
    event_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    event_name TEXT NOT NULL,
    event_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    account_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    model TEXT NOT NULL,
    source TEXT NOT NULL,
    source_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    evidence_grade TEXT NOT NULL,
    coverage_state TEXT NOT NULL,
    usage_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS telemetry_usage_provider_time
ON telemetry_usage(provider, event_at);
CREATE INDEX IF NOT EXISTS telemetry_usage_account_time
ON telemetry_usage(provider, account_id, event_at);
CREATE TABLE IF NOT EXISTS telemetry_receipts (
    provider TEXT PRIMARY KEY,
    requests INTEGER NOT NULL,
    received_records INTEGER NOT NULL,
    accepted_records INTEGER NOT NULL,
    rejected_records INTEGER NOT NULL,
    last_received_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class TelemetryRecord:
    event_key: str
    provider: str
    event_name: str
    event_at: str
    received_at: str
    account_id: str
    session_id: str
    model: str
    source: str
    source_version: str
    schema_version: str
    evidence_grade: str
    coverage_state: str
    usage: TokenUsage

    def db_values(self) -> tuple[str, ...]:
        return (
            self.event_key,
            self.provider,
            self.event_name,
            self.event_at,
            self.received_at,
            self.account_id,
            self.session_id,
            self.model,
            self.source,
            self.source_version,
            self.schema_version,
            self.evidence_grade,
            self.coverage_state,
            json.dumps(self.usage.as_dict(), separators=(",", ":")),
        )


class TelemetryStore:
    """Append-only normalized OTLP usage under SANDGLASS_HOME.

    Raw OTLP records are deliberately never stored: prompt, tool and path fields
    are discarded before this class sees a record.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or telemetry_db()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        apply_schema(conn, SCHEMA)
        return conn

    def append(self, records: Iterable[TelemetryRecord]) -> tuple[int, int]:
        rows = list(records)
        if not rows:
            return 0, 0
        conn = self._connect()
        try:
            before = conn.total_changes
            conn.executemany(
                """
                INSERT OR IGNORE INTO telemetry_usage(
                    event_key, provider, event_name, event_at, received_at,
                    account_id, session_id, model, source, source_version,
                    schema_version, evidence_grade, coverage_state, usage_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [row.db_values() for row in rows],
            )
            conn.commit()
            inserted = conn.total_changes - before
            return inserted, len(rows) - inserted
        finally:
            conn.close()

    def existing_event_keys(self, event_keys: Iterable[str]) -> set[str]:
        """Return keys already persisted, without creating the database, its
        schema or a row. `mode=ro` is what makes that true; it is not the same
        as leaving the directory untouched. Measured: reading a WAL database
        this way creates a -shm and a -wal beside it, and both remain after the
        connection closes. Sidecars are SQLite's, not this store's -- but a
        caller told "no state is created" and then finding two new files has
        been told something false."""

        keys = sorted({str(key) for key in event_keys if str(key)})
        if not keys or not self.path.exists():
            return set()
        uri = self.path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10.0)
        try:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "telemetry_usage" not in tables:
                return set()
            found: set[str] = set()
            for offset in range(0, len(keys), 900):
                batch = keys[offset : offset + 900]
                placeholders = ",".join("?" for _ in batch)
                found.update(
                    str(row[0])
                    for row in conn.execute(
                        f"SELECT event_key FROM telemetry_usage "
                        f"WHERE event_key IN ({placeholders})",
                        batch,
                    )
                )
            return found
        finally:
            conn.close()

    def reset_user_source(self, source: str) -> int:
        """Remove one disabled adapter's normalized rows from Sandglass state."""

        source_id = _custom_source(source)
        if not source_id:
            raise ValueError("normalized records require a stable source id")
        conn = self._connect()
        try:
            before = conn.total_changes
            conn.execute(
                "DELETE FROM telemetry_usage WHERE source = ?",
                (f"user_adapter:{source_id}",),
            )
            conn.commit()
            return conn.total_changes - before
        finally:
            conn.close()

    def record_receipts(self, receipts: dict[str, dict[str, int]], received_at: str) -> None:
        rows = [
            (
                provider,
                1,
                int(counts.get("received_records", 0)),
                int(counts.get("accepted_records", 0)),
                int(counts.get("rejected_records", 0)),
                received_at,
            )
            for provider, counts in receipts.items()
            if _valid_observed_provider(provider) and int(counts.get("received_records", 0)) > 0
        ]
        if not rows:
            return
        conn = self._connect()
        try:
            conn.executemany(
                """
                INSERT INTO telemetry_receipts(
                    provider, requests, received_records, accepted_records,
                    rejected_records, last_received_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    requests = requests + excluded.requests,
                    received_records = received_records + excluded.received_records,
                    accepted_records = accepted_records + excluded.accepted_records,
                    rejected_records = rejected_records + excluded.rejected_records,
                    last_received_at = excluded.last_received_at
                """,
                rows,
            )
            conn.commit()
        finally:
            conn.close()

    def records(self) -> list[dict[str, Any]]:
        from sandglass.user_sources import UserSourceStore

        package_store = UserSourceStore(self.path.with_name("user-sources.sqlite"))
        packages = package_store.active_import_packages()
        package_sources = {str(package["source"]) for package in packages}
        out: list[dict[str, Any]] = []
        if self.path.exists():
            uri = self.path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=10.0)
            conn.row_factory = sqlite3.Row
            try:
                tables = {
                    str(row["name"])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if "telemetry_usage" in tables:
                    for row in conn.execute(
                        "SELECT * FROM telemetry_usage ORDER BY event_at, event_key"
                    ):
                        item = dict(row)
                        if str(item.get("source") or "").removeprefix(
                            "user_adapter:"
                        ) in package_sources:
                            continue
                        item["usage"] = json.loads(item.pop("usage_json"))
                        out.append(item)
            finally:
                conn.close()
        for package in packages:
            out.extend(
                user_source_record_dicts(
                    str(package["source"]), package.get("records", [])
                )
            )
        return sorted(out, key=lambda item: (item["event_at"], item["event_key"]))

    def status(self) -> list[dict[str, Any]]:
        """Summarize received evidence without creating or changing the store."""
        rows: dict[str, dict[str, Any]] = {}
        receipts: dict[str, dict[str, Any]] = {}
        account_ids: dict[str, set[str]] = {}
        for row in self.records():
            provider = str(row.get("provider") or "")
            item = rows.setdefault(
                provider,
                {
                    "provider": provider,
                    "records": 0,
                    "attributed_records": 0,
                    "unassigned_records": 0,
                    "account_count": 0,
                    "sources": [],
                    "first_event_at": "",
                    "last_event_at": "",
                    "last_received_at": "",
                },
            )
            item["records"] += 1
            account_id = str(row.get("account_id") or "")
            if account_id:
                item["attributed_records"] += 1
                account_ids.setdefault(provider, set()).add(account_id)
            else:
                item["unassigned_records"] += 1
            source = str(row.get("source") or "")
            if source and source not in item["sources"]:
                item["sources"].append(source)
            event_at = str(row.get("event_at") or "")
            received_at = str(row.get("received_at") or "")
            if event_at:
                item["first_event_at"] = min(
                    filter(None, (str(item["first_event_at"]), event_at))
                )
                item["last_event_at"] = max(str(item["last_event_at"]), event_at)
            item["last_received_at"] = max(
                str(item["last_received_at"]), received_at
            )
        for provider, item in rows.items():
            item["account_count"] = len(account_ids.get(provider, set()))
            item["sources"] = sorted(item["sources"])
        if self.path.exists():
            uri = self.path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=10.0)
            conn.row_factory = sqlite3.Row
            try:
                tables = {
                    str(row["name"])
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
                if "telemetry_receipts" in tables:
                    for row in conn.execute("SELECT * FROM telemetry_receipts"):
                        item = dict(row)
                        for key in (
                            "requests",
                            "received_records",
                            "accepted_records",
                            "rejected_records",
                        ):
                            item[key] = int(item[key] or 0)
                        receipts[str(item["provider"])] = item
            finally:
                conn.close()

        out = []
        ordered_providers = list(PROVIDERS) + sorted((set(rows) | set(receipts)) - set(PROVIDERS))
        for provider in ordered_providers:
            item = rows.get(provider) or {
                "provider": provider,
                "records": 0,
                "attributed_records": 0,
                "unassigned_records": 0,
                "account_count": 0,
                "sources": [],
                "first_event_at": "",
                "last_event_at": "",
                "last_received_at": "",
            }
            receipt = receipts.get(provider) or {
                "requests": 0,
                "received_records": 0,
                "accepted_records": 0,
                "rejected_records": 0,
                "last_received_at": "",
            }
            item.update(
                {
                    "requests": receipt["requests"],
                    "received_records": receipt["received_records"],
                    "accepted_receipt_records": receipt["accepted_records"],
                    "rejected_receipt_records": receipt["rejected_records"],
                }
            )
            item["last_received_at"] = max(
                str(item.get("last_received_at") or ""),
                str(receipt.get("last_received_at") or ""),
            )
            user_sources = [
                source for source in item.get("sources", [])
                if str(source).startswith("user_adapter:")
            ]
            official_sources = [
                source for source in item.get("sources", [])
                if not str(source).startswith("user_adapter:")
            ]
            if user_sources and official_sources:
                item["source_kind"] = "mixed"
            elif user_sources or provider not in PROVIDERS:
                item["source_kind"] = "user_adapter"
            else:
                item["source_kind"] = "official_builtin"
            if item["attributed_records"]:
                item["state"] = "identity_observed"
            elif item["records"]:
                item["state"] = "usage_only"
            elif item["received_records"]:
                item["state"] = "traffic_only"
            else:
                item["state"] = "never_observed"
            out.append(item)
        return out


def telemetry_status(
    store: TelemetryStore | None = None,
    *,
    receiver: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = store or TelemetryStore()
    return {
        "receiver": receiver or {
            "state": "ready",
            "endpoint": "/v1/logs",
            "user_source_endpoint": "/v1/user-sources",
            "protocol": "otlp_http_protobuf",
            "manageable": False,
        },
        "included_in_report": False,
        "providers": target.status(),
    }


def reconcile_telemetry_minutes(
    sessions: Iterable[SessionRecord],
    store: TelemetryStore | None = None,
) -> dict[str, Any]:
    """Shadow-join official OTLP rows to the existing local minute ledger.

    This deliberately does not modify sessions or report totals.  A match exists
    only when provider, session id and UTC minute are identical and every stored
    token bucket (including calls) sums to the same value on both sides.  OTLP is
    therefore capable of supplying identity for that one minute without becoming
    a second source of tokens.  Conflicting account ids or any numeric mismatch
    remain unmatched evidence.
    """
    local_sessions = list(sessions)
    local = _local_minute_ledger(local_sessions)

    target = store or TelemetryStore()
    rows = target.records()
    result = _reconcile_rows(local, rows, require_identity=True)
    by_source = []
    source_rows: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        source = _text(row.get("source"))
        if source.startswith("user_adapter:"):
            source_rows.setdefault(source, []).append(row)
    for source, mine in sorted(source_rows.items()):
        source_result = _reconcile_rows(local, mine, require_identity=False)
        source_result.update(
            {
                "source": source,
                "source_id": source.removeprefix("user_adapter:"),
                "normalized_records": len(mine),
                "attributed_records": sum(bool(_text(row.get("account_id"))) for row in mine),
                "unassigned_records": sum(not _text(row.get("account_id")) for row in mine),
                "normalized_tokens": sum(
                    _usage_from_dict(row.get("usage")).total_tokens for row in mine
                ),
                "first_event_at": min((_text(row.get("event_at")) for row in mine), default=""),
                "last_event_at": max((_text(row.get("event_at")) for row in mine), default=""),
            }
        )
        by_source.append(source_result)
    result.update(
        {
            "shadow_only": True,
            "included_in_report": False,
            "local_groups": len(local),
            "by_source": by_source,
        }
    )
    return result


def candidate_user_source_ledger(
    sessions: Iterable[SessionRecord],
    settings: dict[str, dict[str, Any]] | None = None,
    store: TelemetryStore | None = None,
    *,
    _include_token_claims: bool = False,
) -> dict[str, Any]:
    """Classify user minutes without changing first-party sessions or reports.

    A minute already present in the official local ledger can only offer identity;
    it can never add Token. Distinct normalized events may coexist in an unseen
    minute. Exact event signatures repeated across user sources remain blocked
    until an explicit de-duplication relationship exists.
    """
    local_sessions = list(sessions)
    local = _local_minute_ledger(local_sessions)
    selected = settings or {}
    grouped: dict[
        tuple[str, str, str], dict[str, list[dict[str, Any]]]
    ] = {}
    sources: dict[str, dict[str, Any]] = {}

    def source_stats(source_id: str) -> dict[str, Any]:
        return sources.setdefault(
            source_id,
            {
                "source_id": source_id,
                "normalized_records": 0,
                "normalized_tokens": 0,
                "official_duplicate_minutes": 0,
                "official_duplicate_tokens": 0,
                "identity_candidate_minutes": 0,
                "identity_candidate_tokens": 0,
                "identity_enabled_minutes": 0,
                "identity_enabled_tokens": 0,
                "token_candidate_minutes": 0,
                "token_candidate_tokens": 0,
                "token_enabled_minutes": 0,
                "token_enabled_tokens": 0,
                "blocked_source_minutes": 0,
                "blocked_source_tokens": 0,
                "identity_blocked_minutes": 0,
                "reasons": {},
            },
        )

    def note(
        stats: dict[str, Any],
        reason: str,
        *,
        tokens: int = 0,
        blocked: bool = False,
        identity_only: bool = False,
    ) -> None:
        reasons = stats["reasons"]
        reasons[reason] = int(reasons.get(reason, 0)) + 1
        if blocked:
            stats["blocked_source_minutes"] += 1
            stats["blocked_source_tokens"] += tokens
        if identity_only:
            stats["identity_blocked_minutes"] += 1

    target = store or TelemetryStore()
    for row in target.records():
        source = _text(row.get("source"))
        if not source.startswith("user_adapter:"):
            continue
        source_id = source.removeprefix("user_adapter:")
        stats = source_stats(source_id)
        usage = _usage_from_dict(row.get("usage"))
        stats["normalized_records"] += 1
        stats["normalized_tokens"] += usage.total_tokens
        provider = _text(row.get("provider"))
        session_id = _text(row.get("session_id"))
        minute = _minute(row.get("event_at"))
        if usage.total_tokens <= 0 or not provider or not session_id or not minute:
            note(stats, "uncorrelated", tokens=usage.total_tokens, blocked=True)
            continue
        key = (provider, session_id, minute)
        grouped.setdefault(key, {}).setdefault(source_id, []).append(row)

    identity_candidates = identity_candidate_tokens = 0
    identity_supplement_minutes = identity_supplement_tokens = 0
    token_candidates = token_candidate_tokens = 0
    token_enabled_minutes = token_enabled_tokens = 0
    token_claims: list[dict[str, Any]] = []
    cross_source_collisions = identity_conflicts = 0
    cross_session_official_ambiguities = 0
    official_overlap_minutes = 0
    local_provider_minutes: set[tuple[str, str]] = {
        (provider, minute) for provider, _session_id, minute in local
    }
    local_accounts: dict[tuple[str, str, str], set[str]] = {}
    for session in local_sessions:
        if (
            not session.account_id
            or session.timeline is None
            or session.extra.get("account_identity_scope")
            == "session_timeline_majority"
        ):
            continue
        for at, usage in session.timeline:
            minute = _minute(at)
            if minute and usage.total_tokens > 0:
                local_accounts.setdefault(
                    (session.provider, session.session_id, minute), set()
                ).add(session.account_id)
    user_keys_by_provider_minute: dict[
        tuple[str, str], set[tuple[str, str, str]]
    ] = {}
    for key in grouped:
        user_keys_by_provider_minute.setdefault((key[0], key[2]), set()).add(key)
    concurrent_user_provider_minutes = {
        provider_minute
        for provider_minute, keys in user_keys_by_provider_minute.items()
        if len(keys) > 1
    }

    for key, by_source in sorted(grouped.items()):
        provider = key[0]
        prepared: dict[str, tuple[TokenUsage, str, bool, bool, bool]] = {}
        for source_id, rows in by_source.items():
            usage = TokenUsage()
            effective_accounts: set[str] = set()
            setting = selected.get(source_id) or {}
            mapped_provider = _text(setting.get("mapped_provider"))
            mapped_account = _text(setting.get("mapped_account_id"))
            mapping_conflict = bool(mapped_account and mapped_provider != provider)
            missing_identity = False
            missing_declared_identity = False
            if mapped_account:
                effective_accounts.add(mapped_account)
            for row in rows:
                usage = usage.add(_usage_from_dict(row.get("usage")))
                declared_account = _text(row.get("account_id"))
                if declared_account:
                    effective_accounts.add(declared_account)
                else:
                    missing_declared_identity = True
                    if not mapped_account:
                        missing_identity = True
            account_conflict = mapping_conflict or len(effective_accounts) > 1
            account = next(iter(effective_accounts)) if len(effective_accounts) == 1 else ""
            prepared[source_id] = (
                usage,
                account,
                account_conflict,
                not missing_identity,
                not missing_declared_identity,
            )

        official = local.get(key)
        if official is not None:
            official_overlap_minutes += 1
            proposals: dict[str, list[str]] = {}
            for source_id, (
                usage,
                account,
                account_conflict,
                identity_complete,
                _declared_complete,
            ) in prepared.items():
                stats = source_stats(source_id)
                if usage != official:
                    note(
                        stats,
                        "official_token_mismatch",
                        tokens=usage.total_tokens,
                        blocked=True,
                    )
                    continue
                stats["official_duplicate_minutes"] += 1
                stats["official_duplicate_tokens"] += usage.total_tokens
                official_accounts = local_accounts.get(key) or set()
                if account_conflict:
                    note(stats, "identity_conflict", identity_only=True)
                elif official_accounts:
                    if len(official_accounts) == 1 and account in official_accounts:
                        note(stats, "identity_already_official")
                    elif account:
                        note(stats, "identity_conflict", identity_only=True)
                    else:
                        note(stats, "missing_identity")
                elif account and identity_complete:
                    proposals.setdefault(account, []).append(source_id)
                else:
                    note(stats, "missing_identity")
            if len(proposals) == 1:
                identity_candidates += 1
                identity_candidate_tokens += official.total_tokens
                proposed_account, proposed_sources = next(iter(proposals.items()))
                enabled_sources = []
                for source_id in proposed_sources:
                    stats = source_stats(source_id)
                    stats["identity_candidate_minutes"] += 1
                    stats["identity_candidate_tokens"] += official.total_tokens
                    setting = selected.get(source_id) or {}
                    mapping_enabled = (
                        _text(setting.get("mapped_provider")) == provider
                        and _text(setting.get("mapped_account_id")) == proposed_account
                    )
                    declared_enabled = bool(setting.get("identity_enabled")) and bool(
                        prepared[source_id][4]
                    )
                    if mapping_enabled or declared_enabled:
                        enabled_sources.append(source_id)
                        stats["identity_enabled_minutes"] += 1
                        stats["identity_enabled_tokens"] += official.total_tokens
                if enabled_sources:
                    identity_supplement_minutes += 1
                    identity_supplement_tokens += official.total_tokens
            elif len(proposals) > 1:
                identity_conflicts += 1
                for source_ids in proposals.values():
                    for source_id in source_ids:
                        note(
                            source_stats(source_id),
                            "cross_source_identity_conflict",
                            identity_only=True,
                        )
            continue

        provider_minute = (key[0], key[2])
        if provider_minute in local_provider_minutes:
            cross_session_official_ambiguities += 1
            for source_id, (usage, _account, _conflict, _complete, _declared) in prepared.items():
                note(
                    source_stats(source_id),
                    "possible_official_cross_session_collision",
                    tokens=usage.total_tokens,
                    blocked=True,
                )
            continue

        event_signatures: dict[tuple[object, ...], set[str]] = {}
        for source_id, rows in by_source.items():
            for row in rows:
                row_usage = _usage_from_dict(row.get("usage"))
                signature = (
                    _text(row.get("event_at")),
                    row_usage.input_tokens,
                    row_usage.output_tokens,
                    row_usage.cache_read_tokens,
                    row_usage.cache_write_tokens,
                    row_usage.cache_write_1h_tokens,
                    row_usage.reasoning_tokens,
                    row_usage.calls,
                )
                event_signatures.setdefault(signature, set()).add(source_id)
        if any(len(claimants) > 1 for claimants in event_signatures.values()):
            cross_source_collisions += 1
            for source_id, (usage, _account, _conflict, _complete, _declared) in prepared.items():
                note(
                    source_stats(source_id),
                    "cross_source_token_collision",
                    tokens=usage.total_tokens,
                    blocked=True,
                )
            continue

        for source_id, (
            usage,
            account,
            account_conflict,
            identity_complete,
            _declared_complete,
        ) in prepared.items():
            stats = source_stats(source_id)
            token_candidates += 1
            token_candidate_tokens += usage.total_tokens
            stats["token_candidate_minutes"] += 1
            stats["token_candidate_tokens"] += usage.total_tokens
            enabled = bool((selected.get(source_id) or {}).get("totals_enabled"))
            if enabled:
                token_enabled_minutes += 1
                token_enabled_tokens += usage.total_tokens
                stats["token_enabled_minutes"] += 1
                stats["token_enabled_tokens"] += usage.total_tokens
            token_claims.append(
                {
                    "provider": key[0],
                    "session_id": key[1],
                    "minute": key[2],
                    "source_id": source_id,
                    "account_id": account
                    if account and not account_conflict and identity_complete
                    else "",
                    "usage": usage,
                }
            )
            if account_conflict:
                note(stats, "identity_conflict", identity_only=True)

    ordered_sources = sorted(sources.values(), key=lambda row: row["source_id"])
    result = {
        "candidate_only": not bool(identity_supplement_minutes or token_enabled_minutes),
        "identity_included_in_report": bool(identity_supplement_minutes),
        "identity_supplement_minutes": identity_supplement_minutes,
        "identity_supplement_tokens": identity_supplement_tokens,
        "tokens_included_in_report": bool(token_enabled_minutes),
        "token_enabled_minutes": token_enabled_minutes,
        "token_enabled_tokens": token_enabled_tokens,
        "included_in_report": bool(identity_supplement_minutes or token_enabled_minutes),
        "admission_ready": bool(token_candidates),
        "collision_key": "provider+session_id+exact_event_signature",
        "ambiguity_key": "provider+utc_minute",
        "known_limitations": [
            "official_cross_session_overlaps_remain_blocked",
            "full_window_inference_requires_explicit_source_authorization",
        ],
        "official_minutes": len(local),
        "user_source_minutes": len(grouped),
        "official_overlap_minutes": official_overlap_minutes,
        "identity_candidates": identity_candidates,
        "identity_candidate_tokens": identity_candidate_tokens,
        "token_candidates": token_candidates,
        "token_candidate_tokens": token_candidate_tokens,
        "cross_source_collisions": cross_source_collisions,
        "cross_session_official_ambiguities": cross_session_official_ambiguities,
        "concurrent_user_provider_minutes": len(concurrent_user_provider_minutes),
        "cross_session_user_ambiguities": 0,
        "identity_conflicts": identity_conflicts,
        "blocked_source_minutes": sum(row["blocked_source_minutes"] for row in ordered_sources),
        "blocked_source_tokens": sum(row["blocked_source_tokens"] for row in ordered_sources),
        "candidate_sources": [
            row["source_id"]
            for row in ordered_sources
            if row["identity_candidate_minutes"] or row["token_candidate_minutes"]
        ],
        "sources": ordered_sources,
    }
    if _include_token_claims:
        result["_token_claims"] = token_claims
    return result


def apply_user_identity_evidence(
    sessions: Iterable[SessionRecord],
    settings: dict[str, dict[str, Any]] | None = None,
    store: TelemetryStore | None = None,
) -> list[SessionRecord]:
    """Attach reversible, exact-match identity evidence to first-party minutes.

    The returned sessions keep their original Token totals.  A user source can
    supplement identity only after the user maps that source to an account and
    its normalized minute exactly matches the existing local minute. Existing
    first-party account identities are never overwritten.
    """
    original = list(sessions)
    selected = settings or {}
    if not selected:
        return original

    existing_accounts: dict[tuple[str, str, str], set[str]] = {}
    for session in original:
        if (
            not session.account_id
            or session.timeline is None
            or session.extra.get("account_identity_scope")
            == "session_timeline_majority"
        ):
            continue
        for at, usage in session.timeline:
            minute = _minute(at)
            if minute and usage.total_tokens > 0:
                existing_accounts.setdefault(
                    (session.provider, session.session_id, minute), set()
                ).add(session.account_id)

    claims: dict[tuple[str, str, str], dict[str, set[str]]] = {}
    shadow = reconcile_telemetry_minutes(original, store or TelemetryStore())
    for source_row in shadow.get("by_source", []):
        source_id = _text(source_row.get("source_id"))
        setting = selected.get(source_id) or {}
        mapped_provider = _text(setting.get("mapped_provider"))
        mapped_account = _text(setting.get("mapped_account_id"))
        declared_enabled = bool(setting.get("identity_enabled"))
        if not declared_enabled and (not mapped_provider or not mapped_account):
            continue
        for match in source_row.get("matches", []):
            provider = _text(match.get("provider"))
            session_id = _text(match.get("session_id"))
            minute = _minute(match.get("minute"))
            declared_account = _text(match.get("account_id"))
            if not session_id or not minute:
                continue
            if declared_enabled:
                if not declared_account:
                    continue
                selected_account = declared_account
            else:
                if provider != mapped_provider or (
                    declared_account and declared_account != mapped_account
                ):
                    continue
                selected_account = mapped_account
            key = (provider, session_id, minute)
            official_accounts = existing_accounts.get(key) or set()
            if official_accounts:
                # The existing local record already has identity. A matching
                # user claim is redundant; a different claim must not replace it.
                continue
            claims.setdefault(key, {}).setdefault(selected_account, set()).add(
                f"user_adapter:{source_id}"
            )

    accepted = {
        key: next(iter(by_account.items()))
        for key, by_account in claims.items()
        if len(by_account) == 1
    }
    if not accepted:
        return original

    out: list[SessionRecord] = []
    for session in original:
        if session.timeline is None:
            out.append(session)
            continue
        overlay: dict[str, dict[str, Any]] = {}
        for at, usage in session.timeline:
            minute = _minute(at)
            if not minute or usage.total_tokens <= 0:
                continue
            claim = accepted.get((session.provider, session.session_id, minute))
            if claim is None:
                continue
            account_id, sources = claim
            overlay[minute] = {
                "account_id": account_id,
                "evidence_sources": sorted(sources),
                "full_window_inference_allowed": all(
                    bool(
                        (selected.get(source.removeprefix("user_adapter:")) or {}).get(
                            "full_window_enabled"
                        )
                    )
                    for source in sources
                ),
            }
        if not overlay:
            out.append(session)
            continue
        extra = dict(session.extra)
        extra["minute_identity_evidence"] = overlay
        out.append(replace(session, extra=extra))
    return out


def apply_user_token_evidence(
    sessions: Iterable[SessionRecord],
    settings: dict[str, dict[str, Any]] | None = None,
    store: TelemetryStore | None = None,
) -> list[SessionRecord]:
    """Read explicitly enabled, non-colliding user Token evidence into a report.

    Nothing is written into cache.sqlite. Disabling the source removes these
    synthetic read-time sessions, while official minutes and ambiguous user
    minutes remain untouched.
    """
    original = list(sessions)
    selected = settings or {}
    if not any(bool(row.get("totals_enabled")) for row in selected.values()):
        return original
    ledger = candidate_user_source_ledger(
        original,
        settings=selected,
        store=store,
        _include_token_claims=True,
    )
    grouped: dict[tuple[str, str, str, str], list[tuple[str, TokenUsage]]] = {}
    for claim in ledger.get("_token_claims", []):
        source_id = _text(claim.get("source_id"))
        if not bool((selected.get(source_id) or {}).get("totals_enabled")):
            continue
        key = (
            source_id,
            _text(claim.get("provider")),
            _text(claim.get("session_id")),
            _text(claim.get("account_id")),
        )
        grouped.setdefault(key, []).append(
            (_text(claim.get("minute")), claim["usage"])
        )

    supplemented: list[SessionRecord] = []
    for (source_id, provider, native_session_id, account_id), rows in sorted(
        grouped.items()
    ):
        timeline = sorted(rows, key=lambda item: item[0])
        usage = TokenUsage()
        daily: dict[str, TokenUsage] = {}
        for at, delta in timeline:
            usage = usage.add(delta)
            moment = parse_ts(at)
            if moment is not None:
                day = moment.astimezone().date().isoformat()
                daily[day] = daily.get(day, TokenUsage()).add(delta)
        opaque_id = hashlib.sha256(
            f"{source_id}\0{provider}\0{native_session_id}".encode("utf-8")
        ).hexdigest()[:20]
        supplemented.append(
            SessionRecord(
                provider=provider,
                session_id=f"user-{opaque_id}",
                path="",
                started_at=timeline[0][0],
                ended_at=timeline[-1][0],
                client=source_id,
                account_id=account_id,
                usage=usage,
                daily=daily,
                timeline=timeline,
                extra={
                    "evidence_class": "B_token",
                    "evidence_sources": [f"user_adapter:{source_id}"],
                    "user_source": source_id,
                    "full_window_inference_allowed": bool(
                        (selected.get(source_id) or {}).get("full_window_enabled")
                    ),
                },
            )
        )
    return original + supplemented


def apply_user_evidence(
    sessions: Iterable[SessionRecord],
    settings: dict[str, dict[str, Any]] | None = None,
    store: TelemetryStore | None = None,
) -> list[SessionRecord]:
    """Apply reversible identity evidence, then reversible Token evidence."""
    original = list(sessions)
    selected = settings or {}
    identified = apply_user_identity_evidence(original, settings=selected, store=store)
    return apply_user_token_evidence(identified, settings=selected, store=store)


def _local_minute_ledger(
    sessions: Iterable[SessionRecord],
) -> dict[tuple[str, str, str], TokenUsage]:
    local: dict[tuple[str, str, str], TokenUsage] = {}
    for session in sessions:
        if not session.session_id or session.timeline is None:
            continue
        for at, usage in session.timeline:
            minute = _minute(at)
            if not minute or usage.total_tokens <= 0:
                continue
            key = (session.provider, session.session_id, minute)
            local[key] = local.get(key, TokenUsage()).add(usage)
    return local


def _reconcile_rows(
    local: dict[tuple[str, str, str], TokenUsage],
    rows: Iterable[dict[str, Any]],
    *,
    require_identity: bool,
) -> dict[str, Any]:
    """Compare one independently selected telemetry row set with local minutes."""
    telemetry: dict[tuple[str, str, str], dict[str, TokenUsage]] = {}
    uncorrelated = uncorrelated_tokens = 0
    for row in rows:
        account_id = _text(row.get("account_id"))
        session_id = _text(row.get("session_id"))
        minute = _minute(row.get("event_at"))
        provider = _text(row.get("provider"))
        usage = _usage_from_dict(row.get("usage"))
        if require_identity and not account_id:
            continue
        if usage.total_tokens <= 0:
            continue
        if not provider or not session_id or not minute:
            uncorrelated += 1
            uncorrelated_tokens += usage.total_tokens
            continue
        key = (provider, session_id, minute)
        by_account = telemetry.setdefault(key, {})
        by_account[account_id] = by_account.get(account_id, TokenUsage()).add(usage)

    matches: list[dict[str, Any]] = []
    conflicts = token_mismatches = missing_local = 0
    matched_tokens = missing_local_tokens = 0
    for key, by_account in sorted(telemetry.items()):
        if len(by_account) != 1:
            conflicts += 1
            continue
        expected = local.get(key)
        if expected is None:
            missing_local += 1
            missing_local_tokens += sum(
                usage.total_tokens for usage in by_account.values()
            )
            continue
        account_id, observed = next(iter(by_account.items()))
        if observed != expected:
            token_mismatches += 1
            continue
        provider, session_id, minute = key
        matched_tokens += expected.total_tokens
        matches.append(
            {
                "provider": provider,
                "session_id": session_id,
                "minute": minute,
                "account_id": account_id,
                "usage": expected.as_dict(),
            }
        )
    return {
        "identity_groups": len(telemetry),
        "exact_matches": len(matches),
        "matched_tokens": matched_tokens,
        "token_mismatches": token_mismatches,
        "missing_local": missing_local,
        "missing_local_tokens": missing_local_tokens,
        "uncorrelated": uncorrelated,
        "uncorrelated_tokens": uncorrelated_tokens,
        "unmatched_records": missing_local + uncorrelated,
        "account_conflicts": conflicts,
        "matches": matches,
    }


def empty_otlp_response() -> bytes:
    return ExportLogsServiceResponse().SerializeToString()


def ingest_otlp_logs(
    payload: bytes,
    store: TelemetryStore | None = None,
    *,
    allowed_providers: Collection[str] | Callable[[str], bool] | None = None,
) -> dict[str, int]:
    """Ingest OTLP records, optionally limiting the active provider streams.

    The receiver is a shared transport, but an enabled-provider predicate keeps
    one platform from silently becoming a data source for the other platforms.
    ``None`` preserves the standalone collector behaviour used by the CLI and
    existing integrations.
    """
    def provider_allowed(provider: str) -> bool:
        if allowed_providers is None:
            return True
        if callable(allowed_providers):
            return bool(allowed_providers(provider))
        return provider in allowed_providers

    request = ExportLogsServiceRequest()
    request.ParseFromString(payload)
    records: list[TelemetryRecord] = []
    receipts: dict[str, dict[str, int]] = {}
    rejected = 0
    for resource_logs in request.resource_logs:
        resource = _attributes(resource_logs.resource.attributes)
        for scope_logs in resource_logs.scope_logs:
            for log in scope_logs.log_records:
                attrs = dict(resource)
                attrs.update(_attributes(log.attributes))
                body = _scalar(log.body)
                wire_name = _log_event_name(log, attrs, body)
                provider = _provider_hint(resource, attrs, wire_name)
                if not provider_allowed(provider):
                    continue
                record = _normalize_log(resource, log, attrs=attrs, wire_name=wire_name)
                if provider:
                    receipt = receipts.setdefault(
                        provider,
                        {"received_records": 0, "accepted_records": 0, "rejected_records": 0},
                    )
                    receipt["received_records"] += 1
                if record is None:
                    rejected += 1
                    if provider:
                        receipt["rejected_records"] += 1
                else:
                    records.append(record)
                    if provider:
                        receipt["accepted_records"] += 1
    target = store or TelemetryStore()
    inserted, duplicates = target.append(records)
    received_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    target.record_receipts(receipts, received_at)
    return {
        "accepted": len(records),
        "inserted": inserted,
        "duplicates": duplicates,
        "rejected": rejected,
    }


def prepare_user_source_records(
    envelope: object,
) -> tuple[str, list[TelemetryRecord], dict[str, Any]]:
    """Validate one normalized batch and return its canonical in-memory rows."""

    if not isinstance(envelope, dict):
        raise ValueError("normalized user source envelope must be an object")
    unknown_envelope = set(envelope) - {"source", "records"}
    if unknown_envelope:
        raise ValueError("unsupported normalized envelope field")
    source = _custom_source(envelope.get("source"))
    if not source:
        raise ValueError("normalized records require a stable source id")
    values = envelope.get("records")
    if not isinstance(values, list) or not values:
        raise ValueError("normalized records require a non-empty records array")
    if len(values) > MAX_USER_SOURCE_RECORDS:
        raise ValueError("too many normalized records in one request")

    received_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    rows: list[TelemetryRecord] = []
    receipts: dict[str, dict[str, int]] = {}
    signature_events: dict[str, list[str]] = {}
    signature_details: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise ValueError(f"normalized record {index} must be an object")
        unknown = set(value) - _USER_RECORD_FIELDS
        if unknown:
            raise ValueError(f"normalized record {index} has unsupported fields")
        provider = _custom_provider(value.get("provider"))
        event_id = _text(value.get("event_id"), 256)
        raw_timestamp = value.get("timestamp")
        event_time = parse_ts(raw_timestamp)
        session_id = _text(value.get("session_id"), 256)
        account_id = _text(value.get("account_id"), 256)
        has_zone = isinstance(raw_timestamp, str) and bool(
            re.search(r"(?:Z|[+-]\d{2}:\d{2})$", raw_timestamp.strip())
        )
        if (
            not provider
            or not event_id
            or event_time is None
            or not has_zone
            or not session_id
        ):
            raise ValueError(
                f"normalized record {index} requires provider, event_id, "
                "timezone-qualified timestamp and session_id"
            )
        usage = TokenUsage(
            input_tokens=_strict_token(value, "input_tokens", index),
            output_tokens=_strict_token(value, "output_tokens", index),
            cache_read_tokens=_strict_token(value, "cache_read_tokens", index),
            cache_write_tokens=_strict_token(value, "cache_write_tokens", index),
            cache_write_1h_tokens=_strict_token(
                value, "cache_write_1h_tokens", index
            ),
            reasoning_tokens=_strict_token(value, "reasoning_tokens", index),
            calls=_strict_token(value, "calls", index, default=1),
        )
        declared_total = _strict_token(value, "total_tokens", index)
        if declared_total <= 0 or declared_total != usage.total_tokens:
            raise ValueError(
                f"normalized record {index} total_tokens must equal its Token buckets"
            )
        if usage.reasoning_tokens > usage.output_tokens:
            raise ValueError(
                f"normalized record {index} reasoning_tokens exceeds output_tokens"
            )
        at = event_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        canonical = {
            "provider": provider,
            "event": "sandglass.usage",
            "at": at,
            "account": account_id,
            "session": session_id,
            "model": _text(value.get("model"), 160),
            "correlation": event_id,
            "usage": usage.as_dict(),
            "source": source,
        }
        event_key = f"{provider}:" + hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        signature = json.dumps(
            {
                "provider": provider,
                "at": at,
                "account": account_id,
                "session": session_id,
                "usage": usage.as_dict(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        signature_events.setdefault(signature, []).append(event_id)
        signature_details[signature] = {
            "provider": provider,
            "timestamp": at,
            "account_id": account_id,
            "session_id": session_id,
            "total_tokens": usage.total_tokens,
        }
        rows.append(
            TelemetryRecord(
                event_key=event_key,
                provider=provider,
                event_name="sandglass.usage",
                event_at=at,
                received_at=received_at,
                account_id=account_id,
                session_id=session_id,
                model=canonical["model"],
                source=f"user_adapter:{source}",
                source_version=_text(value.get("source_version"), 80),
                schema_version=_text(value.get("schema_version"), 40) or "1",
                evidence_grade="U-A" if account_id else "U-B",
                coverage_state=(
                    "user_attributed"
                    if account_id
                    else "user_unassigned_missing_identity"
                ),
                usage=usage,
            )
        )
        receipt = receipts.setdefault(
            provider,
            {"received_records": 0, "accepted_records": 0, "rejected_records": 0},
        )
        receipt["received_records"] += 1
        receipt["accepted_records"] += 1

    unique_rows = {row.event_key: row for row in rows}
    signature_duplicates = []
    for signature, event_ids in signature_events.items():
        if len(event_ids) < 2:
            continue
        signature_duplicates.append(
            {
                **signature_details[signature],
                "records": len(event_ids),
                "event_ids": event_ids[:20],
            }
        )
    validation = {
        "validated": len(rows),
        "unique_event_keys": len(unique_rows),
        "duplicate_event_keys": len(rows) - len(unique_rows),
        "normalized_tokens": sum(
            row.usage.total_tokens for row in unique_rows.values()
        ),
        "first_event_at": min((row.event_at for row in rows), default=None),
        "last_event_at": max((row.event_at for row in rows), default=None),
        "exact_signature_duplicate_groups": len(signature_duplicates),
        "exact_signature_duplicate_records": sum(
            item["records"] - 1 for item in signature_duplicates
        ),
        "duplicate_signatures": signature_duplicates[:20],
        "ready_to_persist": (
            len(rows) == len(unique_rows) and not signature_duplicates
        ),
        "source": source,
    }
    return source, list(unique_rows.values()), validation


def user_source_record_dicts(source: str, records: object) -> list[dict[str, Any]]:
    """Convert one validated package batch to the normal telemetry read shape."""

    _source, rows, validation = prepare_user_source_records(
        {"source": source, "records": records}
    )
    if not validation["ready_to_persist"]:
        raise ValueError("active user source revision contains duplicate normalized records")
    out = []
    for row in rows:
        out.append(
            {
                "event_key": row.event_key,
                "provider": row.provider,
                "event_name": row.event_name,
                "event_at": row.event_at,
                "received_at": row.received_at,
                "account_id": row.account_id,
                "session_id": row.session_id,
                "model": row.model,
                "source": row.source,
                "source_version": row.source_version,
                "schema_version": row.schema_version,
                "evidence_grade": row.evidence_grade,
                "coverage_state": row.coverage_state,
                "usage": row.usage.as_dict(),
            }
        )
    return out


def ingest_user_source_records(
    envelope: object,
    store: TelemetryStore | None = None,
) -> dict[str, Any]:
    """Store legacy normalized JSON without making it accounting authority."""

    if not isinstance(envelope, dict):
        raise ValueError("normalized user source envelope must be an object")
    unknown_envelope = set(envelope) - {"source", "records", "validate_only"}
    if unknown_envelope:
        raise ValueError("unsupported normalized envelope field")
    validate_only = envelope.get("validate_only", False)
    if not isinstance(validate_only, bool):
        raise ValueError("validate_only must be a boolean")
    source, rows, validation = prepare_user_source_records(
        {"source": envelope.get("source"), "records": envelope.get("records")}
    )

    target = store or TelemetryStore()
    if validate_only:
        existing = target.existing_event_keys(row.event_key for row in rows)
        return {
            **validation,
            "existing_event_keys": len(existing),
            "would_insert": len(rows) - len(existing),
            "ready_to_persist": (
                bool(validation["ready_to_persist"]) and not existing
            ),
            "persisted": False,
        }
    inserted, duplicates = target.append(rows)
    receipts: dict[str, dict[str, int]] = {}
    for row in rows:
        receipt = receipts.setdefault(
            row.provider,
            {"received_records": 0, "accepted_records": 0, "rejected_records": 0},
        )
        receipt["received_records"] += 1
        receipt["accepted_records"] += 1
    received_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    target.record_receipts(receipts, received_at)
    return {
        "accepted": len(rows),
        "inserted": inserted,
        "duplicates": duplicates,
        "rejected": 0,
        "source": source,
    }


def official_minute_rows(
    sessions: Iterable[SessionRecord],
    *,
    provider: str = "",
    since: Any = None,
    until: Any = None,
) -> list[dict[str, Any]]:
    """Expose the prompt-free first-party minute ledger for an exact A-class join."""

    selected_provider = _custom_provider(provider) if provider else ""
    if provider and selected_provider not in PROVIDERS:
        raise ValueError("official minute export requires a built-in provider")
    start = parse_ts(since)
    end = parse_ts(until)
    if since not in (None, "") and start is None:
        raise ValueError("since must be a valid timestamp")
    if until not in (None, "") and end is None:
        raise ValueError("until must be a valid timestamp")
    if start is not None and end is not None and start >= end:
        raise ValueError("since must be earlier than until")
    grouped: dict[tuple[str, str, str], TokenUsage] = {}
    for session in sessions:
        if selected_provider and session.provider != selected_provider:
            continue
        for at, usage in session.timeline or []:
            moment = parse_ts(at)
            if moment is None or usage.total_tokens <= 0:
                continue
            if start is not None and moment < start:
                continue
            if end is not None and moment >= end:
                continue
            minute = _minute(moment)
            key = (session.provider, session.session_id, minute)
            grouped[key] = grouped.get(key, TokenUsage()).add(usage)
    return [
        {
            "provider": provider_name,
            "session_id": session_id,
            "timestamp": minute,
            **usage.as_dict(),
        }
        for (provider_name, session_id, minute), usage in sorted(grouped.items())
    ]


def _normalize_log(
    resource: dict[str, Any],
    log,
    *,
    attrs: dict[str, Any] | None = None,
    wire_name: str = "",
) -> TelemetryRecord | None:
    if attrs is None:
        attrs = dict(resource)
        attrs.update(_attributes(log.attributes))
    if not wire_name:
        wire_name = _log_event_name(log, attrs, _scalar(log.body))
    provider, event_name = _provider_event(resource, attrs, wire_name)
    if not provider:
        return None

    custom = event_name == "sandglass.usage"
    usage = _provider_usage(provider, attrs, custom=custom)
    if usage is None or usage.total_tokens <= 0:
        return None

    event_at = _event_time(attrs.get("event.timestamp"), log.time_unix_nano, log.observed_time_unix_nano)
    if not event_at:
        return None

    account_id = _account_id(provider, attrs, custom=custom)
    session_id = _text(
        (attrs.get("sandglass.session_id") if custom else None)
        or attrs.get("session.id")
        or attrs.get("conversation.id"),
        256,
    )
    model = _text((attrs.get("sandglass.model") if custom else None) or attrs.get("model"), 160)
    source = (
        f"user_adapter:{_custom_source(attrs.get('sandglass.source'))}"
        if custom
        else "official_otel"
    )
    source_version = _text(
        (attrs.get("sandglass.source_version") if custom else None)
        or attrs.get("app.version")
        or resource.get("client.version")
        or resource.get("service.version"),
        80,
    )
    schema_version = _text(
        (attrs.get("sandglass.schema_version") if custom else None)
        or resource.get("grok_code.schema.version")
        or attrs.get("grok_code.schema.version"),
        40,
    )
    grade = ("U-A" if account_id else "U-B") if custom else ("A" if account_id else "B")
    state = (
        "user_attributed" if account_id else "user_unassigned_missing_identity"
    ) if custom else ("attributed" if account_id else "unassigned_missing_identity")
    received_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    event_key = _event_key(
        provider, event_name, event_at, account_id, session_id, model, usage,
        attrs, log, custom=custom,
    )
    return TelemetryRecord(
        event_key=event_key,
        provider=provider,
        event_name=event_name,
        event_at=event_at,
        received_at=received_at,
        account_id=account_id,
        session_id=session_id,
        model=model,
        source=source,
        source_version=source_version,
        schema_version=schema_version,
        evidence_grade=grade,
        coverage_state=state,
        usage=usage,
    )


def _log_event_name(log, attrs: dict[str, Any], body: Any = "") -> str:
    """The business event name, not the tracing target that exported it.

    Codex 0.149's OpenTelemetry tracing bridge fills LogRecord.event_name with
    the target ``codex_otel.log_only``. The real name stays on the ``event.name``
    attribute. Preferring the protobuf field made every completion look like
    unidentified traffic.
    """
    named = _text(attrs.get("event.name"), 120)
    if named:
        return named
    record = _text(getattr(log, "event_name", ""), 120)
    if record and "otel" not in record.casefold():
        return record
    return _text(body, 120) or record


def _first_token(attrs: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if key in attrs:
            return _token(attrs[key])
    return 0


def _provider_event(
    resource: dict[str, Any], attrs: dict[str, Any], wire_name: str
) -> tuple[str, str]:
    service = _text(resource.get("service.name") or attrs.get("service.name"), 120).lower()
    name = wire_name.lower()
    kind = _text(
        attrs.get("event.kind") or attrs.get("event_kind") or attrs.get("kind"),
        120,
    ).lower()
    if name in {"api_request", "claude_code.api_request"} and service.startswith("claude-code"):
        return "claude", "claude_code.api_request"
    if (
        "codex" in service or name.startswith("codex.") or name == "codex.sse_event"
    ) and kind in {"response.completed", "completed"}:
        return "codex", "codex.sse_event/response.completed"
    if name == "grok_code.api_request":
        return "grok", "grok_code.api_request"
    if name == "sandglass.usage":
        provider = _custom_provider(attrs.get("sandglass.provider"))
        source = _custom_source(attrs.get("sandglass.source"))
        if provider and source:
            return provider, "sandglass.usage"
    return "", ""


def _provider_usage(
    provider: str, attrs: dict[str, Any], *, custom: bool = False
) -> TokenUsage | None:
    if custom:
        usage = TokenUsage(
            input_tokens=_token(attrs.get("sandglass.input_tokens")),
            output_tokens=_token(attrs.get("sandglass.output_tokens")),
            cache_read_tokens=_token(attrs.get("sandglass.cache_read_tokens")),
            cache_write_tokens=_token(attrs.get("sandglass.cache_write_tokens")),
            cache_write_1h_tokens=_token(attrs.get("sandglass.cache_write_1h_tokens")),
            reasoning_tokens=_token(attrs.get("sandglass.reasoning_tokens")),
            calls=_token(attrs.get("sandglass.calls")) or 1,
        )
        declared_total = _token(attrs.get("sandglass.total_tokens"))
        if declared_total <= 0 or declared_total != usage.total_tokens:
            return None
        if usage.reasoning_tokens > usage.output_tokens:
            return None
        return usage
    if provider == "claude":
        output = _token(attrs.get("output_tokens"))
        reasoning = _token(attrs.get("reasoning_tokens"))
        if reasoning > output:
            return None
        return TokenUsage(
            input_tokens=_token(attrs.get("input_tokens")),
            output_tokens=output,
            cache_read_tokens=_token(attrs.get("cache_read_tokens")),
            cache_write_tokens=_token(attrs.get("cache_creation_tokens")),
            reasoning_tokens=reasoning,
            calls=1,
        )
    if provider == "codex":
        total_input = _first_token(
            attrs, "input_token_count", "gen_ai.usage.input_tokens"
        )
        cached = _first_token(
            attrs,
            "cached_token_count",
            "gen_ai.usage.cache_read.input_tokens",
        )
        cache_write = _first_token(
            attrs,
            "cache_write_token_count",
            "gen_ai.usage.cache_write.input_tokens",
        )
        output = _first_token(
            attrs, "output_token_count", "gen_ai.usage.output_tokens"
        )
        reasoning = _first_token(
            attrs,
            "reasoning_token_count",
            "codex.usage.reasoning_output_tokens",
        )
        reported_total = _first_token(
            attrs, "tool_token_count", "codex.usage.total_tokens"
        )
        if cached + cache_write > total_input or reasoning > output:
            return None
        if (
            "input_token_count" in attrs
            and reported_total
            and reported_total != total_input + output
        ):
            return None
        return TokenUsage(
            input_tokens=total_input - cached - cache_write,
            output_tokens=output,
            cache_read_tokens=cached,
            cache_write_tokens=cache_write,
            reasoning_tokens=reasoning,
            calls=1,
        )
    if provider == "grok":
        total_input = _token(attrs.get("input_tokens"))
        cached = _token(attrs.get("cache_read_tokens"))
        output = _token(attrs.get("output_tokens"))
        reasoning = _token(attrs.get("reasoning_tokens"))
        if cached > total_input or reasoning > output:
            return None
        return TokenUsage(
            input_tokens=total_input - cached,
            output_tokens=output,
            cache_read_tokens=cached,
            reasoning_tokens=reasoning,
            calls=1,
        )
    return None


def _account_id(provider: str, attrs: dict[str, Any], *, custom: bool = False) -> str:
    if custom:
        return _text(attrs.get("sandglass.account_id"), 256)
    if provider == "claude":
        return _text(attrs.get("user.account_id") or attrs.get("user.account_uuid"), 256)
    if provider == "codex":
        return _text(attrs.get("user.account_id"), 256)
    if provider == "grok":
        return _text(attrs.get("user.id"), 256)
    return ""


def _event_key(
    provider: str,
    event_name: str,
    event_at: str,
    account_id: str,
    session_id: str,
    model: str,
    usage: TokenUsage,
    attrs: dict[str, Any],
    log,
    *,
    custom: bool = False,
) -> str:
    correlation = ""
    if custom:
        correlation = _text(attrs.get("sandglass.event_id"), 256)
    elif provider == "claude":
        correlation = _text(attrs.get("request_id"), 256)
    elif provider == "grok":
        prompt = _text(attrs.get("prompt.id"), 256)
        sequence = _text(attrs.get("event.sequence"), 80)
        correlation = f"{prompt}:{sequence}" if prompt or sequence else ""
    if not correlation and (log.trace_id or log.span_id):
        correlation = f"{bytes(log.trace_id).hex()}:{bytes(log.span_id).hex()}"
    canonical = {
        "provider": provider,
        "event": event_name,
        "at": event_at,
        "account": account_id,
        "session": session_id,
        "model": model,
        "correlation": correlation,
        "usage": usage.as_dict(),
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"{provider}:{digest}"


def _event_time(attribute: Any, time_unix_nano: int, observed_time_unix_nano: int) -> str:
    parsed = parse_ts(attribute)
    if parsed is None:
        nanos = int(time_unix_nano or observed_time_unix_nano or 0)
        if nanos:
            try:
                parsed = datetime.fromtimestamp(nanos / 1_000_000_000, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                parsed = None
    if parsed is None:
        return ""
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _minute(value: Any) -> str:
    parsed = parse_ts(value)
    if parsed is None:
        return ""
    return (
        parsed.astimezone(timezone.utc)
        .replace(second=0, microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _provider_hint(resource: dict[str, Any], attrs: dict[str, Any], wire_name: str) -> str:
    provider, _ = _provider_event(resource, attrs, wire_name)
    if provider:
        return provider
    service = _text(resource.get("service.name") or attrs.get("service.name"), 120).lower()
    name = wire_name.lower()
    if service.startswith("claude-code") or name.startswith("claude_code."):
        return "claude"
    if "codex" in service or name.startswith("codex."):
        return "codex"
    if service == "grok-cli" or name.startswith("grok_code."):
        return "grok"
    if name == "sandglass.usage":
        return _custom_provider(attrs.get("sandglass.provider"))
    return ""


def _custom_provider(value: Any) -> str:
    provider = _text(value, 64).lower()
    if not _CUSTOM_PROVIDER_RE.fullmatch(provider):
        return ""
    return provider


def _custom_source(value: Any) -> str:
    source = _text(value, 120)
    if not _CUSTOM_SOURCE_RE.fullmatch(source):
        return ""
    return source


def _valid_observed_provider(value: Any) -> bool:
    provider = _text(value, 64).lower()
    return provider in PROVIDERS or bool(_CUSTOM_PROVIDER_RE.fullmatch(provider))


def _usage_from_dict(value: Any) -> TokenUsage:
    raw = value if isinstance(value, dict) else {}
    return TokenUsage(
        input_tokens=_token(raw.get("input_tokens")),
        output_tokens=_token(raw.get("output_tokens")),
        cache_read_tokens=_token(raw.get("cache_read_tokens")),
        cache_write_tokens=_token(raw.get("cache_write_tokens")),
        cache_write_1h_tokens=_token(raw.get("cache_write_1h_tokens")),
        reasoning_tokens=_token(raw.get("reasoning_tokens")),
        calls=_token(raw.get("calls")),
    )


def _attributes(values) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in values:
        value = _scalar(item.value)
        if isinstance(value, (str, int, float, bool, bytes)):
            out[str(item.key)] = value
    return out


def _scalar(value) -> Any:
    kind = value.WhichOneof("value")
    if kind is None:
        return ""
    if kind in {"string_value", "bool_value", "int_value", "double_value", "bytes_value"}:
        return getattr(value, kind)
    return ""


def _token(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _strict_token(
    row: dict[str, Any],
    field: str,
    index: int,
    *,
    default: int = 0,
) -> int:
    value = row.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            f"normalized record {index} {field} must be a non-negative integer"
        )
    return value


def _text(value: Any, limit: int = 256) -> str:
    if isinstance(value, bytes):
        value = value.hex()
    text = str(value or "").strip()
    return text[:limit]


__all__ = [
    "DecodeError",
    "MAX_OTLP_BODY",
    "TelemetryRecord",
    "TelemetryStore",
    "empty_otlp_response",
    "ingest_otlp_logs",
    "ingest_user_source_records",
    "prepare_user_source_records",
    "reconcile_telemetry_minutes",
    "telemetry_status",
    "user_source_record_dicts",
]
