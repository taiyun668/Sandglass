import ast
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from sandglass.accounts import load_accounts
from sandglass.models import Account, SessionRecord, TokenUsage
from sandglass.report import build_report
from sandglass.serve import (
    Handler,
    OtlpHandler,
    _quota_payload,
    api_payload,
    configure_user_source,
)
from sandglass.telemetry import TelemetryRecord, TelemetryStore
from sandglass.user_sources import UserSourceStore


def _accounting_snapshot(report):
    keys = (
        "totals",
        "by_provider",
        "by_account",
        "by_client",
        "by_day",
        "by_provider_day",
        "unassigned_by_provider",
        "by_block",
        "accounts",
        "sessions",
        "session_count",
        "subagent_count",
    )
    return {key: report.get(key) for key in keys}


def _whole_import(revision="rev-1", *, event_id="event-1", tokens=10, account_id="account-1"):
    return {
        "source": "adapter.example",
        "revision": revision,
        "receipts": [{"native_event": event_id, "native_tokens": tokens}],
        "accounts": [
            {
                "provider": "codex",
                "account_id": account_id,
                "email": f"{account_id}@example.test",
                "label": account_id,
            }
        ],
        "quotas": [
            {
                "provider": "codex",
                "account_id": account_id,
                "fetched_at": "2026-08-30T00:00:00Z",
                "plan": "pro",
                "windows": [{"label": "7d", "used_percent": 25}],
            }
        ],
        "records": [
            {
                "provider": "codex",
                "event_id": event_id,
                "timestamp": "2026-08-30T00:00:00Z",
                "account_id": account_id,
                "session_id": f"session-{event_id}",
                "input_tokens": tokens - 2,
                "output_tokens": 2,
                "reasoning_tokens": 1,
                "total_tokens": tokens,
                "calls": 1,
            }
        ],
        "expected": {
            "receipts": 1,
            "accounts": 1,
            "quotas": 1,
            "records": 1,
            "total_tokens": tokens,
        },
    }


