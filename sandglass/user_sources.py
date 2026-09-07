from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sandglass.dbschema import apply_schema
from sandglass.models import parse_ts
from sandglass.paths import user_sources_db


MAX_USER_SOURCE_BODY = 4 * 1024 * 1024
MAX_USER_SOURCE_IMPORT_BODY = 16 * 1024 * 1024
MAX_USER_SOURCE_ACCOUNTS = 1_000
_SOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")
_REVISION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,159}$")
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ACCOUNT_FIELDS = {"provider", "account_id", "email", "label", "plan", "source_version"}
_QUOTA_FIELDS = {"provider", "account_id", "fetched_at", "plan", "windows", "source_version"}
_QUOTA_WINDOW_FIELDS = {"label", "used_percent", "resets_at", "window_minutes"}

# Recognition is descriptive, not a validation gate. Unknown keys are preserved.
_KNOWN_FIELDS = {
    "provider",
    "account_id",
    "account",
    "session_id",
    "conversation_id",
    "event_id",
    "timestamp",
    "observed_at",
    "model",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cache_write_1h_tokens",
    "total_tokens",
    "calls",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_source_receipts (
    receipt_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    received_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    recognized_fields_json TEXT NOT NULL,
    unknown_fields_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS user_source_receipts_source_time
ON user_source_receipts(source, received_at);
CREATE TABLE IF NOT EXISTS user_source_settings (
    source TEXT PRIMARY KEY,
    display_enabled INTEGER NOT NULL,
    accounts_enabled INTEGER NOT NULL DEFAULT 1,
    identity_enabled INTEGER NOT NULL DEFAULT 0,
    mapped_provider TEXT NOT NULL,
    mapped_account_id TEXT NOT NULL,
    totals_enabled INTEGER NOT NULL DEFAULT 0,
    full_window_enabled INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_source_accounts (
    source TEXT NOT NULL,
    provider TEXT NOT NULL,
    account_id TEXT NOT NULL,
    email TEXT NOT NULL,
    label TEXT NOT NULL,
    plan TEXT NOT NULL,
    source_version TEXT NOT NULL,
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    PRIMARY KEY(source, provider, account_id)
);
CREATE INDEX IF NOT EXISTS user_source_accounts_provider_id
ON user_source_accounts(provider, account_id);
CREATE TABLE IF NOT EXISTS user_source_quotas (
    source TEXT NOT NULL,
    provider TEXT NOT NULL,
    account_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    plan TEXT NOT NULL,
    source_version TEXT NOT NULL,
    windows_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(source, provider, account_id)
);
CREATE TABLE IF NOT EXISTS user_source_import_revisions (
    source TEXT NOT NULL,
    revision TEXT NOT NULL,
    package_sha256 TEXT NOT NULL,
    package_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    PRIMARY KEY(source, revision)
);
CREATE TABLE IF NOT EXISTS user_source_import_active (
    source TEXT PRIMARY KEY,
    revision TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _field_names(value: Any) -> set[str]:
    if not isinstance(value, dict):
        return set()
    return {str(key) for key in value}


def _timezone_qualified(value: str) -> bool:
    return bool(re.search(r"(?:Z|[+-]\d{2}:\d{2})$", str(value or "").strip()))


def _quota_window(value: object, quota_index: int, window_index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(
            f"user source quota {quota_index} window {window_index} must be an object"
        )
    if set(value) - _QUOTA_WINDOW_FIELDS:
        raise ValueError(
            f"user source quota {quota_index} window {window_index} has unsupported fields"
        )
    label = str(value.get("label") or "").strip()
    if not label or len(label) > 80:
        raise ValueError(
            f"user source quota {quota_index} window {window_index} requires a label"
        )
    used = value.get("used_percent")
    if used is not None:
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not 0 <= used <= 100:
            raise ValueError(
                f"user source quota {quota_index} window {window_index} used_percent is invalid"
            )
        used = float(used)
    resets_at = str(value.get("resets_at") or "").strip()
    if resets_at and (not _timezone_qualified(resets_at) or parse_ts(resets_at) is None):
        raise ValueError(
            f"user source quota {quota_index} window {window_index} resets_at is invalid"
        )
    minutes = value.get("window_minutes")
    if minutes is not None and (
        isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0
    ):
        raise ValueError(
            f"user source quota {quota_index} window {window_index} window_minutes is invalid"
        )
    return {
        "label": label,
        "used_percent": used,
        "resets_at": resets_at or None,
        "window_minutes": minutes,
    }


def _normalize_accounts(values: object) -> list[dict[str, str]]:
    if not isinstance(values, list):
        raise ValueError("user source import accounts must be an array")
    if len(values) > MAX_USER_SOURCE_ACCOUNTS:
        raise ValueError("too many user source accounts in one import")
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise ValueError(f"user source account {index} must be an object")
        if set(value) - _ACCOUNT_FIELDS:
            raise ValueError(f"user source account {index} has unsupported fields")
        provider = str(value.get("provider") or "").strip().lower()
        account_id = str(value.get("account_id") or "").strip()
        if not _PROVIDER_RE.fullmatch(provider) or not account_id:
            raise ValueError(
                f"user source account {index} requires provider and account_id"
            )
        fields = {
            key: str(value.get(key) or "").strip()
            for key in ("email", "label", "plan", "source_version")
        }
        if len(account_id) > 256 or any(len(text) > 256 for text in fields.values()):
            raise ValueError(f"user source account {index} field is too long")
        key = (provider, account_id)
        if key in seen:
            raise ValueError(f"user source account {index} is duplicated in this import")
        seen.add(key)
        rows.append({"provider": provider, "account_id": account_id, **fields})
    return rows


def _normalize_quotas(
    values: object, admitted: set[tuple[str, str]]
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise ValueError("user source import quotas must be an array")
    if len(values) > MAX_USER_SOURCE_ACCOUNTS:
        raise ValueError("too many user source quotas in one import")
    rows: list[dict[str, Any]] = []
    seen_observations: set[tuple[str, str, str]] = set()
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise ValueError(f"user source quota {index} must be an object")
        if set(value) - _QUOTA_FIELDS:
            raise ValueError(f"user source quota {index} has unsupported fields")
        provider = str(value.get("provider") or "").strip().lower()
        account_id = str(value.get("account_id") or "").strip()
        if (provider, account_id) not in admitted:
            raise ValueError(
                f"user source quota {index} requires an account in this import"
            )
        fetched_at = str(value.get("fetched_at") or "").strip()
        fetched = parse_ts(fetched_at)
        if not _timezone_qualified(fetched_at) or fetched is None:
            raise ValueError(f"user source quota {index} requires fetched_at with timezone")
        fetched_at = fetched.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        plan = str(value.get("plan") or "").strip()
        source_version = str(value.get("source_version") or "").strip()
        if len(plan) > 256 or len(source_version) > 256:
            raise ValueError(f"user source quota {index} field is too long")
        windows = value.get("windows")
        if not isinstance(windows, list):
            raise ValueError(f"user source quota {index} windows must be an array")
        key = (provider, account_id, fetched_at)
        if key in seen_observations:
            raise ValueError(f"user source quota {index} is duplicated in this import")
        seen_observations.add(key)
        rows.append(
            {
                "provider": provider,
                "account_id": account_id,
                "fetched_at": fetched_at,
                "plan": plan,
                "windows": [
                    _quota_window(window, index, window_index)
                    for window_index, window in enumerate(windows)
                ],
                "source_version": source_version,
            }
        )
    return rows


def _latest_quotas(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for value in values:
        key = (str(value["provider"]), str(value["account_id"]))
        current = latest.get(key)
        if current is None or str(value["fetched_at"]) > str(current["fetched_at"]):
            latest[key] = value
    return sorted(
        latest.values(), key=lambda item: (item["provider"], item["account_id"])
    )


def _normalized_record_signature(row: dict[str, Any]) -> str:
    return _canonical(
        {
            "provider": row["provider"],
            "event_at": row["event_at"],
            "usage": row["usage"],
        }
    )


class UserSourceStore:
    """Revisioned user evidence kept outside cache.sqlite under SANDGLASS_HOME."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_sources_db()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        # These four settings columns were once reconciled by hand here. They
        # are declared in SCHEMA with their defaults, so the generic pass adds
        # them to an older database for the same reason and with the same
        # result -- and now every other table is covered too.
        apply_schema(conn, SCHEMA)
        return conn

    def _import_rows(self, source: str = "") -> list[dict[str, Any]]:
        """Read committed import metadata without creating product state."""

        if not self.path.exists():
            return []
        uri = self.path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            tables = {
                str(row["name"])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not {
                "user_source_import_revisions",
                "user_source_import_active",
            }.issubset(tables):
                return []
            where = "WHERE revisions.source = ?" if source else ""
            params: tuple[str, ...] = (source,) if source else ()
            return [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT revisions.source, revisions.revision,
                           revisions.package_sha256, revisions.summary_json,
                           revisions.committed_at,
                           CASE WHEN active.revision = revisions.revision
                                THEN 1 ELSE 0 END AS active
                    FROM user_source_import_revisions AS revisions
                    LEFT JOIN user_source_import_active AS active
                      ON active.source = revisions.source
                    {where}
                    ORDER BY revisions.source, revisions.committed_at, revisions.revision
                    """,
                    params,
                )
            ]
        finally:
            conn.close()

    def import_status(self, source: str = "") -> dict[str, Any]:
        source = str(source or "").strip()
        if source and not _SOURCE_RE.fullmatch(source):
            raise ValueError("user source import requires a stable source id")
        rows = self._import_rows(source)
        sources: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = sources.setdefault(
                str(row["source"]),
                {"source": str(row["source"]), "active_revision": None, "revisions": []},
            )
            revision = {
                "revision": str(row["revision"]),
                "package_sha256": str(row["package_sha256"]),
                "summary": json.loads(str(row["summary_json"])),
                "committed_at": str(row["committed_at"]),
                "active": bool(row["active"]),
            }
            revision["contract_version"] = int(
                revision["summary"].get("contract_version") or 2
            )
            if revision["active"]:
                item["active_revision"] = revision["revision"]
            item["revisions"].append(revision)
        ordered = [sources[key] for key in sorted(sources)]
        return {
            "contract_version": 2,
            "supported_contract_versions": [2, 3],
            "sources": ordered,
            "source": ordered[0] if source and ordered else None,
        }

    def _prepare_metering_import(
        self, package: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        from sandglass.metering import (
            metering_view,
            observation_signature,
            prepare_metering_package,
        )

        source = str(package.get("source") or "").strip()
        revision = str(package.get("revision") or "").strip()
        if not _SOURCE_RE.fullmatch(source):
            raise ValueError("metering import requires a stable source id")
        if not _REVISION_RE.fullmatch(revision):
            raise ValueError("metering import requires a stable revision id")
        candidate = {**package, "source": source, "revision": revision}
        normalized, check = prepare_metering_package(candidate)
        package_json = _canonical(normalized)
        if len(package_json.encode("utf-8")) > MAX_USER_SOURCE_IMPORT_BODY:
            raise ValueError("metering import package is too large")
        package_sha256 = hashlib.sha256(package_json.encode("utf-8")).hexdigest()

        active_packages = self.active_import_packages()
        active_status = self.import_status()
        current = next(
            (
                item
                for item in active_status.get("sources", [])
                if item["source"] == source
            ),
            None,
        )
        active_set: list[list[str]] = []
        for item in active_status.get("sources", []):
            active_row = next(
                (row for row in item.get("revisions", []) if row.get("active")),
                None,
            )
            if active_row:
                active_set.append(
                    [item["source"], active_row["revision"], active_row["package_sha256"]]
                )
        active_set_sha256 = hashlib.sha256(
            _canonical(sorted(active_set)).encode("utf-8")
        ).hexdigest()
        active_revision = current.get("active_revision") if current else None
        existing = next(
            (
                row
                for row in (current or {}).get("revisions", [])
                if row["revision"] == revision
            ),
            None,
        )
        blockers = list(check["blockers"])
        if existing and existing["package_sha256"] != package_sha256:
            blockers.append({"code": "immutable_revision_conflict", "revision": revision})

        candidate_signatures = set(check["observation_signatures"])
        exact_collisions: list[dict[str, str]] = []
        for active_package in active_packages:
            other_source = str(active_package.get("source") or "")
            if other_source == source or active_package.get("contract_version") != 3:
                continue
            for row in active_package.get("observations") or []:
                signature = observation_signature(row)
                if signature in candidate_signatures:
                    exact_collisions.append(
                        {
                            "source": other_source,
                            "observation_id": str(row.get("observation_id") or ""),
                            "signature": signature,
                        }
                    )
        if exact_collisions:
            blockers.append(
                {
                    "code": "active_metering_observation_collision",
                    "observations": len(exact_collisions),
                    "examples": exact_collisions[:20],
                }
            )

        summary = check["summary"]
        prepared = {
            "source": source,
            "revision": revision,
            "package_sha256": package_sha256,
            "package_json": package_json,
            "summary": summary,
            "record_signatures": [],
            "observation_signatures": sorted(candidate_signatures),
        }
        result = {
            "contract_version": 3,
            "source": source,
            "revision": revision,
            "package_sha256": package_sha256,
            "active_revision": active_revision,
            "active_set_sha256": active_set_sha256,
            "action": (
                "idempotent"
                if existing and existing["package_sha256"] == package_sha256
                else "create"
                if active_revision is None
                else "replace"
            ),
            "ready_to_commit": not blockers,
            "summary": summary,
            "metering_validation": metering_view([normalized]),
            "active_metering_collisions": exact_collisions,
            "blockers": blockers,
            "persisted": False,
        }
        return prepared, result

    def _prepare_import(self, package: object) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(package, dict):
            raise ValueError("user source import package must be an object")
        try:
            raw_package_json = _canonical(package)
        except (TypeError, ValueError) as exc:
            raise ValueError("user source import package must be JSON serializable") from exc
        if len(raw_package_json.encode("utf-8")) > MAX_USER_SOURCE_IMPORT_BODY:
            raise ValueError("user source import package is too large")
        if package.get("contract_version") == 3:
            return self._prepare_metering_import(package)
        required = {"source", "revision", "receipts", "accounts", "quotas", "records", "expected"}
        if set(package) != required:
            missing = sorted(required - set(package))
            unknown = sorted(set(package) - required)
            detail = []
            if missing:
                detail.append("missing " + ", ".join(missing))
            if unknown:
                detail.append("unsupported " + ", ".join(unknown))
            raise ValueError("invalid user source import package: " + "; ".join(detail))
        source = str(package.get("source") or "").strip()
        revision = str(package.get("revision") or "").strip()
        if not _SOURCE_RE.fullmatch(source):
            raise ValueError("user source import requires a stable source id")
        if not _REVISION_RE.fullmatch(revision):
            raise ValueError("user source import requires a stable revision id")
        receipts = package.get("receipts")
        if not isinstance(receipts, list) or not receipts:
            raise ValueError("user source import requires a non-empty receipts array")
        accounts = _normalize_accounts(package.get("accounts"))
        admitted = {(row["provider"], row["account_id"]) for row in accounts}
        quotas = _normalize_quotas(package.get("quotas"), admitted)
        records = package.get("records")
        if not isinstance(records, list):
            raise ValueError("user source import records must be an array")

        record_rows: list[dict[str, Any]] = []
        if records:
            from sandglass.telemetry import (
                ingest_user_source_records,
                user_source_record_dicts,
            )

            record_check = ingest_user_source_records(
                {"source": source, "records": records, "validate_only": True}
            )
            if (
                not record_check["duplicate_event_keys"]
                and not record_check["exact_signature_duplicate_records"]
            ):
                record_rows = user_source_record_dicts(source, records)
        else:
            record_check = {
                "validated": 0,
                "unique_event_keys": 0,
                "duplicate_event_keys": 0,
                "existing_event_keys": 0,
                "would_insert": 0,
                "normalized_tokens": 0,
                "first_event_at": None,
                "last_event_at": None,
                "exact_signature_duplicate_groups": 0,
                "exact_signature_duplicate_records": 0,
                "duplicate_signatures": [],
                "ready_to_persist": True,
                "persisted": False,
                "source": source,
            }

        expected = package.get("expected")
        expected_fields = {"receipts", "accounts", "quotas", "records", "total_tokens"}
        if not isinstance(expected, dict) or set(expected) != expected_fields:
            raise ValueError(
                "user source import expected must contain receipts, accounts, quotas, "
                "records and total_tokens"
            )
        for field in expected_fields:
            value = expected.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"user source import expected {field} must be non-negative")

        actual = {
            "receipts": len(receipts),
            "accounts": len(accounts),
            "quotas": len(quotas),
            "records": int(record_check["validated"]),
            "total_tokens": int(record_check["normalized_tokens"]),
        }
        blockers: list[dict[str, Any]] = []
        for field in sorted(expected_fields):
            if int(expected[field]) != actual[field]:
                blockers.append(
                    {
                        "code": "manifest_mismatch",
                        "field": field,
                        "expected": int(expected[field]),
                        "actual": actual[field],
                    }
                )
        if not record_check["ready_to_persist"]:
            blockers.append(
                {
                    "code": "record_duplicates",
                    "duplicate_event_keys": int(record_check["duplicate_event_keys"]),
                    "exact_signature_duplicate_records": int(
                        record_check["exact_signature_duplicate_records"]
                    ),
                }
            )
        active_collisions: list[dict[str, Any]] = []
        if record_rows:
            from sandglass.telemetry import user_source_record_dicts

            candidate_signatures = {
                _normalized_record_signature(row): row for row in record_rows
            }
            for active_package in self.active_import_packages():
                other_source = str(active_package["source"])
                if other_source == source:
                    continue
                for row in user_source_record_dicts(
                    other_source, active_package.get("records", [])
                ):
                    matched = candidate_signatures.get(
                        _normalized_record_signature(row)
                    )
                    if matched is not None:
                        active_collisions.append(
                            {
                                "source": other_source,
                                "provider": row["provider"],
                                "event_at": row["event_at"],
                                "total_tokens": row["usage"]["total_tokens"],
                            }
                        )
            if active_collisions:
                blockers.append(
                    {
                        "code": "active_user_source_collision",
                        "records": len(active_collisions),
                        "examples": active_collisions[:20],
                    }
                )

        normalized = {
            "source": source,
            "revision": revision,
            "receipts": receipts,
            "accounts": accounts,
            "quotas": quotas,
            "records": records,
            "expected": {field: int(expected[field]) for field in sorted(expected_fields)},
        }
        try:
            package_json = _canonical(normalized)
        except (TypeError, ValueError) as exc:
            raise ValueError("user source import package must be JSON serializable") from exc
        if len(package_json.encode("utf-8")) > MAX_USER_SOURCE_IMPORT_BODY:
            raise ValueError("user source import package is too large")
        package_sha256 = hashlib.sha256(package_json.encode("utf-8")).hexdigest()
        all_status = self.import_status()
        current = next(
            (
                item
                for item in all_status.get("sources", [])
                if item["source"] == source
            ),
            None,
        )
        active_set = []
        for item in all_status.get("sources", []):
            active_revision = item.get("active_revision")
            active_row = next(
                (
                    revision_row
                    for revision_row in item.get("revisions", [])
                    if revision_row.get("active")
                ),
                None,
            )
            if active_revision and active_row:
                active_set.append(
                    [item["source"], active_revision, active_row["package_sha256"]]
                )
        active_set_sha256 = hashlib.sha256(
            _canonical(sorted(active_set)).encode("utf-8")
        ).hexdigest()
        active_revision = current.get("active_revision") if current else None
        existing = next(
            (
                item
                for item in (current or {}).get("revisions", [])
                if item["revision"] == revision
            ),
            None,
        )
        if existing and existing["package_sha256"] != package_sha256:
            blockers.append({"code": "immutable_revision_conflict", "revision": revision})
        summary = {
            **actual,
            "quota_accounts": len(_latest_quotas(quotas)),
            "providers": sorted({row["provider"] for row in accounts + records}),
            "first_event_at": record_check["first_event_at"],
            "last_event_at": record_check["last_event_at"],
        }
        prepared = {
            "source": source,
            "revision": revision,
            "package_sha256": package_sha256,
            "package_json": package_json,
            "summary": summary,
            "record_signatures": sorted(
                _normalized_record_signature(row) for row in record_rows
            ),
            "observation_signatures": [],
        }
        result = {
            "contract_version": 2,
            "source": source,
            "revision": revision,
            "package_sha256": package_sha256,
            "active_revision": active_revision,
            "active_set_sha256": active_set_sha256,
            "action": (
                "idempotent"
                if existing and existing["package_sha256"] == package_sha256
                else "create"
                if active_revision is None
                else "replace"
            ),
            "ready_to_commit": not blockers,
            "summary": summary,
            "record_validation": record_check,
            "active_user_source_collisions": active_collisions[:20],
            "blockers": blockers,
            "persisted": False,
        }
        return prepared, result

    def preflight_import(self, package: object) -> dict[str, Any]:
        """Validate the exact whole-package bytes without creating product state."""

        return self._prepare_import(package)[1]

    def commit_import(self, envelope: object) -> dict[str, Any]:
        """Atomically commit one immutable revision and switch its active pointer."""

        if not isinstance(envelope, dict):
            raise ValueError("user source import commit must be an object")
        if set(envelope) - {"package", "replace", "expected_active_revision"}:
            raise ValueError("unsupported user source import commit field")
        replace = envelope.get("replace", False)
        if not isinstance(replace, bool):
            raise ValueError("replace must be a boolean")
        expected_active = envelope.get("expected_active_revision")
        if expected_active is not None:
            expected_active = str(expected_active).strip()
            if not _REVISION_RE.fullmatch(expected_active):
                raise ValueError("expected_active_revision is invalid")
        prepared, preflight = self._prepare_import(envelope.get("package"))
        if not preflight["ready_to_commit"]:
            codes = ", ".join(str(item["code"]) for item in preflight["blockers"])
            raise ValueError(f"user source import preflight failed: {codes}")
        current = preflight["active_revision"]
        # "Already committed" and "already in force" are different facts. The
        # revision row is immutable and its hash already matched, so it is never
        # written twice -- but when it is not the active one this call still has
        # to move the pointer, which is the half of the documented contract that
        # used to be skipped while the caller was told it had been persisted.
        retained = preflight["action"] == "idempotent"
        if retained and current == prepared["revision"]:
            if expected_active is not None and expected_active != current:
                raise ValueError("expected_active_revision does not match the active import")
            # "Already in force" is a claim about now. The preflight that
            # supports it was an unlocked read, and every other path here
            # re-establishes the same claim inside the transaction before
            # acting on it. This one returned it on trust, so a caller whose
            # revision had just been replaced by another writer was told its
            # package was active while a different one was.
            self._confirm_active(prepared)
            return {**preflight, "persisted": True, "idempotent": True}
        if current is not None:
            if not replace:
                raise ValueError("replace=true is required to change an active import")
            if expected_active != current:
                raise ValueError("expected_active_revision does not match the active import")
        elif expected_active is not None:
            raise ValueError("expected_active_revision must be null for a new import")

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            active_row = conn.execute(
                "SELECT revision FROM user_source_import_active WHERE source = ?",
                (prepared["source"],),
            ).fetchone()
            live_active = str(active_row[0]) if active_row else None
            if live_active != current:
                raise ValueError("active import changed after preflight; run preflight again")
            live_active_set = [
                [str(row[0]), str(row[1]), str(row[2])]
                for row in conn.execute(
                    """
                    SELECT active.source, active.revision, revisions.package_sha256
                    FROM user_source_import_active AS active
                    JOIN user_source_import_revisions AS revisions
                      ON revisions.source = active.source
                     AND revisions.revision = active.revision
                    ORDER BY active.source
                    """
                )
            ]
            live_active_set_sha256 = hashlib.sha256(
                _canonical(live_active_set).encode("utf-8")
            ).hexdigest()
            if live_active_set_sha256 != preflight["active_set_sha256"]:
                raise ValueError("active import set changed after preflight; run preflight again")
            candidate_signatures = set(prepared["record_signatures"])
            if candidate_signatures:
                from sandglass.telemetry import user_source_record_dicts

                for other_source, package_json in conn.execute(
                    """
                    SELECT active.source, revisions.package_json
                    FROM user_source_import_active AS active
                    JOIN user_source_import_revisions AS revisions
                      ON revisions.source = active.source
                     AND revisions.revision = active.revision
                    WHERE active.source <> ?
                    """,
                    (prepared["source"],),
                ):
                    other_package = json.loads(str(package_json))
                    for row in user_source_record_dicts(
                        str(other_source), other_package.get("records", [])
                    ):
                        if _normalized_record_signature(row) in candidate_signatures:
                            raise ValueError(
                                "active user-source collision appeared after preflight"
                            )
            observation_signatures = set(prepared.get("observation_signatures") or [])
            if observation_signatures:
                from sandglass.metering import observation_signature

                for other_source, package_json in conn.execute(
                    """
                    SELECT active.source, revisions.package_json
                    FROM user_source_import_active AS active
                    JOIN user_source_import_revisions AS revisions
                      ON revisions.source = active.source
                     AND revisions.revision = active.revision
                    WHERE active.source <> ?
                    """,
                    (prepared["source"],),
                ):
                    other_package = json.loads(str(package_json))
                    if other_package.get("contract_version") != 3:
                        continue
                    for row in other_package.get("observations") or []:
                        if observation_signature(row) in observation_signatures:
                            raise ValueError(
                                "active metering collision appeared after preflight"
                            )
            if retained:
                # Nothing to write: the row is immutable and its hash matched.
                # Mirror rollback_import and refuse to point the active row at a
                # revision that is no longer retained -- there is no foreign key
                # to catch that for us.
                kept = conn.execute(
                    """SELECT 1 FROM user_source_import_revisions
                       WHERE source = ? AND revision = ?""",
                    (prepared["source"], prepared["revision"]),
                ).fetchone()
                if not kept:
                    raise ValueError("revision is no longer a retained import")
            else:
                conn.execute(
                    """
                    INSERT INTO user_source_import_revisions(
                        source, revision, package_sha256, package_json,
                        summary_json, committed_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prepared["source"],
                        prepared["revision"],
                        prepared["package_sha256"],
                        prepared["package_json"],
                        _canonical(prepared["summary"]),
                        now,
                    ),
                )
            conn.execute(
                """
                INSERT INTO user_source_import_active(source, revision, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    revision=excluded.revision,
                    updated_at=excluded.updated_at
                """,
                (prepared["source"], prepared["revision"], now),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {
            **preflight,
            "active_revision": prepared["revision"],
            "persisted": True,
            "idempotent": False,
        }

    def _confirm_active(self, prepared: dict[str, Any]) -> None:
        """Raise unless this revision is still retained and still the active one."""

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT revision FROM user_source_import_active WHERE source = ?",
                (prepared["source"],),
            ).fetchone()
            if (str(row[0]) if row else None) != prepared["revision"]:
                raise ValueError(
                    "active import changed after preflight; run preflight again"
                )
            kept = conn.execute(
                """SELECT 1 FROM user_source_import_revisions
                   WHERE source = ? AND revision = ?""",
                (prepared["source"], prepared["revision"]),
            ).fetchone()
            if not kept:
                raise ValueError("revision is no longer a retained import")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def rollback_import(self, envelope: object) -> dict[str, Any]:
        """Atomically point a source back to a retained revision, or deactivate it."""

        if not isinstance(envelope, dict):
            raise ValueError("user source rollback must be an object")
        if set(envelope) != {"source", "target_revision", "expected_active_revision"}:
            raise ValueError(
                "rollback requires source, target_revision and expected_active_revision"
            )
        source = str(envelope.get("source") or "").strip()
        target = envelope.get("target_revision")
        expected = str(envelope.get("expected_active_revision") or "").strip()
        if not _SOURCE_RE.fullmatch(source) or not _REVISION_RE.fullmatch(expected):
            raise ValueError("rollback source or expected_active_revision is invalid")
        if target is not None:
            target = str(target).strip()
            if not _REVISION_RE.fullmatch(target):
                raise ValueError("target_revision is invalid")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT revision FROM user_source_import_active WHERE source = ?", (source,)
            ).fetchone()
            current = str(active[0]) if active else None
            if current != expected:
                raise ValueError("expected_active_revision does not match the active import")
            if target is not None:
                exists = conn.execute(
                    """SELECT 1 FROM user_source_import_revisions
                       WHERE source = ? AND revision = ?""",
                    (source, target),
                ).fetchone()
                if not exists:
                    raise ValueError("target_revision is not a retained import")
                now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                conn.execute(
                    "UPDATE user_source_import_active SET revision = ?, updated_at = ? "
                    "WHERE source = ?",
                    (target, now, source),
                )
            else:
                conn.execute("DELETE FROM user_source_import_active WHERE source = ?", (source,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {
            "contract_version": 2,
            "source": source,
            "previous_revision": current,
            "active_revision": target,
            "rolled_back": True,
        }

    def active_import_packages(self) -> list[dict[str, Any]]:
        """Read active immutable packages without creating or mutating them.

        `mode=ro` cannot write a row or add a table. It does let SQLite create
        the -shm and -wal sidecars of a WAL database, which outlive the
        connection; see the same note on TelemetryStore.existing_event_keys.
        """

        if not self.path.exists():
            return []
        uri = self.path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            tables = {
                str(row["name"])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not {
                "user_source_import_revisions",
                "user_source_import_active",
            }.issubset(tables):
                return []
            return [
                json.loads(str(row["package_json"]))
                for row in conn.execute(
                    """
                    SELECT revisions.package_json
                    FROM user_source_import_active AS active
                    JOIN user_source_import_revisions AS revisions
                      ON revisions.source = active.source
                     AND revisions.revision = active.revision
                    ORDER BY active.source
                    """
                )
            ]
        finally:
            conn.close()

    def metering(self) -> dict[str, Any]:
        """Return active v3 model/API observations without merging product totals."""

        from sandglass.metering import metering_view

        return metering_view(self.active_import_packages())

    def append_accounts(self, envelope: object) -> dict[str, Any]:
        """Store a strict, secret-free account manifest from one user source."""

        if not isinstance(envelope, dict):
            raise ValueError("user source account envelope must be an object")
        if set(envelope) - {"source", "accounts"}:
            raise ValueError("unsupported user source account envelope field")
        source = str(envelope.get("source") or "").strip()
        if not _SOURCE_RE.fullmatch(source):
            raise ValueError("user source accounts require a stable source id")
        values = envelope.get("accounts")
        if not isinstance(values, list) or not values:
            raise ValueError("user source accounts require a non-empty accounts array")
        if len(values) > MAX_USER_SOURCE_ACCOUNTS:
            raise ValueError("too many user source accounts in one request")

        observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        rows: list[tuple[str, ...]] = []
        seen: set[tuple[str, str]] = set()
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                raise ValueError(f"user source account {index} must be an object")
            if set(value) - _ACCOUNT_FIELDS:
                raise ValueError(f"user source account {index} has unsupported fields")
            provider = str(value.get("provider") or "").strip().lower()
            account_id = str(value.get("account_id") or "").strip()
            if not _PROVIDER_RE.fullmatch(provider) or not account_id:
                raise ValueError(
                    f"user source account {index} requires provider and account_id"
                )
            fields = {
                key: str(value.get(key) or "").strip()
                for key in ("email", "label", "plan", "source_version")
            }
            if len(account_id) > 256 or any(len(text) > 256 for text in fields.values()):
                raise ValueError(f"user source account {index} field is too long")
            key = (provider, account_id)
            if key in seen:
                raise ValueError(f"user source account {index} is duplicated in this request")
            seen.add(key)
            rows.append(
                (
                    source,
                    provider,
                    account_id,
                    fields["email"],
                    fields["label"],
                    fields["plan"],
                    fields["source_version"],
                    observed_at,
                    observed_at,
                )
            )

        conn = self._connect()
        try:
            before = conn.total_changes
            conn.executemany(
                """
                INSERT INTO user_source_accounts(
                    source, provider, account_id, email, label, plan,
                    source_version, first_observed_at, last_observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, provider, account_id) DO UPDATE SET
                    email=excluded.email,
                    label=excluded.label,
                    plan=excluded.plan,
                    source_version=excluded.source_version,
                    last_observed_at=excluded.last_observed_at
                WHERE email <> excluded.email
                   OR label <> excluded.label
                   OR plan <> excluded.plan
                   OR source_version <> excluded.source_version
                """,
                rows,
            )
            conn.commit()
            changed = conn.total_changes - before
        finally:
            conn.close()
        return {
            "accepted": len(rows),
            "changed": changed,
            "unchanged": len(rows) - changed,
            "source": source,
        }

    def accounts(self) -> list[dict[str, Any]]:
        """Read adapter-discovered accounts without creating product state."""

        legacy: list[dict[str, Any]] = []
        if self.path.exists():
            uri = self.path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=10.0)
            conn.row_factory = sqlite3.Row
            try:
                tables = {
                    str(row["name"])
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if "user_source_accounts" in tables:
                    legacy = [dict(row) for row in conn.execute("SELECT * FROM user_source_accounts")]
            finally:
                conn.close()
        package_rows: list[dict[str, Any]] = []
        package_sources: set[str] = set()
        for package in self.active_import_packages():
            source = str(package["source"])
            package_sources.add(source)
            for row in package.get("accounts", []):
                package_rows.append(
                    {
                        "source": source,
                        **row,
                        "first_observed_at": "",
                        "last_observed_at": "",
                    }
                )
        combined = [row for row in legacy if str(row.get("source") or "") not in package_sources]
        combined.extend(package_rows)
        return sorted(
            combined,
            key=lambda row: (
                str(row.get("provider") or ""),
                str(row.get("label") or ""),
                str(row.get("account_id") or ""),
                str(row.get("source") or ""),
            ),
        )

    def append_quotas(self, envelope: object) -> dict[str, Any]:
        """Store account-scoped quota results, never the credential used to fetch them."""

        if not isinstance(envelope, dict):
            raise ValueError("user source quota envelope must be an object")
        if set(envelope) - {"source", "quotas"}:
            raise ValueError("unsupported user source quota envelope field")
        source = str(envelope.get("source") or "").strip()
        if not _SOURCE_RE.fullmatch(source):
            raise ValueError("user source quotas require a stable source id")
        values = envelope.get("quotas")
        if not isinstance(values, list) or not values:
            raise ValueError("user source quotas require a non-empty quotas array")
        if len(values) > MAX_USER_SOURCE_ACCOUNTS:
            raise ValueError("too many user source quotas in one request")
        admitted = {
            (str(row.get("provider") or ""), str(row.get("account_id") or ""))
            for row in self.accounts()
            if str(row.get("source") or "") == source
        }
        updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        rows: list[tuple[str, ...]] = []
        seen: set[tuple[str, str]] = set()
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                raise ValueError(f"user source quota {index} must be an object")
            if set(value) - _QUOTA_FIELDS:
                raise ValueError(f"user source quota {index} has unsupported fields")
            provider = str(value.get("provider") or "").strip().lower()
            account_id = str(value.get("account_id") or "").strip()
            if (provider, account_id) not in admitted:
                raise ValueError(
                    f"user source quota {index} requires an admitted account from this source"
                )
            fetched_at = str(value.get("fetched_at") or "").strip()
            if not _timezone_qualified(fetched_at) or parse_ts(fetched_at) is None:
                raise ValueError(f"user source quota {index} requires fetched_at with timezone")
            plan = str(value.get("plan") or "").strip()
            source_version = str(value.get("source_version") or "").strip()
            if len(plan) > 256 or len(source_version) > 256:
                raise ValueError(f"user source quota {index} field is too long")
            windows = value.get("windows")
            if not isinstance(windows, list):
                raise ValueError(f"user source quota {index} windows must be an array")
            normalized_windows = [
                _quota_window(window, index, window_index)
                for window_index, window in enumerate(windows)
            ]
            key = (provider, account_id)
            if key in seen:
                raise ValueError(f"user source quota {index} is duplicated in this request")
            seen.add(key)
            rows.append(
                (
                    source,
                    provider,
                    account_id,
                    fetched_at,
                    plan,
                    source_version,
                    _canonical(normalized_windows),
                    updated_at,
                )
            )
        conn = self._connect()
        try:
            before = conn.total_changes
            conn.executemany(
                """
                INSERT INTO user_source_quotas(
                    source, provider, account_id, fetched_at, plan,
                    source_version, windows_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, provider, account_id) DO UPDATE SET
                    fetched_at=excluded.fetched_at,
                    plan=excluded.plan,
                    source_version=excluded.source_version,
                    windows_json=excluded.windows_json,
                    updated_at=excluded.updated_at
                WHERE fetched_at <> excluded.fetched_at
                   OR plan <> excluded.plan
                   OR source_version <> excluded.source_version
                   OR windows_json <> excluded.windows_json
                """,
                rows,
            )
            conn.commit()
            changed = conn.total_changes - before
        finally:
            conn.close()
        return {
            "accepted": len(rows),
            "changed": changed,
            "unchanged": len(rows) - changed,
            "source": source,
        }

    def quotas(self) -> list[dict[str, Any]]:
        legacy: list[dict[str, Any]] = []
        if self.path.exists():
            uri = self.path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=10.0)
            conn.row_factory = sqlite3.Row
            try:
                tables = {
                    str(row["name"])
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if "user_source_quotas" in tables:
                    for row in conn.execute("SELECT * FROM user_source_quotas"):
                        item = dict(row)
                        item["windows"] = json.loads(item.pop("windows_json"))
                        legacy.append(item)
            finally:
                conn.close()
        package_rows: list[dict[str, Any]] = []
        package_sources: set[str] = set()
        for package in self.active_import_packages():
            source = str(package["source"])
            package_sources.add(source)
            for row in _latest_quotas(list(package.get("quotas", []))):
                package_rows.append({"source": source, **row, "updated_at": ""})
        combined = [row for row in legacy if str(row.get("source") or "") not in package_sources]
        combined.extend(package_rows)
        return sorted(
            combined,
            key=lambda row: (
                str(row.get("provider") or ""),
                str(row.get("account_id") or ""),
                str(row.get("source") or ""),
            ),
        )

    def append(self, envelope: object) -> dict[str, Any]:
        if not isinstance(envelope, dict):
            raise ValueError("user source envelope must be an object")
        source = str(envelope.get("source") or "").strip()
        if not _SOURCE_RE.fullmatch(source):
            raise ValueError("user source requires a stable source id")
        if "payload" not in envelope:
            raise ValueError("user source requires a payload")
        payload = envelope["payload"]
        try:
            payload_json = _canonical(payload)
        except (TypeError, ValueError) as exc:
            raise ValueError("user source payload must be JSON serializable") from exc
        if len(payload_json.encode("utf-8")) > MAX_USER_SOURCE_BODY:
            raise ValueError("user source payload is too large")

        fields = _field_names(payload)
        recognized = sorted(fields & _KNOWN_FIELDS)
        unknown = sorted(fields - _KNOWN_FIELDS)
        key_material = _canonical({"source": source, "payload": payload})
        receipt_key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()
        received_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self._connect()
        try:
            before = conn.total_changes
            conn.execute(
                """
                INSERT OR IGNORE INTO user_source_receipts(
                    receipt_key, source, received_at, payload_json,
                    recognized_fields_json, unknown_fields_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_key,
                    source,
                    received_at,
                    payload_json,
                    _canonical(recognized),
                    _canonical(unknown),
                ),
            )
            conn.commit()
            inserted = conn.total_changes > before
        finally:
            conn.close()
        return {
            "accepted": True,
            "inserted": inserted,
            "duplicate": not inserted,
            "receipt_key": receipt_key,
            "source": source,
            "recognized_fields": recognized,
            "unknown_fields": unknown,
            "product_use_stage": "stored_only",
        }

    def receipts(self) -> list[dict[str, Any]]:
        legacy: list[dict[str, Any]] = []
        if self.path.exists():
            uri = self.path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=10.0)
            conn.row_factory = sqlite3.Row
            try:
                tables = {
                    str(row["name"])
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if "user_source_receipts" in tables:
                    for row in conn.execute("SELECT * FROM user_source_receipts"):
                        item = dict(row)
                        item["payload"] = json.loads(item.pop("payload_json"))
                        item["recognized_fields"] = json.loads(
                            item.pop("recognized_fields_json")
                        )
                        item["unknown_fields"] = json.loads(
                            item.pop("unknown_fields_json")
                        )
                        legacy.append(item)
            finally:
                conn.close()
        package_rows: list[dict[str, Any]] = []
        package_sources: set[str] = set()
        status = {
            item["source"]: item
            for item in self.import_status().get("sources", [])
        }
        for package in self.active_import_packages():
            source = str(package["source"])
            package_sources.add(source)
            source_status = status.get(source) or {}
            revision_status = next(
                (
                    item
                    for item in source_status.get("revisions", [])
                    if item.get("active")
                ),
                {},
            )
            received_at = str(revision_status.get("committed_at") or "")
            for index, payload in enumerate(package.get("receipts", [])):
                fields = _field_names(payload)
                package_rows.append(
                    {
                        "receipt_key": hashlib.sha256(
                            _canonical(
                                {
                                    "source": source,
                                    "revision": package["revision"],
                                    "index": index,
                                    "payload": payload,
                                }
                            ).encode("utf-8")
                        ).hexdigest(),
                        "source": source,
                        "received_at": received_at,
                        "payload": payload,
                        "recognized_fields": sorted(fields & _KNOWN_FIELDS),
                        "unknown_fields": sorted(fields - _KNOWN_FIELDS),
                    }
                )
        combined = [row for row in legacy if str(row.get("source") or "") not in package_sources]
        combined.extend(package_rows)
        return sorted(
            combined,
            key=lambda row: (
                str(row.get("received_at") or ""),
                str(row.get("receipt_key") or ""),
            ),
        )

    def settings(self) -> dict[str, dict[str, Any]]:
        """Read reversible per-source choices without creating the database."""
        if not self.path.exists():
            return {}
        uri = self.path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            tables = {
                str(row["name"])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "user_source_settings" not in tables:
                return {}
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(user_source_settings)")
            }
            return {
                str(row["source"]): {
                    "display_enabled": bool(row["display_enabled"]),
                    "accounts_enabled": bool(row["accounts_enabled"])
                    if "accounts_enabled" in columns
                    else True,
                    "identity_enabled": bool(row["identity_enabled"])
                    if "identity_enabled" in columns
                    else False,
                    "mapped_provider": str(row["mapped_provider"] or ""),
                    "mapped_account_id": str(row["mapped_account_id"] or ""),
                    "totals_enabled": bool(row["totals_enabled"])
                    if "totals_enabled" in columns
                    else False,
                    "full_window_enabled": bool(row["full_window_enabled"])
                    if "full_window_enabled" in columns
                    else False,
                    "updated_at": str(row["updated_at"] or ""),
                }
                for row in conn.execute("SELECT * FROM user_source_settings")
            }
        finally:
            conn.close()

    def configure(
        self,
        source: str,
        *,
        display_enabled: bool | None = None,
        accounts_enabled: bool | None = None,
        identity_enabled: bool | None = None,
        mapped_provider: str | None = None,
        mapped_account_id: str | None = None,
        totals_enabled: bool | None = None,
        full_window_enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Persist only explicit reversible UI choices under SANDGLASS_HOME."""
        source = str(source or "").strip()
        if not _SOURCE_RE.fullmatch(source):
            raise ValueError("user source requires a stable source id")
        if display_enabled is not None and not isinstance(display_enabled, bool):
            raise ValueError("display_enabled must be a boolean")
        if accounts_enabled is not None and not isinstance(accounts_enabled, bool):
            raise ValueError("accounts_enabled must be a boolean")
        if identity_enabled is not None and not isinstance(identity_enabled, bool):
            raise ValueError("identity_enabled must be a boolean")
        if totals_enabled is not None and not isinstance(totals_enabled, bool):
            raise ValueError("totals_enabled must be a boolean")
        if full_window_enabled is not None and not isinstance(full_window_enabled, bool):
            raise ValueError("full_window_enabled must be a boolean")
        if (mapped_provider is None) != (mapped_account_id is None):
            raise ValueError("mapped provider and account must be changed together")
        provider = None if mapped_provider is None else str(mapped_provider).strip()
        account_id = None if mapped_account_id is None else str(mapped_account_id).strip()
        if provider is not None:
            if bool(provider) != bool(account_id):
                raise ValueError("mapped provider and account must both be set or cleared")
            if len(provider) > 64 or len(account_id) > 256:
                raise ValueError("mapped account is too long")

        current = self.settings().get(
            source,
            {
                "display_enabled": False,
                "accounts_enabled": True,
                "identity_enabled": False,
                "mapped_provider": "",
                "mapped_account_id": "",
                "totals_enabled": False,
                "full_window_enabled": False,
            },
        )
        selected_display = (
            current["display_enabled"] if display_enabled is None else display_enabled
        )
        selected_accounts = (
            current.get("accounts_enabled", True)
            if accounts_enabled is None
            else accounts_enabled
        )
        selected_identity = (
            current.get("identity_enabled", False)
            if identity_enabled is None
            else identity_enabled
        )
        selected_provider = current["mapped_provider"] if provider is None else provider
        selected_account = current["mapped_account_id"] if account_id is None else account_id
        selected_totals = (
            current.get("totals_enabled", False)
            if totals_enabled is None
            else totals_enabled
        )
        selected_full_window = (
            current.get("full_window_enabled", False)
            if full_window_enabled is None
            else full_window_enabled
        )
        updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO user_source_settings(
                    source, display_enabled, accounts_enabled, identity_enabled,
                    mapped_provider, mapped_account_id,
                    totals_enabled, full_window_enabled, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    display_enabled=excluded.display_enabled,
                    accounts_enabled=excluded.accounts_enabled,
                    identity_enabled=excluded.identity_enabled,
                    mapped_provider=excluded.mapped_provider,
                    mapped_account_id=excluded.mapped_account_id,
                    totals_enabled=excluded.totals_enabled,
                    full_window_enabled=excluded.full_window_enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    source,
                    int(selected_display),
                    int(selected_accounts),
                    int(selected_identity),
                    selected_provider,
                    selected_account,
                    int(selected_totals),
                    int(selected_full_window),
                    updated_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return {
            "source": source,
            "display_enabled": selected_display,
            "accounts_enabled": selected_accounts,
            "identity_enabled": selected_identity,
            "mapped_provider": selected_provider,
            "mapped_account_id": selected_account,
            "totals_enabled": selected_totals,
            "full_window_enabled": selected_full_window,
            "updated_at": updated_at,
        }

    def reset(self, source: str) -> dict[str, int]:
        """Remove one disabled import so the same stable source can be rebuilt."""

        source = str(source or "").strip()
        if not _SOURCE_RE.fullmatch(source):
            raise ValueError("user source requires a stable source id")
        if self.import_status(source).get("source") is not None:
            raise ValueError(
                "revisioned imports are retained; deactivate or roll back the active revision"
            )
        current = self.settings().get(source, {})
        if any(
            bool(current.get(field))
            for field in (
                "display_enabled",
                "accounts_enabled",
                "identity_enabled",
                "mapped_provider",
                "mapped_account_id",
                "totals_enabled",
                "full_window_enabled",
            )
        ):
            raise ValueError("disable every source control before resetting an import")
        conn = self._connect()
        try:
            counts: dict[str, int] = {}
            for table, label in (
                ("user_source_receipts", "receipts"),
                ("user_source_accounts", "accounts"),
                ("user_source_quotas", "quotas"),
                ("user_source_settings", "settings"),
            ):
                before = conn.total_changes
                conn.execute(f"DELETE FROM {table} WHERE source = ?", (source,))
                counts[label] = conn.total_changes - before
            conn.commit()
            return counts
        finally:
            conn.close()

    def mirror(self) -> dict[str, Any]:
        rows = self.receipts()
        settings = self.settings()
        sources: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = sources.setdefault(
                row["source"],
                {
                    "source": row["source"],
                    "receipts": 0,
                    "recognized_fields": set(),
                    "unknown_fields": set(),
                    "first_received_at": row["received_at"],
                    "last_received_at": row["received_at"],
                },
            )
            item["receipts"] += 1
            item["recognized_fields"].update(row["recognized_fields"])
            item["unknown_fields"].update(row["unknown_fields"])
            item["last_received_at"] = max(item["last_received_at"], row["received_at"])
        summary = []
        for item in sources.values():
            item = dict(item)
            item["recognized_fields"] = sorted(item["recognized_fields"])
            item["unknown_fields"] = sorted(item["unknown_fields"])
            item["state"] = "received_unmapped"
            item["settings"] = settings.get(
                item["source"],
                {
                    "display_enabled": False,
                    "accounts_enabled": True,
                    "identity_enabled": False,
                    "mapped_provider": "",
                    "mapped_account_id": "",
                    "totals_enabled": False,
                    "full_window_enabled": False,
                    "updated_at": "",
                },
            )
            item["product_use_stage"] = (
                "full_window_enabled"
                if item["settings"].get("full_window_enabled")
                else "totals_included"
                if item["settings"].get("totals_enabled")
                else "account_mapped"
                if (
                    item["settings"].get("identity_enabled")
                    or item["settings"]["mapped_account_id"]
                )
                else "displayed"
                if item["settings"]["display_enabled"]
                else "stored_only"
            )
            summary.append(item)
        return {
            # This is a product-stage description, not evidence that report
            # isolation holds.  The audit measures that property by building
            # reports before and after a synthetic receipt is added.
            "control_model": "per_source",
            "sources": sorted(summary, key=lambda item: item["source"]),
            "recent_receipts": rows[-20:],
        }
