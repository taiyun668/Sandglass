import gzip
import http.client
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest

from sandglass.serve import Handler, OtlpHandler, _bounded_gzip_decompress, _GzipBodyTooLarge
from sandglass.attribution import owns_minute, supplemental_sources_for_account
from sandglass.models import Account, SessionRecord, TokenUsage
from sandglass.report import build_report
from sandglass.telemetry import (
    MAX_OTLP_BODY,
    TelemetryRecord,
    TelemetryStore,
    apply_user_evidence,
    apply_user_identity_evidence,
    apply_user_token_evidence,
    candidate_user_source_ledger,
    ingest_otlp_logs,
    ingest_user_source_records,
    official_minute_rows,
    reconcile_telemetry_minutes,
    telemetry_status,
)


def _attr(target, key, value) -> None:
    item = target.attributes.add()
    item.key = key
    if isinstance(value, bool):
        item.value.bool_value = value
    elif isinstance(value, int):
        item.value.int_value = value
    elif isinstance(value, float):
        item.value.double_value = value
    else:
        item.value.string_value = str(value)


def _payload(service: str, event_name: str, attrs: dict, *, body: str = "", version: str = "test") -> bytes:
    request = ExportLogsServiceRequest()
    resource_logs = request.resource_logs.add()
    _attr(resource_logs.resource, "service.name", service)
    _attr(resource_logs.resource, "service.version", version)
    scope_logs = resource_logs.scope_logs.add()
    log = scope_logs.log_records.add()
    log.time_unix_nano = 1_787_932_800_123_000_000
    log.event_name = event_name
    if body:
        log.body.string_value = body
    for key, value in attrs.items():
        _attr(log, key, value)
    return request.SerializeToString()