class UserSourceTests(unittest.TestCase):
    def test_whole_import_preflight_is_read_only_and_measures_the_package(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            store = UserSourceStore()
            result = store.preflight_import(_whole_import())

            self.assertTrue(result["ready_to_commit"])
            self.assertEqual(result["action"], "create")
            self.assertEqual(result["summary"]["total_tokens"], 10)
            self.assertEqual(result["summary"]["quota_accounts"], 1)
            self.assertFalse(result["persisted"])
            self.assertFalse(Path(tmp, "user-sources.sqlite").exists())
            self.assertFalse(Path(tmp, "telemetry.sqlite").exists())

    def test_whole_import_commit_is_atomic_immutable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            store = UserSourceStore()
            package = _whole_import()
            committed = store.commit_import({"package": package})
            repeated = store.commit_import({"package": package})
            changed = _whole_import(tokens=11)

            self.assertTrue(committed["persisted"])
            self.assertFalse(committed["idempotent"])
            self.assertTrue(repeated["idempotent"])
            self.assertEqual(len(store.receipts()), 1)
            self.assertEqual(len(store.accounts()), 1)
            self.assertEqual(len(store.quotas()), 1)
            self.assertEqual(
                sum(row["usage"]["total_tokens"] for row in TelemetryStore().records()),
                10,
            )
            with patch("sandglass.serve.collect_all", return_value=[]):
                mirror = api_payload("/api/user-sources", live_quota=False)
            revision = mirror["inbox"]["sources"][0]["import_revision"]
            self.assertEqual(revision["active_revision"], "rev-1")
            self.assertEqual(revision["active_package_sha256"], committed["package_sha256"])
            self.assertEqual(revision["retained_revisions"], 1)
            with self.assertRaisesRegex(ValueError, "preflight failed"):
                store.commit_import({"package": changed})
            self.assertEqual(store.import_status("adapter.example")["source"]["active_revision"], "rev-1")

    def test_recommitting_a_retained_revision_moves_the_pointer_and_keeps_its_guards(self):
        """Committed once is not the same as in force now.

        A package whose revision is already retained used to return early with
        persisted=true before replace and expected_active_revision were read at
        all, so a caller doing compare-and-swap was told its package was in
        place while a different revision stayed active.
        """
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            store = UserSourceStore()
            first = _whole_import()
            second = _whole_import(
                "rev-2", event_id="event-2", tokens=20, account_id="account-2"
            )
            store.commit_import({"package": first})
            store.commit_import(
                {"package": second, "replace": True, "expected_active_revision": "rev-1"}
            )

            active = lambda: store.import_status("adapter.example")["source"]["active_revision"]
            self.assertEqual(active(), "rev-2")

            # Re-committing the active package stays a true no-op.
            repeated = store.commit_import({"package": second})
            self.assertTrue(repeated["idempotent"])
            self.assertEqual(active(), "rev-2")

            # A retained-but-inactive revision is a change, so it needs the same
            # two guards a brand-new revision needs.
            with self.assertRaisesRegex(ValueError, "replace=true"):
                store.commit_import({"package": first})
            with self.assertRaisesRegex(ValueError, "does not match"):
                store.commit_import(
                    {
                        "package": first,
                        "replace": True,
                        "expected_active_revision": "wrong-revision",
                    }
                )
            self.assertEqual(active(), "rev-2")

            restored = store.commit_import(
                {"package": first, "replace": True, "expected_active_revision": "rev-2"}
            )
            self.assertEqual(restored["active_revision"], "rev-1")
            self.assertFalse(restored["idempotent"])
            self.assertEqual(active(), "rev-1")
            self.assertEqual(
                sorted(
                    row["revision"]
                    for row in store.import_status("adapter.example")["source"]["revisions"]
                ),
                ["rev-1", "rev-2"],
            )
            self.assertEqual([row["account_id"] for row in store.accounts()], ["account-1"])

    def test_already_in_force_is_confirmed_now_and_not_taken_from_the_preflight(self):
        """The last path in commit_import that acted on an unlocked read.

        Committing a package that preflight found both retained and active
        returned persisted=true straight from that preflight. The preflight
        read takes no lock, and these endpoints are served by a threading HTTP
        server, so between the two another writer can move the pointer. The
        caller was then told its package was in force, with active_revision
        naming a revision that no longer was -- while every neighbouring path
        in the same function raises "changed after preflight" for exactly this.

        `_prepare_import` is patched to hand back the earlier preflight
        verbatim: that is what it would have returned had the other commit
        landed a moment later.
        """
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            store = UserSourceStore()
            first = _whole_import()
            second = _whole_import(
                "rev-2", event_id="event-2", tokens=20, account_id="account-2"
            )
            store.commit_import({"package": first})
            stale = store._prepare_import(first)
            self.assertEqual(stale[1]["action"], "idempotent")
            self.assertEqual(stale[1]["active_revision"], "rev-1")

            store.commit_import(
                {"package": second, "replace": True, "expected_active_revision": "rev-1"}
            )

            with patch.object(UserSourceStore, "_prepare_import", lambda self, package: stale):
                with self.assertRaisesRegex(ValueError, "changed after preflight"):
                    store.commit_import({"package": first})

            self.assertEqual(
                store.import_status("adapter.example")["source"]["active_revision"],
                "rev-2",
                "拒绝之后活动指针不该被动过",
            )

    def test_whole_import_replace_and_rollback_switch_every_view_together(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            store = UserSourceStore()
            first = _whole_import()
            second = _whole_import(
                "rev-2", event_id="event-2", tokens=20, account_id="account-2"
            )
            store.commit_import({"package": first})
            with self.assertRaisesRegex(ValueError, "replace=true"):
                store.commit_import({"package": second})
            with self.assertRaisesRegex(ValueError, "does not match"):
                store.commit_import(
                    {
                        "package": second,
                        "replace": True,
                        "expected_active_revision": "wrong-revision",
                    }
                )
            replaced = store.commit_import(
                {
                    "package": second,
                    "replace": True,
                    "expected_active_revision": "rev-1",
                }
            )

            self.assertEqual(replaced["active_revision"], "rev-2")
            self.assertEqual([row["account_id"] for row in store.accounts()], ["account-2"])
            self.assertEqual([row["account_id"] for row in store.quotas()], ["account-2"])
            self.assertEqual(
                [row["usage"]["total_tokens"] for row in TelemetryStore().records()], [20]
            )

            rolled_back = store.rollback_import(
                {
                    "source": "adapter.example",
                    "target_revision": "rev-1",
                    "expected_active_revision": "rev-2",
                }
            )
            self.assertEqual(rolled_back["active_revision"], "rev-1")
            self.assertEqual([row["account_id"] for row in store.accounts()], ["account-1"])
            self.assertEqual([row["account_id"] for row in store.quotas()], ["account-1"])
            self.assertEqual(
                [row["usage"]["total_tokens"] for row in TelemetryStore().records()], [10]
            )
            self.assertEqual(len(store.import_status("adapter.example")["source"]["revisions"]), 2)

    def test_whole_import_rejects_partial_or_mismeasured_package_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            store = UserSourceStore()
            wrong_manifest = _whole_import()
            wrong_manifest["expected"]["records"] = 2
            result = store.preflight_import(wrong_manifest)
            self.assertFalse(result["ready_to_commit"])
            self.assertEqual(result["blockers"][0]["code"], "manifest_mismatch")
            with self.assertRaisesRegex(ValueError, "preflight failed"):
                store.commit_import({"package": wrong_manifest})

            bad_quota = _whole_import()
            bad_quota["quotas"][0]["account_id"] = "not-admitted"
            with self.assertRaisesRegex(ValueError, "requires an account"):
                store.preflight_import(bad_quota)
            self.assertFalse(Path(tmp, "user-sources.sqlite").exists())
            self.assertFalse(Path(tmp, "telemetry.sqlite").exists())

    def test_whole_import_blocks_same_usage_event_under_a_different_source_id(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            store = UserSourceStore()
            first = _whole_import()
            store.commit_import({"package": first})
            disguised = _whole_import(event_id="renamed-event")
            disguised["source"] = "adapter.renamed"

            result = store.preflight_import(disguised)
            self.assertFalse(result["ready_to_commit"])
            self.assertIn(
                "active_user_source_collision",
                {item["code"] for item in result["blockers"]},
            )
            with self.assertRaisesRegex(ValueError, "active_user_source_collision"):
                store.commit_import({"package": disguised})
            self.assertEqual(len(store.import_status()["sources"]), 1)

    def test_whole_import_loopback_endpoints_preflight_commit_and_report_status(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0), partial(Handler, since=None, live_quota=False)
            )
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            package = _whole_import()
            try:
                preflight_request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/v2/user-source-imports/preflight",
                    data=json.dumps(package).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(preflight_request, timeout=3) as response:
                    preflight = json.load(response)
                self.assertTrue(preflight["ready_to_commit"])
                self.assertFalse(Path(tmp, "user-sources.sqlite").exists())

                commit_request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/v2/user-source-imports/commit",
                    data=json.dumps({"package": package}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(commit_request, timeout=3) as response:
                    committed = json.load(response)
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{httpd.server_port}/api/user-source-imports"
                    "?source=adapter.example",
                    timeout=3,
                ) as response:
                    status = json.load(response)

                self.assertTrue(committed["persisted"])
                self.assertEqual(status["source"]["active_revision"], "rev-1")
                self.assertEqual(status["source"]["revisions"][0]["summary"]["total_tokens"], 10)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=3)

    def test_adapter_discovered_accounts_enter_product_list_and_revoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "SANDGLASS_HOME": str(root / "state"),
                "CLAUDE_CONFIG_DIR": str(root / "claude"),
                "CODEX_HOME": str(root / "codex"),
                "GROK_HOME": str(root / "grok"),
            }
            with patch.dict(os.environ, env, clear=False):
                store = UserSourceStore()
                store.append({"source": "identity.timeline", "payload": {"native": True}})
                first = store.append_accounts(
                    {
                        "source": "identity.timeline",
                        "accounts": [
                            {
                                "provider": "claude",
                                "account_id": "claude-account",
                                "email": "same@example.test",
                                "label": "Claude local",
                            },
                            {
                                "provider": "codex",
                                "account_id": "codex-account",
                                "email": "same@example.test",
                                "label": "Codex local",
                            },
                        ],
                    }
                )
                second = store.append_accounts(
                    {
                        "source": "identity.timeline",
                        "accounts": [
                            {
                                "provider": "claude",
                                "account_id": "claude-account",
                                "email": "same@example.test",
                                "label": "Claude local",
                            },
                            {
                                "provider": "codex",
                                "account_id": "codex-account",
                                "email": "same@example.test",
                                "label": "Codex local",
                            },
                        ],
                    }
                )
                quota_result = store.append_quotas(
                    {
                        "source": "identity.timeline",
                        "quotas": [
                            {
                                "provider": "codex",
                                "account_id": "codex-account",
                                "fetched_at": "2026-08-30T00:00:00Z",
                                "plan": "pro",
                                "windows": [
                                    {
                                        "label": "7d",
                                        "used_percent": 25,
                                        "resets_at": "2026-09-06T00:00:00Z",
                                        "window_minutes": 10080,
                                    }
                                ],
                            }
                        ],
                    }
                )

                discovered = load_accounts()
                quota = _quota_payload(live=False)
                with patch("sandglass.serve.collect_all", return_value=[]):
                    revoked = configure_user_source(
                        {"source": "identity.timeline", "accounts_enabled": False}
                    )
                hidden = load_accounts()
                with patch("sandglass.serve.collect_all", return_value=[]):
                    restored = configure_user_source(
                        {"source": "identity.timeline", "accounts_enabled": True}
                    )
                restored_accounts = load_accounts()

            self.assertEqual(first["changed"], 2)
            self.assertEqual(second["unchanged"], 2)
            self.assertEqual(quota_result["changed"], 1)
            self.assertEqual(
                {(row.provider, row.account_id) for row in discovered},
                {("claude", "claude-account"), ("codex", "codex-account")},
            )
            self.assertTrue(
                all(row.extra["account_source"] == "user_adapter:identity.timeline" for row in discovered)
            )
            self.assertTrue(
                all(item["account_source_official"] is False for item in quota["accounts"])
            )
            quota_by_id = {item["account_id"]: item for item in quota["accounts"]}
            self.assertEqual(quota_by_id["codex-account"]["plan"], "pro")
            self.assertEqual(
                quota_by_id["codex-account"]["windows"][0]["used_percent"], 25.0
            )
            self.assertEqual(
                quota_by_id["codex-account"]["quota_source"],
                "user_adapter:identity.timeline",
            )
            self.assertFalse(revoked["setting"]["accounts_enabled"])
            self.assertEqual(hidden, [])
            self.assertTrue(restored["setting"]["accounts_enabled"])
            self.assertEqual(len(restored_accounts), 2)

    def test_user_source_account_manifest_rejects_secret_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = UserSourceStore(Path(tmp) / "user-sources.sqlite")
            with self.assertRaisesRegex(ValueError, "unsupported fields"):
                store.append_accounts(
                    {
                        "source": "identity.timeline",
                        "accounts": [
                            {
                                "provider": "codex",
                                "account_id": "account-1",
                                "access_token": "must-not-enter-product-state",
                            }
                        ],
                    }
                )
            store.append_accounts(
                {
                    "source": "identity.timeline",
                    "accounts": [{"provider": "codex", "account_id": "account-1"}],
                }
            )
            with self.assertRaisesRegex(ValueError, "unsupported fields"):
                store.append_quotas(
                    {
                        "source": "identity.timeline",
                        "quotas": [
                            {
                                "provider": "codex",
                                "account_id": "account-1",
                                "fetched_at": "2026-08-30T00:00:00Z",
                                "windows": [],
                                "refresh_token": "must-not-enter-product-state",
                            }
                        ],
                    }
                )

    def test_official_account_wins_over_matching_user_source_account(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            store = UserSourceStore()
            store.append({"source": "identity.timeline", "payload": {"native": True}})
            store.append_accounts(
                {
                    "source": "identity.timeline",
                    "accounts": [
                        {
                            "provider": "claude",
                            "account_id": "same-account",
                            "label": "Adapter label",
                        }
                    ],
                }
            )
            official = Account(
                provider="claude",
                account_id="same-account",
                label="Official label",
                active=True,
                extra={
                    "account_source": "official_claude_config",
                    "account_source_official": True,
                },
            )
            with patch(
                "sandglass.accounts.load_claude_accounts", return_value=[official]
            ), patch("sandglass.accounts.load_codex_accounts", return_value=[]), patch(
                "sandglass.accounts.load_grok_accounts", return_value=[]
            ):
                accounts = load_accounts()

            self.assertEqual(len(accounts), 1)
            self.assertEqual(accounts[0].label, "Official label")
            self.assertIs(accounts[0].extra["account_source_official"], True)
            self.assertEqual(
                accounts[0].extra["supplemental_account_sources"],
                ["user_adapter:identity.timeline"],
            )

    def test_unknown_shape_is_preserved_and_requires_display_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = UserSourceStore(Path(tmp) / "user-sources.sqlite")
            payload = {
                "provider": "example",
                "monthly_invoice_total": 42,
                "native_nested": {"evidence": [1, 2, 3]},
            }

            result = store.append({"source": "example.tool", "payload": payload})
            duplicate = store.append({"source": "example.tool", "payload": payload})
            mirror = store.mirror()

            self.assertTrue(result["inserted"])
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(store.receipts()[0]["payload"], payload)
            self.assertEqual(mirror["control_model"], "per_source")
            self.assertEqual(mirror["sources"][0]["product_use_stage"], "stored_only")
            self.assertEqual(mirror["sources"][0]["recognized_fields"], ["provider"])
            self.assertEqual(
                mirror["sources"][0]["unknown_fields"],
                ["monthly_invoice_total", "native_nested"],
            )

    def test_loopback_receiver_accepts_json_without_requiring_known_fields(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0), partial(OtlpHandler, since=None, live_quota=False)
            )
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                body = json.dumps(
                    {"source": "unknown.tool", "payload": {"strange": "kept"}}
                ).encode("utf-8")
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/v1/user-sources",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=3) as response:
                    result = json.load(response)
                self.assertTrue(result["accepted"])
                self.assertEqual(result["recognized_fields"], [])
                self.assertEqual(result["unknown_fields"], ["strange"])
                self.assertEqual(result["product_use_stage"], "stored_only")
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=3)

    def test_normalized_json_endpoint_requires_raw_receipt_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0), partial(OtlpHandler, since=None, live_quota=False)
            )
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            record_body = json.dumps(
                {
                    "source": "multi.identity",
                    "records": [
                        {
                            "provider": "codex",
                            "event_id": "event-1",
                            "timestamp": "2026-08-30T00:00:00Z",
                            "account_id": "account-1",
                            "session_id": "session-1",
                            "input_tokens": 7,
                            "output_tokens": 3,
                            "reasoning_tokens": 1,
                            "total_tokens": 10,
                            "calls": 1,
                        }
                    ],
                }
            ).encode("utf-8")
            try:
                missing = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/v1/user-sources/records",
                    data=record_body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    urllib.request.urlopen(missing, timeout=3)
                self.assertEqual(rejected.exception.code, 400)

                UserSourceStore().append(
                    {"source": "multi.identity", "payload": {"native": True}}
                )
                account_request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/v1/user-sources/accounts",
                    data=json.dumps(
                        {
                            "source": "multi.identity",
                            "accounts": [
                                {
                                    "provider": "codex",
                                    "account_id": "account-1",
                                    "label": "Adapter account",
                                }
                            ],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(account_request, timeout=3) as response:
                    accounts_result = json.load(response)
                quota_request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/v1/user-sources/quotas",
                    data=json.dumps(
                        {
                            "source": "multi.identity",
                            "quotas": [
                                {
                                    "provider": "codex",
                                    "account_id": "account-1",
                                    "fetched_at": "2026-08-30T00:00:00Z",
                                    "windows": [
                                        {"label": "7d", "used_percent": 40}
                                    ],
                                }
                            ],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(quota_request, timeout=3) as response:
                    quota_result = json.load(response)
                with urllib.request.urlopen(missing, timeout=3) as response:
                    first = json.load(response)
                with urllib.request.urlopen(missing, timeout=3) as response:
                    second = json.load(response)

                self.assertEqual(first["inserted"], 1)
                self.assertEqual(second["duplicates"], 1)
                self.assertEqual(accounts_result["changed"], 1)
                self.assertEqual(quota_result["changed"], 1)
                self.assertEqual(len(TelemetryStore().records()), 1)
                self.assertEqual(len(UserSourceStore().accounts()), 1)
                self.assertEqual(len(UserSourceStore().quotas()), 1)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=3)

    def test_declared_record_identity_control_is_reversible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "SANDGLASS_HOME": str(root / "state"),
                "CLAUDE_CONFIG_DIR": str(root / "claude"),
                "CODEX_HOME": str(root / "codex"),
                "GROK_HOME": str(root / "grok"),
            }
            with patch.dict(os.environ, env, clear=False):
                self._assert_declared_record_identity_control_is_reversible()

    def _assert_declared_record_identity_control_is_reversible(self):
        UserSourceStore().append(
            {"source": "multi.identity", "payload": {"native": True}}
        )
        UserSourceStore().append_accounts(
            {
                "source": "multi.identity",
                "accounts": [
                    {
                        "provider": "codex",
                        "account_id": "account-1",
                        "label": "one",
                    }
                ],
            }
        )
        usage = TokenUsage(input_tokens=7, output_tokens=3, reasoning_tokens=1, calls=1)
        TelemetryStore().append(
            [
                TelemetryRecord(
                    event_key="event-1",
                    provider="codex",
                    event_name="sandglass.usage",
                    event_at="2026-08-30T00:00:00Z",
                    received_at="2026-08-30T00:00:01Z",
                    account_id="account-1",
                    session_id="session-1",
                    model="",
                    source="user_adapter:multi.identity",
                    source_version="1",
                    schema_version="1",
                    evidence_grade="U-A",
                    coverage_state="user_attributed",
                    usage=usage,
                )
            ]
        )
        official = SessionRecord(
            provider="codex",
            session_id="session-1",
            path="official.jsonl",
            usage=usage,
            timeline=[("2026-08-30T00:00:00Z", usage)],
        )
        with patch("sandglass.serve.collect_all", return_value=[official]):
            enabled = configure_user_source(
                {"source": "multi.identity", "identity_enabled": True}
            )
            payload = api_payload("/api/user-sources", live_quota=False)
            with self.assertRaisesRegex(ValueError, "choose verified"):
                configure_user_source(
                    {
                        "source": "multi.identity",
                        "mapped_provider": "codex",
                        "mapped_account_id": "account-1",
                    }
                )
            revoked = configure_user_source(
                {"source": "multi.identity", "accounts_enabled": False}
            )
            restored = configure_user_source(
                {"source": "multi.identity", "accounts_enabled": True}
            )

        self.assertTrue(enabled["setting"]["identity_enabled"])
        self.assertEqual(enabled["product_use_stage"], "account_mapped")
        self.assertTrue(
            payload["inbox"]["sources"][0]["stages"]["account_mapping"][
                "declared_identity_enabled"
            ]
        )
        self.assertEqual(payload["candidate_ledger"]["identity_supplement_minutes"], 1)
        self.assertFalse(revoked["setting"]["accounts_enabled"])
        self.assertFalse(revoked["setting"]["identity_enabled"])
        self.assertFalse(revoked["setting"]["full_window_enabled"])
        self.assertTrue(restored["setting"]["accounts_enabled"])
        self.assertFalse(restored["setting"]["identity_enabled"])

    def test_official_minute_api_pages_prompt_free_records(self):
        usage = TokenUsage(input_tokens=7, output_tokens=3, reasoning_tokens=1, calls=1)
        session = SessionRecord(
            provider="codex",
            session_id="session-1",
            path="private-path.jsonl",
            title="private prompt",
            usage=usage,
            timeline=[
                ("2026-08-30T00:00:00Z", usage),
                ("2026-08-30T00:01:00Z", usage),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ), patch("sandglass.serve.collect_all", return_value=[session]):
            page = api_payload(
                "/api/user-sources/official-minutes?provider=codex&offset=0&limit=1",
                live_quota=False,
            )

            self.assertEqual(page["authority"], "official_builtin")
            self.assertEqual(page["total"], 2)
            self.assertEqual(page["next_offset"], 1)
            self.assertEqual(page["records"][0]["total_tokens"], 10)
            self.assertNotIn("path", page["records"][0])
            self.assertNotIn("title", page["records"][0])

            with self.assertRaisesRegex(ValueError, "offset must be non-negative"):
                api_payload(
                    "/api/user-sources/official-minutes?provider=codex&offset=-1",
                    live_quota=False,
                )
            with self.assertRaisesRegex(ValueError, "limit must be between"):
                api_payload(
                    "/api/user-sources/official-minutes?provider=codex&limit=5001",
                    live_quota=False,
                )

    def test_mirror_api_keeps_raw_inbox_and_normalized_shadow_separate(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ), patch("sandglass.serve.collect_all", return_value=[]) as collect:
            UserSourceStore().append(
                {"source": "daily.export", "payload": {"daily_total": 99}}
            )

            result = api_payload("/api/user-sources", live_quota=False)

            self.assertEqual(result["inbox"]["sources"][0]["source"], "daily.export")
            self.assertEqual(result["inbox"]["recent_receipts"][0]["payload"], {"daily_total": 99})
            self.assertEqual(result["inbox"]["control_model"], "per_source")
            self.assertEqual(
                result["inbox"]["sources"][0]["product_use_stage"], "stored_only"
            )
            self.assertTrue(result["normalized_shadow"]["shadow_only"])
            self.assertFalse(result["normalized_shadow"]["included_in_report"])
            self.assertTrue(result["candidate_ledger"]["candidate_only"])
            self.assertFalse(result["candidate_ledger"]["included_in_report"])
            collect.assert_not_called()

    def test_display_and_account_mapping_choices_are_reversible(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = UserSourceStore(Path(tmp) / "user-sources.sqlite")
            store.append({"source": "example.tool", "payload": {"native": 1}})

            hidden = store.configure("example.tool", display_enabled=False)
            mapped = store.configure(
                "example.tool",
                mapped_provider="codex",
                mapped_account_id="account-1",
            )
            cleared = store.configure(
                "example.tool", mapped_provider="", mapped_account_id=""
            )

            self.assertFalse(hidden["display_enabled"])
            self.assertEqual(mapped["mapped_account_id"], "account-1")
            self.assertEqual(cleared["mapped_account_id"], "")
            row = store.mirror()["sources"][0]
            self.assertFalse(row["settings"]["display_enabled"])
            self.assertEqual(row["product_use_stage"], "stored_only")

    def test_mirror_api_attaches_candidate_accounting_to_normalized_only_source(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ), patch("sandglass.serve.collect_all", return_value=[]):
            usage = TokenUsage(input_tokens=7, output_tokens=3, calls=1)
            TelemetryStore().append(
                [
                    TelemetryRecord(
                        event_key="event-1",
                        provider="openrouter",
                        event_name="sandglass.usage",
                        event_at="2026-08-30T00:00:00Z",
                        received_at="2026-08-30T00:00:01Z",
                        account_id="",
                        session_id="session-1",
                        model="",
                        source="user_adapter:example.normalized",
                        source_version="1",
                        schema_version="1",
                        evidence_grade="U-B",
                        coverage_state="user_unassigned_missing_identity",
                        usage=usage,
                    )
                ]
            )

            result = api_payload("/api/user-sources", live_quota=False)

            source = result["inbox"]["sources"][0]
            self.assertEqual(source["state"], "normalized_only")
            self.assertEqual(source["candidate_accounting"]["token_candidate_minutes"], 1)
            self.assertEqual(source["stages"]["totals"]["candidate_tokens"], 10)
            self.assertEqual(result["candidate_ledger"]["token_candidates"], 1)
            self.assertTrue(result["candidate_ledger"]["admission_ready"])
            self.assertTrue(source["stages"]["totals"]["available"])
            self.assertFalse(source["stages"]["totals"]["enabled"])

    def test_totals_choice_is_reversible_and_old_settings_migrate_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "user-sources.sqlite"
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE user_source_settings (
                    source TEXT PRIMARY KEY,
                    display_enabled INTEGER NOT NULL,
                    mapped_provider TEXT NOT NULL,
                    mapped_account_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO user_source_settings VALUES ('old.source', 1, '', '', 'old');
                """
            )
            conn.commit()
            conn.close()
            store = UserSourceStore(path)

            self.assertFalse(store.settings()["old.source"]["totals_enabled"])
            self.assertFalse(store.settings()["old.source"]["full_window_enabled"])
            enabled = store.configure("old.source", totals_enabled=True)
            window_enabled = store.configure(
                "old.source", full_window_enabled=True
            )
            window_disabled = store.configure(
                "old.source", full_window_enabled=False
            )
            disabled = store.configure("old.source", totals_enabled=False)

            self.assertTrue(enabled["totals_enabled"])
            self.assertTrue(window_enabled["full_window_enabled"])
            self.assertFalse(window_disabled["full_window_enabled"])
            self.assertFalse(disabled["totals_enabled"])
            self.assertFalse(store.settings()["old.source"]["totals_enabled"])

    def test_configuration_endpoint_works_when_otlp_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            UserSourceStore().append(
                {"source": "example.tool", "payload": {"native": 1}}
            )
            httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                partial(Handler, since=None, live_quota=False, allow_otlp=False),
            )
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                body = json.dumps(
                    {"source": "example.tool", "display_enabled": False}
                ).encode("utf-8")
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/api/user-sources/configure",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=3) as response:
                    result = json.load(response)
                self.assertTrue(result["ok"])
                self.assertFalse(result["setting"]["display_enabled"])
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=3)

    def test_totals_configuration_requires_a_candidate_and_is_reversible(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ), patch("sandglass.serve.collect_all", return_value=[]):
            usage = TokenUsage(input_tokens=7, output_tokens=3, calls=1)
            TelemetryStore().append(
                [
                    TelemetryRecord(
                        event_key="config-token-1",
                        provider="openrouter",
                        event_name="sandglass.usage",
                        event_at="2026-08-30T00:00:00Z",
                        received_at="2026-08-30T00:00:01Z",
                        account_id="",
                        session_id="session-1",
                        model="",
                        source="user_adapter:example.config",
                        source_version="1",
                        schema_version="1",
                        evidence_grade="U-B",
                        coverage_state="user_unassigned_missing_identity",
                        usage=usage,
                    )
                ]
            )

            enabled = configure_user_source(
                {"source": "example.config", "totals_enabled": True}
            )
            account = Account(
                provider="openrouter", account_id="account-1", label="one"
            )
            with patch("sandglass.serve.load_accounts", return_value=[account]):
                configure_user_source(
                    {
                        "source": "example.config",
                        "mapped_provider": "openrouter",
                        "mapped_account_id": "account-1",
                    }
                )
                window_enabled = configure_user_source(
                    {"source": "example.config", "full_window_enabled": True}
                )
                payload = api_payload("/api/user-sources", live_quota=False)
                window_disabled = configure_user_source(
                    {"source": "example.config", "full_window_enabled": False}
                )
                configure_user_source(
                    {"source": "example.config", "full_window_enabled": True}
                )
                disabled = configure_user_source(
                    {"source": "example.config", "totals_enabled": False}
                )

            self.assertEqual(enabled["product_use_stage"], "totals_included")
            self.assertTrue(enabled["setting"]["totals_enabled"])
            self.assertEqual(
                window_enabled["product_use_stage"], "full_window_enabled"
            )
            self.assertTrue(window_enabled["setting"]["full_window_enabled"])
            self.assertTrue(
                payload["inbox"]["sources"][0]["stages"]["full_window"]["enabled"]
            )
            self.assertFalse(window_disabled["setting"]["full_window_enabled"])
            self.assertFalse(disabled["setting"]["totals_enabled"])
            self.assertFalse(disabled["setting"]["full_window_enabled"])
            UserSourceStore().append(
                {"source": "empty.config", "payload": {"native": 1}}
            )
            with self.assertRaisesRegex(ValueError, "no unambiguous"):
                configure_user_source(
                    {"source": "empty.config", "totals_enabled": True}
                )

    def test_account_mapping_accepts_only_a_currently_discovered_account(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            UserSourceStore().append(
                {"source": "example.tool", "payload": {"native": 1}}
            )
            account = Account(provider="codex", account_id="account-1", label="one")
            with patch("sandglass.serve.load_accounts", return_value=[account]):
                result = configure_user_source(
                    {
                        "source": "example.tool",
                        "mapped_provider": "codex",
                        "mapped_account_id": "account-1",
                    }
                )
                self.assertEqual(result["product_use_stage"], "account_mapped")
                with self.assertRaisesRegex(ValueError, "not currently discoverable"):
                    configure_user_source(
                        {
                            "source": "example.tool",
                            "mapped_provider": "codex",
                            "mapped_account_id": "missing",
                        }
                    )

    def test_filling_user_source_store_does_not_change_report_accounting(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            account = Account(
                provider="codex",
                account_id="account-1",
                label="account-1",
                active=True,
                extra={
                    "windows": [
                        {
                            "label": "5h",
                            "used_percent": 50.0,
                            "window_start": "2026-08-30T00:00:00Z",
                            "resets_at": "2026-08-30T05:00:00Z",
                        }
                    ]
                },
            )
            usage = TokenUsage(input_tokens=80, output_tokens=20, calls=1)
            session = SessionRecord(
                provider="codex",
                session_id="session-1",
                path="official-rollout.jsonl",
                account_id="account-1",
                account_label="account-1",
                started_at="2026-08-30T01:00:00Z",
                ended_at="2026-08-30T01:00:00Z",
                usage=usage,
                daily={"2026-08-30": usage},
                timeline=[("2026-08-30T01:00:00Z", usage)],
            )

            with patch("sandglass.report.switch_runs_for", return_value=[]):
                before = _accounting_snapshot(
                    build_report(
                        [session],
                        accounts=[account],
                        live_quota=False,
                        attribution_mode="single_official",
                    )
                )
                UserSourceStore().append(
                    {
                        "source": "looks.mergeable",
                        "payload": {
                            "provider": "codex",
                            "account_id": "account-1",
                            "session_id": "session-1",
                            "timestamp": "2026-08-30T01:00:00Z",
                            "input_tokens": 800000,
                            "output_tokens": 200000,
                            "total_tokens": 1000000,
                            "calls": 1,
                        },
                    }
                )
                after = _accounting_snapshot(
                    build_report(
                        [session],
                        accounts=[account],
                        live_quota=False,
                        attribution_mode="single_official",
                    )
                )

            self.assertEqual(after, before)
            self.assertEqual(after["totals"]["usage"]["total_tokens"], 100)
            self.assertEqual(len(after["accounts"]), 1)
            self.assertEqual(
                after["accounts"][0]["local_in_windows"][0]["usage"]["total_tokens"],
                100,
            )

    def test_usage_accounting_modules_do_not_import_user_source_store(self):
        package = Path(__file__).resolve().parents[1] / "sandglass"
        for name in ("report.py", "collectors.py", "models.py", "attribution.py"):
            path = package / name
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append(node.module or "")
            self.assertNotIn("sandglass.user_sources", imports, name)

        accounts_path = package / "accounts.py"
        accounts_tree = ast.parse(
            accounts_path.read_text(encoding="utf-8"), filename=str(accounts_path)
        )
        account_imports = []
        for node in ast.walk(accounts_tree):
            if isinstance(node, ast.Import):
                account_imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                account_imports.append(node.module or "")
        self.assertIn("sandglass.user_sources", account_imports)

    def test_user_identity_cache_stamp_changes_with_receipts_and_settings(self):
        from sandglass.serve import _user_identity_source_stamp

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}, clear=False
        ):
            before = _user_identity_source_stamp()
            store = UserSourceStore()
            store.append({"source": "example.identity", "payload": {"native": 1}})
            after_receipt = _user_identity_source_stamp()
            store.configure(
                "example.identity",
                mapped_provider="claude",
                mapped_account_id="account-1",
            )
            after_setting = _user_identity_source_stamp()

            self.assertNotEqual(after_receipt, before)
            self.assertNotEqual(after_setting, after_receipt)


if __name__ == "__main__":
    unittest.main()
