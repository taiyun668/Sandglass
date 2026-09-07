import json
import hashlib
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from sandglass.models import Account, SessionRecord, TokenUsage


class _NoopCache:
    def close(self):
        return None


class IdentityReloadHttpTests(unittest.TestCase):
    def test_v2_diagnostics_semantically_sorts_mixed_utc_spellings_and_keeps_gap(self):
        from sandglass import serve

        events = [
            {"at": "2026-09-04T23:45:00Z", "kind": "observed", "account_id": "old"},
            {
                "at": "2026-09-04T23:45:00.250000+00:00",
                "kind": "observed",
                "account_id": "new",
            },
            {
                "at": "2026-09-04T23:45:01Z",
                "kind": "unassigned",
                "reason": "observer_coverage_gap",
            },
            {
                "at": "2026-09-04T23:45:02.000000+00:00",
                "kind": "observed",
                "account_id": "new",
            },
        ]
        runs = [(event["at"], event.get("account_id", "")) for event in events]
        with tempfile.TemporaryDirectory() as tmp, patch(
            "sandglass.accounts.claude_identity_runs", return_value=[]
        ), patch(
            "sandglass.accounts.codex_identity_runs", return_value=runs
        ), patch(
            "sandglass.accounts.grok_identity_runs", return_value=[]
        ), patch(
            "sandglass.accounts.identity_source_stamp", return_value=()
        ), patch(
            "sandglass.paths.meter_home", return_value=Path(tmp)
        ), patch.object(serve, "_user_identity_source_stamp", return_value=()):
            ledger = Path(tmp) / "codex-official-identity-events-v2.json"
            ledger.write_text(
                json.dumps({"schema": 2, "events": events}), encoding="utf-8"
            )
            old_cache = serve._local_cache
            serve._local_cache = {
                "at": 0.0,
                "payload": {"accounts": []},
                "identity_stamp": (),
            }
            try:
                diagnostics = serve._attribution_diagnostics()["identity_runs"]["codex"]
            finally:
                serve._local_cache = old_cache

        expected = hashlib.sha256(
            json.dumps(runs, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertTrue(diagnostics["matches_disk"], diagnostics)
        self.assertEqual(diagnostics["count"], 4)
        self.assertEqual(diagnostics["first_at"], events[0]["at"])
        self.assertEqual(diagnostics["last_at"], events[-1]["at"])
        self.assertEqual(diagnostics["sha256"], expected)

    def test_v2_diagnostics_collapses_repeated_owner_rows_without_crossing_gap(self):
        from sandglass import serve

        events = [
            {"at": "2026-09-04T23:00:00Z", "kind": "observed", "account_id": "same"},
            {"at": "2026-09-04T23:05:00+00:00", "kind": "observed", "account_id": "same"},
            {
                "at": "2026-09-04T23:10:00.000000Z",
                "kind": "unassigned",
                "reason": "observer_stopped",
            },
            {"at": "2026-09-04T23:15:00Z", "kind": "observed", "account_id": "same"},
        ]
        runs = [(event["at"], event.get("account_id", "")) for event in events]
        with tempfile.TemporaryDirectory() as tmp, patch(
            "sandglass.accounts.claude_identity_runs", return_value=[]
        ), patch(
            "sandglass.accounts.codex_identity_runs", return_value=runs
        ), patch(
            "sandglass.accounts.grok_identity_runs", return_value=[]
        ), patch(
            "sandglass.accounts.identity_source_stamp", return_value=()
        ), patch(
            "sandglass.paths.meter_home", return_value=Path(tmp)
        ), patch.object(serve, "_user_identity_source_stamp", return_value=()):
            ledger = Path(tmp) / "codex-official-identity-events-v2.json"
            ledger.write_text(
                json.dumps({"schema": 2, "events": events}), encoding="utf-8"
            )
            old_cache = serve._local_cache
            serve._local_cache = {"at": 0.0, "payload": {"accounts": []}, "identity_stamp": ()}
            try:
                diagnostics = serve._attribution_diagnostics()["identity_runs"]["codex"]
            finally:
                serve._local_cache = old_cache

        projected = [runs[0], runs[2], runs[3]]
        expected = hashlib.sha256(
            json.dumps(projected, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertTrue(diagnostics["matches_disk"], diagnostics)
        self.assertEqual(diagnostics["count"], 3)
        self.assertEqual(diagnostics["sha256"], expected)

    def test_v2_disk_diagnostics_marks_malformed_timestamp_unreadable(self):
        from sandglass import serve

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "codex-official-identity-events-v2.json"
            path.write_text(
                json.dumps({
                    "schema": 2,
                    "events": [{
                        "at": "not-a-timestamp",
                        "kind": "observed",
                        "account_id": "account",
                    }],
                }),
                encoding="utf-8",
            )
            evidence = serve._disk_run_evidence(path, "account_id")

        self.assertFalse(evidence["readable"])
        self.assertEqual(evidence["count"], 0)

    def test_legacy_disk_diagnostics_rejects_mixed_valid_and_invalid_rows(self):
        from sandglass import serve

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "grok-official-identity-runs.json"
            path.write_text(
                json.dumps([
                    {"at": "2026-09-04T23:00:00Z", "account_id": "valid"},
                    {"at": "2026-09-04T23:05:00Z"},
                ]),
                encoding="utf-8",
            )
            evidence = serve._disk_run_evidence(path, "account_id")

        self.assertFalse(evidence["readable"])
        self.assertEqual(evidence["count"], 0)

    def test_attribution_diagnostics_exposes_only_non_identifying_run_evidence(self):
        from sandglass import serve

        codex_runs = [("2026-08-30T01:00:00+00:00", "private@example.com")]
        grok_runs = [("2026-08-30T02:00:00+00:00", "private-grok-id")]
        with tempfile.TemporaryDirectory() as tmp, patch(
            "sandglass.accounts.codex_identity_runs", return_value=codex_runs
        ), patch(
            "sandglass.accounts.grok_identity_runs", return_value=grok_runs
        ), patch(
            "sandglass.accounts.identity_source_stamp", return_value=((1, 2), (3, 4))
        ), patch(
            "sandglass.paths.meter_home", return_value=Path(tmp)
        ):
            meter = Path(tmp)
            (meter / "codex-official-identity-events-v2.json").write_text(
                json.dumps({
                    "schema": 2,
                    "events": [{
                        "at": codex_runs[0][0],
                        "kind": "observed",
                        "account_id": codex_runs[0][1],
                    }],
                }),
                encoding="utf-8",
            )
            (meter / "grok-official-identity-runs.json").write_text(
                json.dumps([{"at": grok_runs[0][0], "account_id": grok_runs[0][1]}]),
                encoding="utf-8",
            )
            old_cache = serve._local_cache
            serve._local_cache = {
                "at": 0.0,
                "payload": {"accounts": []},
                "identity_stamp": ((1, 2), (3, 4)),
            }
            try:
                payload = serve._attribution_diagnostics()
                codex_file_sha = hashlib.sha256(
                    (meter / "codex-official-identity-events-v2.json").read_bytes()
                ).hexdigest()
            finally:
                serve._local_cache = old_cache

        encoded = json.dumps(payload)
        self.assertNotIn("private@example.com", encoded)
        self.assertNotIn("private-grok-id", encoded)
        self.assertEqual(payload["identity_runs"]["codex"]["count"], 1)
        self.assertTrue(payload["identity_runs"]["codex"]["matches_disk"])
        self.assertTrue(payload["identity_runs"]["grok"]["matches_disk"])
        self.assertTrue(payload["identity_runs"]["codex"]["disk"]["stable_snapshot"])
        self.assertEqual(
            payload["identity_runs"]["codex"]["disk"]["file_sha256"],
            codex_file_sha,
        )
        self.assertTrue(payload["identity_runs"]["codex"]["disk"]["captured_at"])
        self.assertFalse(
            payload["identity_runs"]["codex"]["disk"]["last_write_matches_file"]
        )
        self.assertEqual(
            payload["identity_runs"]["codex"]["sha256"],
            hashlib.sha256(
                json.dumps(codex_runs, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        )
        self.assertTrue(
            payload["local_windows_cache"]["identity_stamp_matches_disk"]
        )

    def test_live_http_cache_reloads_identity_ledger_written_by_another_process(self):
        """A running panel must see a switch without waiting for its TTL or restart."""
        from sandglass import accounts, serve

        now = datetime.now(timezone.utc)
        minute = now - timedelta(minutes=30)
        window = {
            "label": "7d",
            "used_percent": 10,
            "window_minutes": 7 * 24 * 60,
            "resets_at": (now + timedelta(days=1)).isoformat(),
        }
        old = Account(
            provider="codex",
            account_id="old",
            email="old@example.com",
            extra={"windows": [window]},
        )
        new = Account(
            provider="codex",
            account_id="new",
            email="new@example.com",
            extra={"windows": [window]},
        )
        session = SessionRecord(
            provider="codex",
            session_id="switch-test",
            path="switch-test.jsonl",
            account_id="old",
            timeline=[(minute.isoformat(), TokenUsage(input_tokens=123, calls=1))],
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meter = root / "meter"
            meter.mkdir()
            ledger = meter / "codex-official-identity-events-v2.json"
            ledger.write_text(
                json.dumps(
                    {"schema": 2, "events": [{
                        "at": (now - timedelta(hours=2)).isoformat(),
                        "kind": "observed",
                        "account_id": old.email,
                    }]},
                    indent=2,
                ),
                encoding="utf-8",
            )

            with patch.object(accounts, "meter_home", lambda: meter), patch.object(
                accounts, "codex_home", lambda: root / "codex"
            ), patch.object(accounts, "grok_home", lambda: root / "grok"), patch.object(
                serve, "load_accounts", lambda: [old, new]
            ), patch.object(serve, "collect_all", lambda cache=None: [session]), patch.object(
                serve, "SessionCache", _NoopCache
            ), patch.object(
                serve, "quota_anchor", lambda provider, account_id, label: ("", "period", False)
            ):
                accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
                serve._local_cache = {"at": 0.0, "payload": None, "identity_stamp": None}
                httpd = ThreadingHTTPServer(
                    ("127.0.0.1", 0),
                    partial(serve.Handler, since=None, live_quota=False),
                )
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                url = f"http://127.0.0.1:{httpd.server_port}/api/local-windows"
                try:
                    before = self._usage_by_account(url)
                    self.assertEqual(before, {"old": 123, "new": 0})

                    subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            (
                                "import json,sys; from pathlib import Path; "
                               "p=Path(sys.argv[1]); payload=json.loads(p.read_text(encoding='utf-8')); "
                               "payload['events'].append({'at':sys.argv[2],'kind':'observed','account_id':sys.argv[3]}); "
                               "p.write_text(json.dumps(payload,indent=2),encoding='utf-8')"
                            ),
                            str(ledger),
                            (now - timedelta(hours=1)).isoformat(),
                            new.email,
                        ],
                        check=True,
                    )

                    after = self._usage_by_account(url)
                    self.assertEqual(after, {"old": 0, "new": 123})
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(timeout=5)
                    accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
                    serve._local_cache = {"at": 0.0, "payload": None, "identity_stamp": None}

    def test_running_panel_recovers_earlier_history_after_atomic_ledger_replacement(self):
        """A partial/malformed disk view must not erase live history or require restart."""
        from sandglass import accounts, serve

        now = datetime.now(timezone.utc)
        old_minute = now - timedelta(minutes=90)
        new_minute = now - timedelta(minutes=30)
        window = {
            "label": "7d",
            "used_percent": 10,
            "window_minutes": 7 * 24 * 60,
            "resets_at": (now + timedelta(days=1)).isoformat(),
        }
        old = Account(
            provider="codex",
            account_id="old",
            email="old@example.com",
            extra={"windows": [window]},
        )
        new = Account(
            provider="codex",
            account_id="new",
            email="new@example.com",
            extra={"windows": [window]},
        )
        session = SessionRecord(
            provider="codex",
            session_id="long-lived-history",
            path="long-lived-history.jsonl",
            timeline=[
                (old_minute.isoformat(), TokenUsage(input_tokens=111, calls=1)),
                (new_minute.isoformat(), TokenUsage(input_tokens=222, calls=1)),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meter = root / "meter"
            meter.mkdir()
            ledger = meter / "codex-official-identity-events-v2.json"
            ledger.write_text(
                json.dumps(
                    {"schema": 2, "events": [{
                        "at": (now - timedelta(hours=1)).isoformat(),
                        "kind": "observed",
                        "account_id": new.email,
                    }]},
                    indent=2,
                ),
                encoding="utf-8",
            )

            with patch.object(accounts, "meter_home", lambda: meter), patch(
                "sandglass.paths.meter_home", lambda: meter
            ), patch.object(
                accounts, "codex_home", lambda: root / "codex"
            ), patch.object(accounts, "grok_home", lambda: root / "grok"), patch.object(
                serve, "load_accounts", lambda: [old, new]
            ), patch.object(serve, "collect_all", lambda cache=None: [session]), patch.object(
                serve, "SessionCache", _NoopCache
            ), patch.object(
                serve, "quota_anchor", lambda provider, account_id, label: ("", "period", False)
            ):
                accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
                serve._local_cache = {
                    "at": 0.0,
                    "payload": None,
                    "identity_stamp": None,
                    "source_stamp": None,
                }
                httpd = ThreadingHTTPServer(
                    ("127.0.0.1", 0),
                    partial(serve.Handler, since=None, live_quota=False),
                )
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                base = f"http://127.0.0.1:{httpd.server_port}"
                try:
                    # The running process initially sees only the later takeover;
                    # the earlier directly measured event remains unassigned.
                    self.assertEqual(
                        self._usage_by_account(base + "/api/local-windows"),
                        {"old": 0, "new": 222},
                    )

                    # A torn/corrupt external write must not turn the cached
                    # authoritative history into an empty timeline.
                    ledger.write_text('[{"at":', encoding="utf-8")
                    self.assertEqual(
                        self._usage_by_account(base + "/api/local-windows"),
                        {"old": 0, "new": 222},
                    )

                    # Another process then publishes a complete history by
                    # atomic replacement, as the product writers do.
                    subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            (
                                "import json,os,sys,tempfile; from pathlib import Path; "
                                 "p=Path(sys.argv[1]); payload=json.loads(sys.argv[2]); "
                                "f=tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=p.parent,"
                                "prefix='.'+p.name+'.',suffix='.tmp',delete=False); "
                                 "json.dump(payload,f,indent=2); f.flush(); os.fsync(f.fileno()); "
                                "name=f.name; f.close(); os.replace(name,p)"
                            ),
                            str(ledger),
                            json.dumps(
                                {
                                    "schema": 2,
                                    "events": [
                                        {
                                            "at": (now - timedelta(hours=2)).isoformat(),
                                            "kind": "observed",
                                            "account_id": old.email,
                                        },
                                        {
                                            "at": (now - timedelta(hours=1)).isoformat(),
                                            "kind": "observed",
                                            "account_id": new.email,
                                        },
                                    ],
                                }
                            ),
                        ],
                        check=True,
                    )

                    self.assertEqual(
                        self._usage_by_account(base + "/api/local-windows"),
                        {"old": 111, "new": 222},
                    )
                    with urllib.request.urlopen(
                        base + "/api/attribution-diagnostics", timeout=5
                    ) as response:
                        diagnostics = json.load(response)
                    self.assertEqual(diagnostics["identity_runs"]["codex"]["count"], 2)
                    self.assertTrue(
                        diagnostics["identity_runs"]["codex"]["matches_disk"],
                        diagnostics["identity_runs"]["codex"],
                    )
                    self.assertTrue(
                        diagnostics["local_windows_cache"]["identity_stamp_matches_disk"]
                    )
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(timeout=5)
                    accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
                    serve._local_cache = {
                        "at": 0.0,
                        "payload": None,
                        "identity_stamp": None,
                        "source_stamp": None,
                    }

    def test_live_http_cache_reloads_grok_ledger_written_by_another_process(self):
        """Grok ownership changes must also invalidate a running panel immediately."""
        from sandglass import accounts, serve

        now = datetime.now(timezone.utc)
        minute = now - timedelta(minutes=30)
        window = {
            "label": "7d",
            "used_percent": 10,
            "window_minutes": 7 * 24 * 60,
            "resets_at": (now + timedelta(days=1)).isoformat(),
        }
        old = Account(
            provider="grok",
            account_id="old-grok",
            email="old-grok@example.com",
            extra={"windows": [window]},
        )
        new = Account(
            provider="grok",
            account_id="new-grok",
            email="new-grok@example.com",
            extra={"windows": [window]},
        )
        session = SessionRecord(
            provider="grok",
            session_id="grok-switch-test",
            path="updates.jsonl",
            timeline=[(minute.isoformat(), TokenUsage(input_tokens=321, calls=1))],
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meter = root / "meter"
            meter.mkdir()
            ledger = meter / "grok-official-identity-runs.json"
            ledger.write_text(
                json.dumps(
                    [{"at": (now - timedelta(hours=2)).isoformat(), "account_id": old.account_id}],
                    indent=2,
                ),
                encoding="utf-8",
            )

            with patch.object(accounts, "meter_home", lambda: meter), patch.object(
                accounts, "codex_home", lambda: root / "codex"
            ), patch.object(accounts, "grok_home", lambda: root / "grok"), patch.object(
                serve, "load_accounts", lambda: [old, new]
            ), patch.object(serve, "collect_all", lambda cache=None: [session]), patch.object(
                serve, "SessionCache", _NoopCache
            ), patch.object(
                serve, "quota_anchor", lambda provider, account_id, label: ("", "period", False)
            ):
                accounts._GROK_RUNS = accounts._GROK_RUNS_SRC = None
                serve._local_cache = {"at": 0.0, "payload": None, "identity_stamp": None}
                httpd = ThreadingHTTPServer(
                    ("127.0.0.1", 0),
                    partial(serve.Handler, since=None, live_quota=False),
                )
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                url = f"http://127.0.0.1:{httpd.server_port}/api/local-windows"
                try:
                    before = self._usage_by_account(url)
                    self.assertEqual(before, {"old-grok": 321, "new-grok": 0})

                    subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            (
                                "import json,sys; from pathlib import Path; "
                                "p=Path(sys.argv[1]); rows=json.loads(p.read_text(encoding='utf-8')); "
                                "rows.append({'at':sys.argv[2],'account_id':sys.argv[3]}); "
                                "p.write_text(json.dumps(rows,indent=2),encoding='utf-8')"
                            ),
                            str(ledger),
                            (now - timedelta(hours=1)).isoformat(),
                            new.account_id,
                        ],
                        check=True,
                    )

                    after = self._usage_by_account(url)
                    self.assertEqual(after, {"old-grok": 0, "new-grok": 321})
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(timeout=5)
                    accounts._GROK_RUNS = accounts._GROK_RUNS_SRC = None
                    serve._local_cache = {"at": 0.0, "payload": None, "identity_stamp": None}

    @staticmethod
    def _usage_by_account(url: str) -> dict[str, int]:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.load(response)
        return {
            row["account_id"]: row["windows"][0]["usage"]["total_tokens"]
            for row in payload["accounts"]
        }


if __name__ == "__main__":
    unittest.main()