class TelemetryTests(unittest.TestCase):
    def test_otlp_ingest_can_limit_records_to_enabled_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            payload = _payload(
                "grok-cli",
                "grok_code.api_request",
                {"event.sequence": 1, "user.id": "grok-account", "input_tokens": 8, "output_tokens": 2},
            )
            blocked = ingest_otlp_logs(payload, store, allowed_providers={"claude"})
            self.assertEqual(blocked["accepted"], 0)
            self.assertEqual(store.records(), [])

            accepted = ingest_otlp_logs(payload, store, allowed_providers={"grok"})
            self.assertEqual(accepted["accepted"], 1)
            self.assertEqual(len(store.records()), 1)

    def test_grok_usage_event_does_not_need_the_cli_service_name(self):
        """The event name is already Grok's. Requiring service.name == grok-cli
        would drop a completion the hint already counted as Grok traffic."""
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            result = ingest_otlp_logs(
                _payload(
                    "grok",
                    "grok_code.api_request",
                    {
                        "event.sequence": 1,
                        "user.id": "grok-account",
                        "input_tokens": 8,
                        "output_tokens": 2,
                    },
                ),
                store,
            )
            self.assertEqual(result["accepted"], 1)
            self.assertEqual(store.records()[0]["provider"], "grok")

    def test_normalized_json_enables_multi_account_identity_and_reverts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            records = [
                {
                    "provider": "codex",
                    "event_id": f"switch-{index}",
                    "timestamp": f"2026-08-30T00:0{index}:00Z",
                    "account_id": account_id,
                    "session_id": f"session-{index}",
                    "input_tokens": 7,
                    "output_tokens": 3,
                    "reasoning_tokens": 1,
                    "total_tokens": 10,
                    "calls": 1,
                }
                for index, account_id in ((1, "account-1"), (2, "account-2"))
            ]

            accepted = ingest_user_source_records(
                {"source": "multi.identity", "records": records}, store
            )
            duplicate = ingest_user_source_records(
                {"source": "multi.identity", "records": records}, store
            )
            official = [
                SessionRecord(
                    provider="codex",
                    session_id=f"session-{index}",
                    path=f"official-{index}.jsonl",
                    usage=TokenUsage(input_tokens=7, output_tokens=3, reasoning_tokens=1, calls=1),
                    timeline=[
                        (
                            f"2026-08-30T00:0{index}:00Z",
                            TokenUsage(
                                input_tokens=7,
                                output_tokens=3,
                                reasoning_tokens=1,
                                calls=1,
                            ),
                        )
                    ],
                )
                for index in (1, 2)
            ]
            setting = {"multi.identity": {"identity_enabled": True}}

            ledger = candidate_user_source_ledger(
                official, settings=setting, store=store
            )
            enabled = apply_user_identity_evidence(
                official, settings=setting, store=store
            )
            reverted = apply_user_identity_evidence(
                official, settings={}, store=store
            )

            self.assertEqual(accepted["inserted"], 2)
            self.assertEqual(duplicate["duplicates"], 2)
            self.assertEqual(ledger["identity_candidates"], 2)
            self.assertEqual(ledger["identity_supplement_minutes"], 2)
            self.assertEqual(
                [
                    next(iter(row.extra["minute_identity_evidence"].values()))[
                        "account_id"
                    ]
                    for row in enabled
                ],
                ["account-1", "account-2"],
            )
            self.assertTrue(
                all("minute_identity_evidence" not in row.extra for row in reverted)
            )
            self.assertEqual(
                sum(row.usage.total_tokens for row in enabled),
                sum(row.usage.total_tokens for row in official),
            )

    def test_normalized_json_rejects_inconsistent_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            with self.assertRaisesRegex(ValueError, "must equal"):
                ingest_user_source_records(
                    {
                        "source": "bad.total",
                        "records": [
                            {
                                "provider": "codex",
                                "event_id": "bad-1",
                                "timestamp": "2026-08-30T00:00:00Z",
                                "session_id": "session-1",
                                "input_tokens": 7,
                                "output_tokens": 3,
                                "total_tokens": 11,
                            }
                        ],
                    },
                    store,
                )
            with self.assertRaisesRegex(ValueError, "timezone-qualified"):
                ingest_user_source_records(
                    {
                        "source": "bad.time",
                        "records": [
                            {
                                "provider": "codex",
                                "event_id": "bad-2",
                                "timestamp": "2026-08-30T00:00:00",
                                "session_id": "session-1",
                                "input_tokens": 7,
                                "output_tokens": 3,
                                "total_tokens": 10,
                            }
                        ],
                    },
                    store,
                )

    def test_official_minute_rows_are_prompt_free_and_exact(self):
        usage = TokenUsage(input_tokens=7, output_tokens=3, reasoning_tokens=1, calls=1)
        rows = official_minute_rows(
            [
                SessionRecord(
                    provider="codex",
                    session_id="session-1",
                    path="secret-provider-path.jsonl",
                    title="private prompt title",
                    usage=usage,
                    timeline=[("2026-08-30T00:00:30Z", usage)],
                )
            ],
            provider="codex",
        )

        self.assertEqual(rows[0]["timestamp"], "2026-08-30T00:00:00Z")
        self.assertEqual(rows[0]["total_tokens"], 10)
        self.assertNotIn("path", rows[0])
        self.assertNotIn("title", rows[0])

        with self.assertRaisesRegex(ValueError, "built-in provider"):
            official_minute_rows([], provider="custom-provider")
        with self.assertRaisesRegex(ValueError, "valid timestamp"):
            official_minute_rows([], provider="codex", since="not-a-time")
        with self.assertRaisesRegex(ValueError, "earlier"):
            official_minute_rows(
                [],
                provider="codex",
                since="2026-08-30T00:01:00Z",
                until="2026-08-30T00:00:00Z",
            )

    def test_enabled_token_evidence_requires_separate_full_window_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            usage = TokenUsage(input_tokens=7, output_tokens=3, calls=1)
            store.append(
                [
                    TelemetryRecord(
                        event_key="user-token-1",
                        provider="openrouter",
                        event_name="sandglass.usage",
                        event_at="2026-08-30T00:00:00Z",
                        received_at="2026-08-30T00:00:01Z",
                        account_id="account-1",
                        session_id="source-session",
                        model="",
                        source="user_adapter:example.tokens",
                        source_version="1",
                        schema_version="1",
                        evidence_grade="U-B",
                        coverage_state="user_attributed",
                        usage=usage,
                    )
                ]
            )
            settings = {"example.tokens": {"totals_enabled": True}}
            authorized_settings = {
                "example.tokens": {
                    "totals_enabled": True,
                    "mapped_provider": "openrouter",
                    "mapped_account_id": "account-1",
                    "full_window_enabled": True,
                }
            }
            disabled = apply_user_token_evidence([], settings={}, store=store)
            enabled = apply_user_evidence([], settings=settings, store=store)
            authorized = apply_user_evidence(
                [], settings=authorized_settings, store=store
            )
            reverted = apply_user_evidence([], settings={}, store=store)
            account = Account(
                provider="openrouter",
                account_id="account-1",
                label="account-1",
                extra={
                    "windows": [
                        {
                            "label": "5h",
                            "used_percent": 50,
                            "window_start": "2026-08-29T23:00:00Z",
                            "resets_at": "2026-08-30T04:00:00Z",
                        }
                    ]
                },
            )
            report = build_report(enabled, accounts=[account], live_quota=False)
            authorized_report = build_report(
                authorized, accounts=[account], live_quota=False
            )

            self.assertEqual(disabled, [])
            self.assertEqual(reverted, [])
            self.assertEqual(len(enabled), 1)
            self.assertEqual(enabled[0].usage.total_tokens, 10)
            self.assertEqual(enabled[0].extra["evidence_class"], "B_token")
            self.assertEqual(
                report["totals"]["evidence_sources"],
                ["user_adapter:example.tokens"],
            )
            window = report["accounts"][0]["local_in_windows"][0]
            self.assertEqual(window["usage"]["total_tokens"], 10)
            self.assertFalse(window["full_window_inference_allowed"])
            self.assertTrue(
                authorized_report["accounts"][0]["local_in_windows"][0][
                    "full_window_inference_allowed"
                ]
            )

    def test_enabled_token_evidence_still_rejects_cross_source_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            usage = TokenUsage(input_tokens=7, output_tokens=3, calls=1)
            store.append(
                [
                    TelemetryRecord(
                        event_key=f"collision-{index}",
                        provider="openrouter",
                        event_name="sandglass.usage",
                        event_at="2026-08-30T00:00:00Z",
                        received_at="2026-08-30T00:00:01Z",
                        account_id="",
                        session_id="shared-session",
                        model="",
                        source=f"user_adapter:example.{index}",
                        source_version="1",
                        schema_version="1",
                        evidence_grade="U-B",
                        coverage_state="user_unassigned_missing_identity",
                        usage=usage,
                    )
                    for index in (1, 2)
                ]
            )
            settings = {
                "example.1": {"totals_enabled": True},
                "example.2": {"totals_enabled": True},
            }

            self.assertEqual(
                apply_user_token_evidence([], settings=settings, store=store), []
            )
            ledger = candidate_user_source_ledger([], settings=settings, store=store)
            self.assertFalse(ledger["tokens_included_in_report"])
            self.assertEqual(ledger["token_enabled_tokens"], 0)

    def test_empty_status_does_not_create_a_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "telemetry.sqlite"

            status = telemetry_status(TelemetryStore(path))

            self.assertFalse(path.exists())
            self.assertEqual(status["receiver"]["state"], "ready")
            self.assertFalse(status["included_in_report"])
            self.assertEqual(
                [
                    (row["provider"], row["state"], row["records"], row["received_records"])
                    for row in status["providers"]
                ],
                [
                    ("claude", "never_observed", 0, 0),
                    ("codex", "never_observed", 0, 0),
                    ("grok", "never_observed", 0, 0),
                ],
            )

    def test_provider_traffic_is_visible_before_a_supported_usage_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")

            result = ingest_otlp_logs(
                _payload(
                    "claude-code",
                    "claude_code.session_start",
                    {"session.id": "connected-session"},
                ),
                store,
            )
            status = telemetry_status(store)
            claude = next(row for row in status["providers"] if row["provider"] == "claude")

            self.assertEqual(result["accepted"], 0)
            self.assertEqual(result["rejected"], 1)
            self.assertEqual(store.records(), [])
            self.assertEqual(claude["state"], "traffic_only")
            self.assertEqual(claude["requests"], 1)
            self.assertEqual(claude["received_records"], 1)
            self.assertEqual(claude["accepted_receipt_records"], 0)
            self.assertEqual(claude["rejected_receipt_records"], 1)
            self.assertTrue(claude["last_received_at"])

            ingest_otlp_logs(
                _payload(
                    "claude-code",
                    "claude_code.api_request",
                    {
                        "request_id": "usage-after-connect",
                        "user.account_id": "claude-account",
                        "input_tokens": 3,
                        "output_tokens": 2,
                    },
                ),
                store,
            )
            updated = telemetry_status(store)
            claude = next(row for row in updated["providers"] if row["provider"] == "claude")
            self.assertEqual(claude["state"], "identity_observed")
            self.assertEqual(claude["requests"], 2)
            self.assertEqual(claude["received_records"], 2)
            self.assertEqual(claude["accepted_receipt_records"], 1)

    def test_status_reads_a_pre_receipt_database_without_migrating_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            ingest_otlp_logs(
                _payload(
                    "codex_cli_rs",
                    "codex.sse_event",
                    {
                        "event.kind": "response.completed",
                        "input_token_count": 3,
                        "output_token_count": 2,
                        "tool_token_count": 5,
                    },
                ),
                store,
            )
            conn = sqlite3.connect(store.path)
            try:
                conn.execute("DROP TABLE telemetry_receipts")
                conn.commit()
            finally:
                conn.close()

            status = telemetry_status(store)
            codex = next(row for row in status["providers"] if row["provider"] == "codex")

            self.assertEqual(codex["state"], "usage_only")
            self.assertEqual(codex["records"], 1)
            self.assertEqual(codex["received_records"], 0)
            conn = sqlite3.connect(store.path)
            try:
                names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
            finally:
                conn.close()
            self.assertNotIn("telemetry_receipts", names)

    def test_status_separates_identity_evidence_from_usage_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            ingest_otlp_logs(
                _payload(
                    "claude-code",
                    "api_request",
                    {
                        "request_id": "with-account",
                        "user.account_id": "claude-account",
                        "input_tokens": 3,
                        "output_tokens": 2,
                    },
                ),
                store,
            )
            ingest_otlp_logs(
                _payload(
                    "claude-code",
                    "api_request",
                    {"request_id": "without-account", "input_tokens": 4, "output_tokens": 1},
                ),
                store,
            )
            ingest_otlp_logs(
                _payload(
                    "codex_cli_rs",
                    "codex.sse_event",
                    {
                        "event.kind": "response.completed",
                        "input_token_count": 6,
                        "output_token_count": 2,
                        "tool_token_count": 8,
                    },
                ),
                store,
            )

            status = telemetry_status(store)
            rows = {row["provider"]: row for row in status["providers"]}

            self.assertEqual(rows["claude"]["state"], "identity_observed")
            self.assertEqual(rows["claude"]["records"], 2)
            self.assertEqual(rows["claude"]["attributed_records"], 1)
            self.assertEqual(rows["claude"]["unassigned_records"], 1)
            self.assertEqual(rows["claude"]["account_count"], 1)
            self.assertTrue(rows["claude"]["first_event_at"])
            self.assertTrue(rows["claude"]["last_received_at"])
            self.assertEqual(rows["codex"]["state"], "usage_only")
            self.assertEqual(rows["grok"]["state"], "never_observed")

    def test_claude_stores_only_allowlisted_usage_and_dedupes_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            payload = _payload(
                "claude-code",
                "claude_code.api_request",
                {
                    "request_id": "req-1",
                    "session.id": "claude-session",
                    "user.account_id": "claude-account",
                    "model": "claude-test",
                    "input_tokens": "10",
                    "cache_read_tokens": "20",
                    "cache_creation_tokens": "30",
                    "output_tokens": "40",
                    "prompt": "SECRET PROMPT MUST NOT PERSIST",
                    "tool_input": "SECRET TOOL INPUT MUST NOT PERSIST",
                    "file_path": "C:/private/source.py",
                },
            )

            first = ingest_otlp_logs(payload, store)
            second = ingest_otlp_logs(payload, store)
            rows = store.records()

            self.assertEqual(first, {"accepted": 1, "inserted": 1, "duplicates": 0, "rejected": 0})
            self.assertEqual(second, {"accepted": 1, "inserted": 0, "duplicates": 1, "rejected": 0})
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["evidence_grade"], "A")
            self.assertEqual(rows[0]["coverage_state"], "attributed")
            self.assertEqual(rows[0]["usage"]["total_tokens"], 100)
            persisted = b"".join(path.read_bytes() for path in Path(tmp).glob("telemetry.sqlite*"))
            self.assertNotIn(b"SECRET PROMPT", persisted)
            self.assertNotIn(b"SECRET TOOL INPUT", persisted)
            self.assertNotIn(b"private/source.py", persisted)
            status = telemetry_status(store)
            claude = next(row for row in status["providers"] if row["provider"] == "claude")
            self.assertEqual(claude["requests"], 2)
            self.assertEqual(claude["received_records"], 2)
            self.assertEqual(claude["accepted_receipt_records"], 2)

    def test_codex_cache_buckets_partition_input_and_reasoning_is_not_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            payload = _payload(
                "codex_cli_rs",
                "codex.sse_event",
                {
                    "event.kind": "response.completed",
                    "conversation.id": "codex-session",
                    "user.account_id": "codex-account",
                    "model": "gpt-test",
                    "input_token_count": 100,
                    "cached_token_count": 40,
                    "cache_write_token_count": 60,
                    "output_token_count": 10,
                    "reasoning_token_count": 5,
                    "tool_token_count": 110,
                },
            )

            result = ingest_otlp_logs(payload, store)
            usage = store.records()[0]["usage"]

            self.assertEqual(result["inserted"], 1)
            self.assertEqual(usage["input_tokens"], 0)
            self.assertEqual(usage["cache_read_tokens"], 40)
            self.assertEqual(usage["cache_write_tokens"], 60)
            self.assertEqual(usage["output_tokens"], 10)
            self.assertEqual(usage["reasoning_tokens"], 5)
            self.assertEqual(usage["total_tokens"], 110)

    def test_codex_completion_reads_event_name_from_attributes_not_tracing_target(self):
        """0.149 fills LogRecord.event_name with codex_otel.log_only."""
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            result = ingest_otlp_logs(
                _payload(
                    "codex-cli",
                    "codex_otel.log_only",
                    {
                        "event.name": "codex.sse_event",
                        "event.kind": "response.completed",
                        "conversation.id": "codex-session",
                        "user.account_id": "codex-account",
                        "input_token_count": 100,
                        "cached_token_count": 40,
                        "cache_write_token_count": 60,
                        "output_token_count": 10,
                        "reasoning_token_count": 5,
                        "tool_token_count": 110,
                    },
                ),
                store,
            )
            rows = store.records()
            self.assertEqual(result["accepted"], 1)
            self.assertEqual(result["rejected"], 0)
            self.assertEqual(rows[0]["account_id"], "codex-account")
            self.assertEqual(rows[0]["event_name"], "codex.sse_event/response.completed")
            self.assertEqual(rows[0]["usage"]["total_tokens"], 110)

    def test_codex_completion_accepts_gen_ai_usage_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            result = ingest_otlp_logs(
                _payload(
                    "codex-cli",
                    "codex_otel.log_only",
                    {
                        "event.name": "codex.sse_event",
                        "event.kind": "response.completed",
                        "conversation.id": "codex-session",
                        "user.account_id": "codex-account",
                        "gen_ai.usage.input_tokens": 80,
                        "gen_ai.usage.output_tokens": 20,
                    },
                ),
                store,
            )
            self.assertEqual(result["accepted"], 1)
            self.assertEqual(store.records()[0]["usage"]["input_tokens"], 80)
            self.assertEqual(store.records()[0]["usage"]["output_tokens"], 20)

    def test_codex_non_completion_sse_stays_traffic(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            result = ingest_otlp_logs(
                _payload(
                    "codex-cli",
                    "codex_otel.log_only",
                    {
                        "event.name": "codex.sse_event",
                        "event.kind": "response.output_item.done",
                        "conversation.id": "codex-session",
                    },
                ),
                store,
            )
            self.assertEqual(result["accepted"], 0)
            self.assertEqual(result["rejected"], 1)
            self.assertEqual(store.records(), [])
            status = telemetry_status(store)
            codex = next(row for row in status["providers"] if row["provider"] == "codex")
            self.assertEqual(codex["state"], "traffic_only")

    def test_grok_cached_and_reasoning_are_subsets(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            payload = _payload(
                "grok-cli",
                "grok_code.api_request",
                {
                    "event.sequence": 7,
                    "prompt.id": "prompt-1",
                    "session.id": "grok-session",
                    "user.id": "grok-account",
                    "model": "grok-test",
                    "input_tokens": 100,
                    "cache_read_tokens": 40,
                    "output_tokens": 10,
                    "reasoning_tokens": 5,
                },
                version="1.0.5",
            )

            ingest_otlp_logs(payload, store)
            row = store.records()[0]

            self.assertEqual(row["usage"]["input_tokens"], 60)
            self.assertEqual(row["usage"]["cache_read_tokens"], 40)
            self.assertEqual(row["usage"]["output_tokens"], 10)
            self.assertEqual(row["usage"]["reasoning_tokens"], 5)
            self.assertEqual(row["usage"]["total_tokens"], 110)
            self.assertEqual(row["source_version"], "1.0.5")

    def test_missing_identity_is_explicitly_unassigned(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            payload = _payload(
                "claude-code",
                "api_request",
                {"request_id": "req-no-account", "input_tokens": 3, "output_tokens": 2},
            )

            ingest_otlp_logs(payload, store)
            row = store.records()[0]

            self.assertEqual(row["account_id"], "")
            self.assertEqual(row["evidence_grade"], "B")
            self.assertEqual(row["coverage_state"], "unassigned_missing_identity")

    def test_explicit_user_adapter_is_stored_and_labeled_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            payload = _payload(
                "local-openrouter-meter",
                "sandglass.usage",
                {
                    "sandglass.provider": "openrouter",
                    "sandglass.source": "example.local-adapter",
                    "sandglass.source_version": "1.0.0",
                    "sandglass.schema_version": "1",
                    "sandglass.event_id": "event-1",
                    "sandglass.account_id": "local-account",
                    "sandglass.session_id": "local-session",
                    "sandglass.model": "example-model",
                    "sandglass.input_tokens": 7,
                    "sandglass.output_tokens": 3,
                    "sandglass.reasoning_tokens": 1,
                    "sandglass.total_tokens": 10,
                    "prompt": "MUST NOT PERSIST",
                },
            )

            result = ingest_otlp_logs(payload, store)
            row = store.records()[0]
            custom = next(
                item for item in telemetry_status(store)["providers"]
                if item["provider"] == "openrouter"
            )

            self.assertEqual(result["inserted"], 1)
            self.assertEqual(row["source"], "user_adapter:example.local-adapter")
            self.assertEqual(row["evidence_grade"], "U-A")
            self.assertEqual(row["coverage_state"], "user_attributed")
            self.assertEqual(row["usage"]["total_tokens"], 10)
            self.assertEqual(custom["source_kind"], "user_adapter")
            self.assertEqual(custom["records"], 1)
            self.assertEqual(custom["sources"], ["user_adapter:example.local-adapter"])
            source_shadow = reconcile_telemetry_minutes([], store)["by_source"][0]
            self.assertEqual(source_shadow["source_id"], "example.local-adapter")
            self.assertEqual(source_shadow["unmatched_records"], 1)
            self.assertEqual(source_shadow["missing_local_tokens"], 10)
            persisted = b"".join(path.read_bytes() for path in Path(tmp).glob("telemetry.sqlite*"))
            self.assertNotIn(b"MUST NOT PERSIST", persisted)

    def test_user_adapter_can_target_builtin_but_cannot_skip_total_invariant(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            base = {
                "sandglass.source": "example.local-adapter",
                "sandglass.event_id": "event-1",
                "sandglass.input_tokens": 7,
                "sandglass.output_tokens": 3,
                "sandglass.total_tokens": 11,
            }

            inconsistent = ingest_otlp_logs(
                _payload(
                    "local-adapter",
                    "sandglass.usage",
                    {**base, "sandglass.provider": "openrouter"},
                ),
                store,
            )
            targeting_builtin = ingest_otlp_logs(
                _payload(
                    "local-adapter",
                    "sandglass.usage",
                    {**base, "sandglass.provider": "claude", "sandglass.total_tokens": 10},
                ),
                store,
            )
            replay = ingest_otlp_logs(
                _payload(
                    "local-adapter",
                    "sandglass.usage",
                    {**base, "sandglass.provider": "claude", "sandglass.total_tokens": 10},
                ),
                store,
            )

            self.assertEqual(inconsistent["rejected"], 1)
            self.assertEqual(targeting_builtin["inserted"], 1)
            self.assertEqual(replay["duplicates"], 1)
            row = store.records()[0]
            self.assertEqual(row["provider"], "claude")
            self.assertEqual(row["source"], "user_adapter:example.local-adapter")
            self.assertEqual(row["evidence_grade"], "U-B")
            claude = next(
                item for item in telemetry_status(store)["providers"]
                if item["provider"] == "claude"
            )
            self.assertEqual(claude["source_kind"], "user_adapter")

    def test_user_adapter_shadow_is_reported_per_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            ingest_otlp_logs(
                _payload(
                    "local-adapter",
                    "sandglass.usage",
                    {
                        "sandglass.provider": "claude",
                        "sandglass.source": "example.local-adapter",
                        "sandglass.event_id": "event-1",
                        "sandglass.account_id": "account-1",
                        "sandglass.session_id": "session-1",
                        "sandglass.input_tokens": 7,
                        "sandglass.output_tokens": 3,
                        "sandglass.total_tokens": 10,
                    },
                ),
                store,
            )
            event_at = store.records()[0]["event_at"]
            usage = TokenUsage(input_tokens=7, output_tokens=3, calls=1)
            session = SessionRecord(
                provider="claude",
                session_id="session-1",
                path="official.jsonl",
                usage=usage,
                timeline=[(event_at, usage)],
            )

            result = reconcile_telemetry_minutes([session], store)
            source = result["by_source"][0]

            self.assertEqual(source["source_id"], "example.local-adapter")
            self.assertEqual(source["normalized_records"], 1)
            self.assertEqual(source["exact_matches"], 1)
            self.assertEqual(source["missing_local"], 0)
            self.assertEqual(source["normalized_tokens"], 10)

    def test_candidate_ledger_uses_exact_official_match_for_identity_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            ingest_otlp_logs(
                _payload(
                    "local-adapter",
                    "sandglass.usage",
                    {
                        "sandglass.provider": "claude",
                        "sandglass.source": "example.identity",
                        "sandglass.event_id": "identity-1",
                        "sandglass.session_id": "session-1",
                        "sandglass.input_tokens": 7,
                        "sandglass.output_tokens": 3,
                        "sandglass.total_tokens": 10,
                    },
                ),
                store,
            )
            event_at = store.records()[0]["event_at"]
            usage = TokenUsage(input_tokens=7, output_tokens=3, calls=1)
            session = SessionRecord(
                provider="claude",
                session_id="session-1",
                path="official.jsonl",
                usage=usage,
                timeline=[(event_at, usage)],
            )

            result = candidate_user_source_ledger(
                [session],
                settings={
                    "example.identity": {
                        "mapped_provider": "claude",
                        "mapped_account_id": "account-1",
                    }
                },
                store=store,
            )

            self.assertFalse(result["candidate_only"])
            self.assertTrue(result["included_in_report"])
            self.assertTrue(result["identity_included_in_report"])
            self.assertFalse(result["tokens_included_in_report"])
            self.assertEqual(result["identity_supplement_minutes"], 1)
            self.assertFalse(result["admission_ready"])
            self.assertEqual(
                result["collision_key"], "provider+session_id+exact_event_signature"
            )
            self.assertEqual(result["ambiguity_key"], "provider+utc_minute")
            self.assertEqual(result["identity_candidates"], 1)
            self.assertEqual(result["identity_candidate_tokens"], 10)
            self.assertEqual(result["token_candidates"], 0)
            self.assertEqual(result["sources"][0]["official_duplicate_minutes"], 1)

    def test_mapped_exact_identity_moves_no_tokens_and_is_reversible(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            ingest_otlp_logs(
                _payload(
                    "local-adapter",
                    "sandglass.usage",
                    {
                        "sandglass.provider": "claude",
                        "sandglass.source": "example.identity",
                        "sandglass.event_id": "identity-1",
                        "sandglass.session_id": "session-1",
                        "sandglass.input_tokens": 7,
                        "sandglass.output_tokens": 3,
                        "sandglass.total_tokens": 10,
                    },
                ),
                store,
            )
            event_at = store.records()[0]["event_at"]
            event_time = datetime.fromisoformat(event_at.replace("Z", "+00:00"))
            window_start = (event_time - timedelta(hours=1)).isoformat()
            window_end = (event_time + timedelta(hours=1)).isoformat()
            usage = TokenUsage(input_tokens=7, output_tokens=3, calls=1)
            session = SessionRecord(
                provider="claude",
                session_id="session-1",
                path="official.jsonl",
                usage=usage,
                timeline=[(event_at, usage)],
            )
            account = Account(
                provider="claude",
                account_id="account-1",
                label="claude-a",
                extra={
                    "windows": [
                        {
                            "label": "5h",
                            "window_start": window_start,
                            "resets_at": window_end,
                        }
                    ]
                },
            )
            setting = {
                "example.identity": {
                    "mapped_provider": "claude",
                    "mapped_account_id": "account-1",
                }
            }
            authorized_setting = {
                "example.identity": {
                    **setting["example.identity"],
                    "full_window_enabled": True,
                }
            }

            supplemented = apply_user_identity_evidence(
                [session], settings=setting, store=store
            )
            reverted = apply_user_identity_evidence([session], settings={}, store=store)
            authorized = apply_user_identity_evidence(
                [session], settings=authorized_setting, store=store
            )
            supplemented_report = build_report(
                supplemented, accounts=[account], live_quota=False
            )
            reverted_report = build_report(reverted, accounts=[account], live_quota=False)
            authorized_report = build_report(
                authorized, accounts=[account], live_quota=False
            )

            self.assertEqual(
                supplemented_report["totals"]["usage"]["total_tokens"], 10
            )
            self.assertEqual(reverted_report["totals"]["usage"]["total_tokens"], 10)
            self.assertEqual(
                supplemented_report["accounts"][0]["local_usage"]["total_tokens"],
                10,
            )
            self.assertEqual(
                reverted_report["accounts"][0]["local_usage"]["total_tokens"], 0
            )
            self.assertEqual(
                supplemented_report["accounts"][0]["local_evidence_sources"],
                ["official_builtin:claude", "user_adapter:example.identity"],
            )
            self.assertEqual(
                supplemented_report["accounts"][0]["local_in_windows"][0][
                    "evidence_sources"
                ],
                ["official_builtin:claude", "user_adapter:example.identity"],
            )
            self.assertFalse(
                supplemented_report["accounts"][0]["local_in_windows"][0][
                    "full_window_inference_allowed"
                ]
            )
            self.assertTrue(
                authorized_report["accounts"][0]["local_in_windows"][0][
                    "full_window_inference_allowed"
                ]
            )
            self.assertEqual(
                supplemented_report["totals"]["evidence_sources"],
                ["official_builtin:claude"],
            )
            from sandglass.serve import _activity_days, _windows_for

            windows = [
                {
                    "label": "5h",
                    "window_start": window_start,
                    "resets_at": window_end,
                }
            ]
            with patch("sandglass.serve.quota_anchor", return_value=("", "", False)):
                fast_window = _windows_for(
                    account,
                    [account],
                    supplemented,
                    windows,
                    single_account_owner_id="",
                )[0]
            activity = _activity_days(
                account,
                supplemented,
                None,
                [account],
                single_account_owner_id="",
            )
            self.assertEqual(
                fast_window["evidence_sources"],
                ["official_builtin:claude", "user_adapter:example.identity"],
            )
            active_day = next(day for day in fast_window["days"] if day["spent"])
            self.assertEqual(
                active_day["evidence_sources"],
                ["official_builtin:claude", "user_adapter:example.identity"],
            )
            self.assertEqual(
                activity["evidence_sources"],
                ["official_builtin:claude", "user_adapter:example.identity"],
            )

    def test_user_identity_never_overwrites_existing_or_conflicting_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            for source, account, event_id in (
                ("example.one", "account-2", "identity-1"),
                ("example.two", "account-3", "identity-2"),
            ):
                ingest_otlp_logs(
                    _payload(
                        "local-adapter",
                        "sandglass.usage",
                        {
                            "sandglass.provider": "claude",
                            "sandglass.source": source,
                            "sandglass.event_id": event_id,
                            "sandglass.session_id": "session-1",
                            "sandglass.account_id": account,
                            "sandglass.input_tokens": 7,
                            "sandglass.output_tokens": 3,
                            "sandglass.total_tokens": 10,
                        },
                    ),
                    store,
                )
            event_at = store.records()[0]["event_at"]
            usage = TokenUsage(input_tokens=7, output_tokens=3, calls=1)
            assigned = SessionRecord(
                provider="claude",
                session_id="session-1",
                path="official.jsonl",
                account_id="account-1",
                usage=usage,
                timeline=[(event_at, usage)],
            )
            settings = {
                "example.one": {
                    "mapped_provider": "claude",
                    "mapped_account_id": "account-2",
                },
                "example.two": {
                    "mapped_provider": "claude",
                    "mapped_account_id": "account-3",
                },
            }

            supplemented = apply_user_identity_evidence(
                [assigned], settings=settings, store=store
            )
            unassigned = SessionRecord(
                provider="claude",
                session_id="session-1",
                path="official.jsonl",
                usage=usage,
                timeline=[(event_at, usage)],
            )
            conflicted = apply_user_identity_evidence(
                [unassigned], settings=settings, store=store
            )

            self.assertNotIn("minute_identity_evidence", supplemented[0].extra)
            self.assertEqual(supplemented[0].account_id, "account-1")
            self.assertNotIn("minute_identity_evidence", conflicted[0].extra)

    def test_exact_minute_identity_composes_with_inferred_session_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            ingest_user_source_records(
                {
                    "source": "example.activation",
                    "records": [
                        {
                            "provider": "codex",
                            "event_id": "activation-1",
                            "timestamp": "2026-08-30T00:01:00Z",
                            "account_id": "new-account",
                            "session_id": "session-1",
                            "input_tokens": 7,
                            "output_tokens": 3,
                            "total_tokens": 10,
                            "calls": 1,
                        }
                    ],
                },
                store,
            )
            usage = TokenUsage(input_tokens=7, output_tokens=3, calls=1)
            inferred = SessionRecord(
                provider="codex",
                session_id="session-1",
                path="official.jsonl",
                account_id="old-account",
                usage=usage,
                timeline=[("2026-08-30T00:01:00Z", usage)],
                extra={"account_identity_scope": "session_timeline_majority"},
            )
            settings = {"example.activation": {"identity_enabled": True}}

            ledger = candidate_user_source_ledger(
                [inferred], settings=settings, store=store
            )
            supplemented = apply_user_identity_evidence(
                [inferred], settings=settings, store=store
            )[0]
            new_account = Account(
                provider="codex",
                account_id="new-account",
                label="new-account",
            )
            old_account = Account(
                provider="codex",
                account_id="old-account",
                label="old-account",
            )
            moment = datetime.fromisoformat("2026-08-30T00:01:00+00:00")
            old_runs = [("2026-08-29T00:00:00Z", "old-account")]

            self.assertEqual(ledger["identity_candidates"], 1)
            self.assertEqual(ledger["identity_conflicts"], 0)
            self.assertEqual(
                supplemented.extra["minute_identity_evidence"][
                    "2026-08-30T00:01:00Z"
                ]["account_id"],
                "new-account",
            )
            self.assertTrue(
                owns_minute(
                    new_account,
                    old_runs,
                    supplemented,
                    moment,
                    {"old-account", "new-account"},
                )
            )
            self.assertFalse(
                owns_minute(
                    old_account,
                    old_runs,
                    supplemented,
                    moment,
                    {"old-account", "new-account"},
                )
            )
            self.assertEqual(
                supplemental_sources_for_account(
                    new_account,
                    old_runs,
                    supplemented,
                    moment,
                ),
                ["user_adapter:example.activation"],
            )

    def test_candidate_ledger_blocks_cross_source_token_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            for source, event_id in (("example.one", "event-1"), ("example.two", "event-2")):
                ingest_otlp_logs(
                    _payload(
                        "local-adapter",
                        "sandglass.usage",
                        {
                            "sandglass.provider": "openrouter",
                            "sandglass.source": source,
                            "sandglass.event_id": event_id,
                            "sandglass.session_id": "shared-session",
                            "sandglass.input_tokens": 7,
                            "sandglass.output_tokens": 3,
                            "sandglass.total_tokens": 10,
                        },
                    ),
                    store,
                )

            result = candidate_user_source_ledger([], store=store)

            self.assertEqual(result["token_candidates"], 0)
            self.assertEqual(result["cross_source_collisions"], 1)
            self.assertEqual(result["blocked_source_minutes"], 2)
            self.assertEqual(result["blocked_source_tokens"], 20)
            self.assertEqual(
                [row["reasons"] for row in result["sources"]],
                [
                    {"cross_source_token_collision": 1},
                    {"cross_source_token_collision": 1},
                ],
            )

    def test_candidate_ledger_accepts_distinct_cross_source_events_in_one_session_minute(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            store.append(
                [
                    TelemetryRecord(
                        event_key=f"distinct-{index}",
                        provider="openrouter",
                        event_name="sandglass.usage",
                        event_at=f"2026-08-30T00:00:{second:02d}Z",
                        received_at="2026-08-30T00:01:00Z",
                        account_id="account-1",
                        session_id="shared-session",
                        model="",
                        source=f"user_adapter:example.{index}",
                        source_version="1",
                        schema_version="1",
                        evidence_grade="U-A",
                        coverage_state="user_attributed",
                        usage=TokenUsage(
                            input_tokens=7 + index,
                            output_tokens=3,
                            calls=1,
                        ),
                    )
                    for index, second in ((1, 3), (2, 51))
                ]
            )

            result = candidate_user_source_ledger(
                [],
                settings={
                    "example.1": {"totals_enabled": True},
                    "example.2": {"totals_enabled": True},
                },
                store=store,
            )

            self.assertEqual(result["token_candidates"], 2)
            self.assertEqual(result["token_enabled_tokens"], 23)
            self.assertEqual(result["cross_source_collisions"], 0)
            self.assertEqual(result["blocked_source_tokens"], 0)

    def test_candidate_ledger_does_not_hide_declared_mapping_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            ingest_otlp_logs(
                _payload(
                    "local-adapter",
                    "sandglass.usage",
                    {
                        "sandglass.provider": "claude",
                        "sandglass.source": "example.conflict",
                        "sandglass.event_id": "event-1",
                        "sandglass.account_id": "declared-account",
                        "sandglass.session_id": "session-1",
                        "sandglass.input_tokens": 7,
                        "sandglass.output_tokens": 3,
                        "sandglass.total_tokens": 10,
                    },
                ),
                store,
            )
            event_at = store.records()[0]["event_at"]
            usage = TokenUsage(input_tokens=7, output_tokens=3, calls=1)
            session = SessionRecord(
                provider="claude",
                session_id="session-1",
                path="official.jsonl",
                usage=usage,
                timeline=[(event_at, usage)],
            )

            result = candidate_user_source_ledger(
                [session],
                settings={
                    "example.conflict": {
                        "mapped_provider": "claude",
                        "mapped_account_id": "different-account",
                    }
                },
                store=store,
            )

            self.assertEqual(result["identity_candidates"], 0)
            self.assertEqual(result["sources"][0]["identity_blocked_minutes"], 1)
            self.assertEqual(result["sources"][0]["reasons"], {"identity_conflict": 1})

    def test_candidate_ledger_blocks_possible_official_cross_session_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            ingest_otlp_logs(
                _payload(
                    "local-adapter",
                    "sandglass.usage",
                    {
                        "sandglass.provider": "claude",
                        "sandglass.source": "example.cross-session",
                        "sandglass.event_id": "event-1",
                        "sandglass.session_id": "adapter-session",
                        "sandglass.input_tokens": 7,
                        "sandglass.output_tokens": 3,
                        "sandglass.total_tokens": 10,
                    },
                ),
                store,
            )
            event_at = store.records()[0]["event_at"]
            usage = TokenUsage(input_tokens=7, output_tokens=3, calls=1)
            official = SessionRecord(
                provider="claude",
                session_id="different-official-session",
                path="official.jsonl",
                usage=usage,
                timeline=[(event_at, usage)],
            )

            result = candidate_user_source_ledger([official], store=store)

            self.assertEqual(result["token_candidates"], 0)
            self.assertEqual(result["cross_session_official_ambiguities"], 1)
            self.assertEqual(
                result["sources"][0]["reasons"],
                {"possible_official_cross_session_collision": 1},
            )

    def test_candidate_ledger_accepts_distinct_user_sessions_in_the_same_minute(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            for source, session_id, event_id in (
                ("example.one", "session-one", "event-1"),
                ("example.two", "session-two", "event-2"),
            ):
                ingest_otlp_logs(
                    _payload(
                        "local-adapter",
                        "sandglass.usage",
                        {
                            "sandglass.provider": "openrouter",
                            "sandglass.source": source,
                            "sandglass.event_id": event_id,
                            "sandglass.session_id": session_id,
                            "sandglass.input_tokens": 7,
                            "sandglass.output_tokens": 3,
                            "sandglass.total_tokens": 10,
                        },
                    ),
                    store,
                )

            result = candidate_user_source_ledger(
                [],
                settings={
                    "example.one": {"totals_enabled": True},
                    "example.two": {"totals_enabled": True},
                },
                store=store,
            )

            self.assertEqual(result["token_candidates"], 2)
            self.assertEqual(result["token_enabled_tokens"], 20)
            self.assertEqual(result["concurrent_user_provider_minutes"], 1)
            self.assertEqual(result["cross_session_user_ambiguities"], 0)
            self.assertEqual(result["blocked_source_minutes"], 0)
            self.assertEqual([row["reasons"] for row in result["sources"]], [{}, {}])

    def test_candidate_ledger_accepts_one_missing_local_source_as_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            ingest_otlp_logs(
                _payload(
                    "local-adapter",
                    "sandglass.usage",
                    {
                        "sandglass.provider": "openrouter",
                        "sandglass.source": "example.only",
                        "sandglass.event_id": "event-1",
                        "sandglass.session_id": "source-session",
                        "sandglass.input_tokens": 7,
                        "sandglass.output_tokens": 3,
                        "sandglass.total_tokens": 10,
                    },
                ),
                store,
            )

            result = candidate_user_source_ledger([], store=store)

            self.assertEqual(result["token_candidates"], 1)
            self.assertEqual(result["token_candidate_tokens"], 10)
            self.assertEqual(result["cross_source_collisions"], 0)
            self.assertEqual(result["candidate_sources"], ["example.only"])

    def test_shadow_reconciliation_requires_an_exact_minute_and_token_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            ingest_otlp_logs(
                _payload(
                    "claude-code",
                    "api_request",
                    {
                        "request_id": "exact-1",
                        "session.id": "session-exact",
                        "user.account_id": "account-exact",
                        "input_tokens": 10,
                        "cache_read_tokens": 20,
                        "cache_creation_tokens": 30,
                        "output_tokens": 40,
                    },
                ),
                store,
            )
            event_at = store.records()[0]["event_at"]
            local_usage = TokenUsage(
                input_tokens=10,
                cache_read_tokens=20,
                cache_write_tokens=30,
                output_tokens=40,
                calls=1,
            )
            session = SessionRecord(
                provider="claude",
                session_id="session-exact",
                path="local.jsonl",
                usage=local_usage,
                timeline=[(event_at, local_usage)],
            )

            result = reconcile_telemetry_minutes([session], store)

            self.assertTrue(result["shadow_only"])
            self.assertFalse(result["included_in_report"])
            self.assertEqual(result["identity_groups"], 1)
            self.assertEqual(result["exact_matches"], 1)
            self.assertEqual(result["matched_tokens"], 100)
            self.assertEqual(result["token_mismatches"], 0)
            self.assertEqual(result["matches"][0]["account_id"], "account-exact")
            self.assertEqual(session.account_id, "")
            self.assertEqual(session.usage.total_tokens, 100)

            changed = SessionRecord(
                provider="claude",
                session_id="session-exact",
                path="changed.jsonl",
                usage=TokenUsage(
                    input_tokens=10,
                    output_tokens=39,
                    cache_read_tokens=20,
                    cache_write_tokens=30,
                    calls=1,
                ),
                timeline=[
                    (
                        event_at,
                        TokenUsage(
                            input_tokens=10,
                            output_tokens=39,
                            cache_read_tokens=20,
                            cache_write_tokens=30,
                            calls=1,
                        ),
                    )
                ],
            )
            rejected = reconcile_telemetry_minutes([changed], store)
            self.assertEqual(rejected["exact_matches"], 0)
            self.assertEqual(rejected["token_mismatches"], 1)

    def test_shadow_reconciliation_rejects_account_conflicts_and_missing_local_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            for request_id, account_id in (("one", "account-1"), ("two", "account-2")):
                ingest_otlp_logs(
                    _payload(
                        "claude-code",
                        "api_request",
                        {
                            "request_id": request_id,
                            "session.id": "shared-session",
                            "user.account_id": account_id,
                            "input_tokens": 5,
                            "output_tokens": 5,
                        },
                    ),
                    store,
                )

            result = reconcile_telemetry_minutes([], store)

            self.assertEqual(result["identity_groups"], 1)
            self.assertEqual(result["account_conflicts"], 1)
            self.assertEqual(result["missing_local"], 0)
            self.assertEqual(result["exact_matches"], 0)

            single = TelemetryStore(Path(tmp) / "single.sqlite")
            ingest_otlp_logs(
                _payload(
                    "grok-cli",
                    "grok_code.api_request",
                    {
                        "event.sequence": 1,
                        "prompt.id": "missing",
                        "session.id": "missing-session",
                        "user.id": "grok-account",
                        "input_tokens": 8,
                        "output_tokens": 2,
                    },
                ),
                single,
            )
            missing = reconcile_telemetry_minutes([], single)
            self.assertEqual(missing["missing_local"], 1)
            self.assertEqual(missing["account_conflicts"], 0)

    def test_inconsistent_provider_totals_are_rejected_instead_of_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelemetryStore(Path(tmp) / "telemetry.sqlite")
            payload = _payload(
                "codex_cli_rs",
                "codex.sse_event",
                {
                    "event.kind": "response.completed",
                    "user.account_id": "codex-account",
                    "input_token_count": 100,
                    "cached_token_count": 80,
                    "cache_write_token_count": 30,
                    "output_token_count": 10,
                    "tool_token_count": 110,
                },
            )

            result = ingest_otlp_logs(payload, store)

            self.assertEqual(result["rejected"], 1)
            self.assertEqual(store.records(), [])

    def test_dashboard_accepts_gzipped_otlp_protobuf_on_localhost(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0), partial(Handler, since=None, live_quota=False)
            )
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                payload = _payload(
                    "grok-cli",
                    "grok_code.api_request",
                    {
                        "event.sequence": 1,
                        "user.id": "grok-account",
                        "input_tokens": 8,
                        "output_tokens": 2,
                    },
                )
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/v1/logs",
                    data=gzip.compress(payload),
                    headers={
                        "Content-Type": "application/x-protobuf",
                        "Content-Encoding": "gzip",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get_content_type(), "application/x-protobuf")
                self.assertEqual(len(TelemetryStore().records()), 1)
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{httpd.server_port}/api/telemetry-status", timeout=3
                ) as response:
                    status = json.load(response)
                grok = next(row for row in status["providers"] if row["provider"] == "grok")
                self.assertEqual(status["receiver"]["state"], "ready")
                self.assertFalse(status["included_in_report"])
                self.assertEqual(grok["state"], "identity_observed")
                self.assertEqual(grok["attributed_records"], 1)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=3)

    def test_otlp_only_handler_accepts_logs_but_rejects_dashboard_gets(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0), partial(OtlpHandler, since=None, live_quota=False)
            )
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{httpd.server_port}/api/quota", timeout=3
                    )
                self.assertEqual(raised.exception.code, 404)

                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/v1/logs",
                    data=_payload(
                        "grok-cli",
                        "grok_code.api_request",
                        {"event.sequence": 1, "user.id": "account", "input_tokens": 2},
                    ),
                    headers={"Content-Type": "application/x-protobuf"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 200)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=3)

    def test_gzip_decoder_reads_only_through_the_body_limit(self):
        requested = []

        class FakeGzipFile:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size):
                requested.append(size)
                return b"x" * size

        with patch("sandglass.serve.gzip.GzipFile", return_value=FakeGzipFile()):
            with self.assertRaises(_GzipBodyTooLarge):
                _bounded_gzip_decompress(b"compressed")

        self.assertEqual(requested, [MAX_OTLP_BODY + 1])

    def test_dashboard_rejects_gzip_body_larger_than_limit(self):
        httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(Handler, since=None, live_quota=False)
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{httpd.server_port}/v1/logs",
                data=gzip.compress(b"x" * (MAX_OTLP_BODY + 1)),
                headers={
                    "Content-Type": "application/x-protobuf",
                    "Content-Encoding": "gzip",
                },
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=3)
            self.assertEqual(raised.exception.code, 413)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)

    def test_dashboard_rejects_json_instead_of_silently_dropping_it(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0), partial(Handler, since=None, live_quota=False)
            )
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/v1/logs",
                    data=json.dumps({}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request, timeout=3)
                self.assertEqual(raised.exception.code, 415)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=3)

    def test_the_ledger_refuses_what_a_web_page_can_post_without_asking(self):
        """This is what the accepted content-type list is for.

        The receiver listens on loopback, and a page in the user's browser is
        on loopback too. A cross-origin POST is sent without permission only
        when its content type is one of three -- text/plain,
        application/x-www-form-urlencoded, multipart/form-data -- or when it
        carries none at all. Anything else makes the browser ask first with a
        preflight, and this server implements no OPTIONS to answer it with.

        So those four are the whole exposure: if any of them reached
        ingest_otlp_logs, any site the user visits could write usage into the
        books this product exists to keep honest. The existing test pins
        application/json, which a page cannot send unasked either -- it does
        not pin this.

        http.client, not urllib: urllib puts back a
        Content-type: application/x-www-form-urlencoded of its own when a
        request with a body has none, so the fourth case would have been the
        second case wearing a different name.
        """
        reachable_without_a_preflight = (
            "text/plain",
            "application/x-www-form-urlencoded",
            "multipart/form-data",
            None,
        )
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0), partial(Handler, since=None, live_quota=False)
            )
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                for content_type in reachable_without_a_preflight:
                    with self.subTest(content_type=content_type):
                        conn = http.client.HTTPConnection(
                            "127.0.0.1", httpd.server_port, timeout=3
                        )
                        try:
                            conn.request(
                                "POST", "/v1/logs", body=b"forged-not-protobuf",
                                headers=(
                                    {} if content_type is None
                                    else {"Content-Type": content_type}
                                ),
                            )
                            response = conn.getresponse()
                            response.read()
                        finally:
                            conn.close()
                        self.assertEqual(response.status, 415)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=3)

    def test_dashboard_rejects_unknown_content_encoding(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0), partial(Handler, since=None, live_quota=False)
            )
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/v1/logs",
                    data=b"not-brotli",
                    headers={
                        "Content-Type": "application/x-protobuf",
                        "Content-Encoding": "br",
                    },
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request, timeout=3)
                self.assertEqual(raised.exception.code, 415)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
