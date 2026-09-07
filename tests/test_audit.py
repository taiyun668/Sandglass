import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from tools import audit
from sandglass.models import Account, SessionRecord, TokenUsage
from sandglass.telemetry import TelemetryStore, ingest_otlp_logs


class AuditAvailabilityTests(unittest.TestCase):
    def tearDown(self):
        audit._results.clear()

    def setUp(self):
        audit._results.clear()

    def test_panel_check_is_skipped_only_when_the_app_is_really_down(self):
        """A stopped app is the one honest reason to skip this check."""
        with mock.patch.object(audit, "_sandglass_running", lambda: False), mock.patch(
            "sandglass.live_snapshot.live_snapshots", lambda *a, **k: []
        ):
            audit.audit_against_panel([])
        self.assertEqual(
            audit._results, [(audit.SKIP, "面板对账", "Sandglass 未运行")]
        )

    def test_a_running_app_with_nothing_recorded_fails_instead(self):
        """The earlier version of this test asserted the defect.

        It required a SKIP whenever urlopen raised, and an HTTPError is a
        URLError: once the panel's old port went to the OTLP receiver, that
        receiver's 404 satisfied this test while the check it guards stopped
        running at all. What matters is not how the read failed but whether
        there was anything to reconcile against.
        """
        with mock.patch.object(audit, "_sandglass_running", lambda: True), mock.patch(
            "sandglass.live_snapshot.live_snapshots", lambda *a, **k: []
        ):
            audit.audit_against_panel([])
        self.assertEqual([status for status, _, _ in audit._results], [audit.FAIL])

    def test_accounting_snapshot_ignores_only_live_reset_countdown(self):
        first = {
            "totals": {"total_tokens": 10},
            "accounts": [
                {
                    "windows": [
                        {
                            "used_percent": 25,
                            "resets_at": "2026-09-01T00:00:00Z",
                            "resets_in_seconds": 61,
                        }
                    ]
                }
            ],
        }
        second = {
            "totals": {"total_tokens": 10},
            "accounts": [
                {
                    "windows": [
                        {
                            "used_percent": 25,
                            "resets_at": "2026-09-01T00:00:00Z",
                            "resets_in_seconds": 60,
                        }
                    ]
                }
            ],
        }
        self.assertEqual(
            audit._report_accounting_snapshot(first),
            audit._report_accounting_snapshot(second),
        )
        second["totals"]["total_tokens"] = 11
        self.assertNotEqual(
            audit._report_accounting_snapshot(first),
            audit._report_accounting_snapshot(second),
        )

    def test_direct_user_identity_beats_observation_timeline(self):
        session = SessionRecord(
            provider="codex",
            session_id="direct",
            path="direct.jsonl",
            account_id="adapter-account",
            extra={"evidence_sources": ["user_adapter:owner.timeline"]},
        )
        ledger = audit.Ledger(
            [("2026-08-30T00:00:00+00:00", "observed-account")]
        )

        owner = audit.owner_of(
            session,
            datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
            ledger,
            {"adapter-account", "observed-account"},
        )

        self.assertEqual(owner, "adapter-account")

    def test_exact_minute_identity_beats_observation_timeline(self):
        session = SessionRecord(
            provider="codex",
            session_id="minute",
            path="minute.jsonl",
            extra={
                "minute_identity_evidence": {
                    "2026-08-30T01:00:00Z": {"account_id": "minute-account"}
                }
            },
        )
        ledger = audit.Ledger(
            [("2026-08-30T00:00:00+00:00", "observed-account")]
        )

        owner = audit.owner_of(
            session,
            datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
            ledger,
            {"minute-account", "observed-account"},
        )

        self.assertEqual(owner, "minute-account")

    def test_unassigned_codex_boundary_clears_the_observation_owner(self):
        ledger = audit.Ledger([
            ("2026-08-30T00:00:00+00:00", "observed-account"),
            ("2026-08-30T01:00:00+00:00", ""),
        ])
        self.assertEqual(
            ledger.at(datetime(2026, 8, 30, 0, 30, tzinfo=timezone.utc)),
            "observed-account",
        )
        self.assertEqual(
            ledger.at(datetime(2026, 8, 30, 1, 0, tzinfo=timezone.utc)), ""
        )
        self.assertTrue(
            ledger.explicitly_unassigned_at(
                datetime(2026, 8, 30, 1, 30, tzinfo=timezone.utc)
            )
        )

    def _codex_stream(self, home, events):
        """Write the v2 stream the audit reads for itself.

        These tests used to hand the ledger to the check through
        audit.codex_identity_runs -- the product's own projection. The check no
        longer consults it, precisely so that a projection cannot silence a
        failure in the instrument that tests it, so the evidence goes on disk
        where the audit's own validator picks it up.
        """
        (home / "codex-official-identity-events-v2.json").write_text(
            json.dumps({"schema": 2, "events": events}), encoding="utf-8"
        )

    OBSERVED_THEN_GAP = [
        {"at": "2026-08-30T00:00:00Z", "kind": "observed", "account_id": "known"},
        {"at": "2026-08-30T01:00:00Z", "kind": "unassigned", "reason": "observer_stopped"},
    ]

    def test_post_open_audit_accepts_only_an_explicit_unassigned_gap(self):
        usage = TokenUsage(input_tokens=7, calls=1)
        session = SessionRecord(
            provider="codex",
            session_id="gap",
            path="rollout.jsonl",
            timeline=[("2026-08-30T01:30:00Z", usage)],
        )
        account = Account(provider="codex", account_id="known", active=True)
        opened = datetime(2026, 8, 30, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._codex_stream(home, self.OBSERVED_THEN_GAP)
            with mock.patch.object(audit, "_book_opened_at", return_value=opened),                  mock.patch.object(audit, "meter_home", lambda: home):
                audit.audit_post_open_attribution([session], [account])
        # Every token in this window sits behind a "we were not observing"
        # boundary, so the check examined nothing. It may report that, and it
        # may not report a pass: one failed identity read on a fresh install
        # writes exactly such a boundary, and this line used to print PASS over
        # 100% unattributed usage for as long as that lasted.
        self.assertEqual(audit._results[-1][0], audit.SKIP)
        self.assertIn("显式缺口 7", audit._results[-1][2])

        audit._results.clear()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._codex_stream(home, [
                {"at": "2026-08-30T00:00:00Z", "kind": "observed",
                 "account_id": "forgotten-account"},
            ])
            with mock.patch.object(audit, "_book_opened_at", return_value=opened),                  mock.patch.object(audit, "meter_home", lambda: home):
                audit.audit_post_open_attribution([session], [account])
        self.assertEqual(audit._results[-1][0], audit.FAIL)
        self.assertIn("异常未归属 7", audit._results[-1][2])

    def test_post_open_audit_still_passes_when_it_had_something_to_check(self):
        """The counterpart: skipping the all-gap window must not skip everything.

        An owned minute beside a gap minute is a window with something in it,
        and the check has to render a verdict on the part it could see.
        """
        usage = TokenUsage(input_tokens=7, calls=1)
        session = SessionRecord(
            provider="codex",
            session_id="mixed",
            path="rollout.jsonl",
            timeline=[
                ("2026-08-30T00:30:00Z", usage),   # owned
                ("2026-08-30T01:30:00Z", usage),   # explicit gap
            ],
        )
        account = Account(provider="codex", account_id="known", active=True)
        opened = datetime(2026, 8, 30, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._codex_stream(home, self.OBSERVED_THEN_GAP)
            with mock.patch.object(audit, "_book_opened_at", return_value=opened),                  mock.patch.object(audit, "meter_home", lambda: home):
                audit.audit_post_open_attribution([session], [account])

        self.assertEqual(audit._results[-1][0], audit.PASS)
        self.assertIn("显式缺口 7", audit._results[-1][2])
        self.assertIn("异常未归属 0", audit._results[-1][2])

    def test_the_check_does_not_take_its_ledger_from_the_product(self):
        """A projection that can silence this check may not supply it.

        While the product's projection could only *name* an owner, the audit
        checked that name against the account roster and the arrangement was
        sound. Accepting an explicit unassigned boundary gave it a second power:
        an empty owner now reclassifies an unattributed minute as a deliberate
        coverage gap, which is the difference between a red line and a green
        one. So the ledger is read by this module's own validator instead.

        Poisoning codex_identity_runs is how that is proved: if the check still
        reaches the right verdict from the file alone, it is not consulting it.
        """
        usage = TokenUsage(input_tokens=7, calls=1)
        session = SessionRecord(
            provider="codex",
            session_id="poisoned",
            path="rollout.jsonl",
            timeline=[("2026-08-30T01:30:00Z", usage)],
        )
        account = Account(provider="codex", account_id="known", active=True)
        opened = datetime(2026, 8, 30, tzinfo=timezone.utc)

        def poisoned():
            raise AssertionError("the audit consulted the product's projection")

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._codex_stream(home, self.OBSERVED_THEN_GAP)
            with mock.patch.object(audit, "_book_opened_at", return_value=opened),                  mock.patch.object(audit, "meter_home", lambda: home),                  mock.patch.object(audit, "codex_identity_runs", poisoned):
                audit.audit_post_open_attribution([session], [account])

        self.assertEqual(audit._results[-1][0], audit.SKIP)
        self.assertIn("显式缺口 7", audit._results[-1][2])

    def test_the_conservation_line_asserts_only_what_it_can_catch(self):
        """unique + unassigned + duplicated == total was true by construction.

        Every token in the loop lands in exactly one of the three buckets, so
        that conjunct could never fail while reading like a second condition.
        What remains is the one thing this line can catch: a minute claimed by
        more than one account. Unowned volume is 开账后归属's question.
        """
        usage = TokenUsage(input_tokens=7, calls=1)
        session = SessionRecord(
            provider="codex",
            session_id="unowned",
            path="rollout.jsonl",
            timeline=[(datetime.now(timezone.utc).isoformat(), usage)],
        )
        account = Account(provider="codex", account_id="known", active=True)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._codex_stream(home, [
                {"at": "2026-08-30T00:00:00Z", "kind": "unassigned",
                 "reason": "observer_stopped"},
            ])
            with mock.patch.object(audit, "meter_home", lambda: home):
                audit.audit_conservation([session], [account])

        # Nobody owns it, and this line still passes -- that is its contract,
        # and the reason something else has to be able to fail on it.
        self.assertTrue(all(status == audit.PASS for status, _, _ in audit._results))
        self.assertIn("唯一归属 0", audit._results[0][2])

        # And the one thing it can catch still fails it: two accounts answering
        # to the same name both claim the minute.
        audit._results.clear()
        twins = [
            Account(provider="codex", account_id="known", active=True),
            Account(provider="codex", account_id="other", email="known"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._codex_stream(home, [
                {"at": "2026-08-30T00:00:00Z", "kind": "observed",
                 "account_id": "known"},
            ])
            with mock.patch.object(audit, "meter_home", lambda: home):
                audit.audit_conservation([session], twins)

        self.assertTrue(any(status == audit.FAIL for status, _, _ in audit._results),
                        "同一分钟被两个账号认领,这道线必须失败")

    def test_session_majority_projection_does_not_reown_an_explicit_ledger_gap(self):
        usage = TokenUsage(input_tokens=1, calls=1)
        session = SessionRecord(
            provider="codex",
            session_id="majority-gap",
            path="rollout.jsonl",
            account_id="a",
            timeline=[("2026-08-30T01:00:00Z", usage)],
            extra={"account_identity_scope": "session_timeline_majority"},
        )
        ledger = audit.Ledger([
            ("2026-08-30T00:00:00Z", "a"),
            ("2026-08-30T01:00:00Z", ""),
            ("2026-08-30T02:00:00Z", "a"),
        ])

        self.assertEqual(
            audit.owner_of(
                session,
                datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
                ledger,
                {"a"},
            ),
            "",
        )

    def test_user_adapter_account_id_remains_direct_evidence(self):
        session = SessionRecord(
            provider="codex",
            session_id="adapter-direct",
            path="adapter.jsonl",
            account_id="adapter-account",
            extra={"evidence_sources": ["user_adapter:owner.timeline"]},
        )

        self.assertEqual(
            audit.owner_of(
                session,
                datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
                audit.Ledger([]),
                {"adapter-account"},
            ),
            "adapter-account",
        )

    def test_single_official_audit_requires_one_active_peer(self):
        legacy = Account(provider="codex", account_id="legacy", active=False)
        current = Account(provider="codex", account_id="current", active=True)

        self.assertEqual(audit._audit_single_official_owner_id([legacy]), "")
        self.assertEqual(
            audit._audit_single_official_owner_id([legacy, current]), "current"
        )

    def test_single_official_panel_projection_keeps_user_adapter_minute_owner(self):
        official = SessionRecord(
            provider="claude",
            session_id="official",
            path="official.jsonl",
            timeline=[("2026-09-04T12:00:00Z", TokenUsage(input_tokens=10, calls=1))],
            extra={"evidence_sources": ["official_builtin:claude"]},
        )
        adapted = SessionRecord(
            provider="claude",
            session_id="adapted",
            path="adapted.jsonl",
            account_id="b",
            timeline=[("2026-09-04T12:00:00Z", TokenUsage(input_tokens=90, calls=1))],
            extra={"evidence_sources": ["user_adapter:owner.timeline"]},
        )
        current = Account(provider="claude", account_id="a", active=True)
        mapped = Account(provider="claude", account_id="b", active=False)
        local = {
            "a|7d": {
                "total": 10,
                "from": "2026-09-01T00:00:00Z",
                "to": "2026-09-08T00:00:00Z",
                "boundary": "reset",
                "days": {"2026-09-04": 10},
            }
        }
        snapshot = {
            "readings": {
                "local_windows": [{"at": "2026-09-05T00:00:00Z", "value": local}],
                "quota_windows": [{"at": "2026-09-05T00:00:00Z", "value": {}}],
                "product_mode": [{
                    "at": "2026-09-05T00:00:00Z",
                    "value": {"attribution_mode": "single_official"},
                }],
            }
        }
        with mock.patch.object(audit, "_live_snapshot", return_value=snapshot), mock.patch.object(
            audit, "_newer_local_windows", return_value=local
        ), mock.patch.object(audit, "collect_all", return_value=[official, adapted]), mock.patch.object(
            audit, "_book_opened_at", return_value=datetime(2026, 9, 1, tzinfo=timezone.utc)
        ), mock.patch.object(audit, "claude_identity_runs", return_value=[]), mock.patch.object(
            audit, "codex_identity_runs", return_value=[]
        ), mock.patch.object(audit, "grok_identity_runs", return_value=[]), mock.patch(
            "sandglass.telemetry.apply_user_evidence", side_effect=lambda sessions, settings: sessions
        ), mock.patch("sandglass.user_sources.UserSourceStore") as store:
            store.return_value.settings.return_value = {}
            audit.audit_against_panel([current, mapped])

        self.assertEqual(audit._results[0][0], audit.PASS, audit._results[0][2])

    def test_switch_boundary_is_half_open_at_the_next_switch(self):
        at = "2026-09-04T01:00:00Z"
        usage = TokenUsage(input_tokens=1, calls=1)
        session = SessionRecord(
            provider="claude",
            session_id="boundary",
            path="boundary.jsonl",
            timeline=[(at, usage)],
        )
        accounts = [
            Account(provider="claude", account_id="a"),
            Account(provider="claude", account_id="b"),
        ]
        runs = [
            ("2026-09-04T00:00:00Z", "a"),
            (at, "b"),
        ]
        with mock.patch.object(audit, "claude_identity_runs", return_value=runs), mock.patch.object(
            audit, "codex_identity_runs", return_value=[]
        ), mock.patch.object(audit, "grok_identity_runs", return_value=[]):
            audit.audit_switch_boundaries([session], accounts)

        self.assertEqual(audit._results, [(audit.PASS, "claude 切号边界", "切号到下次切号之间 1 个分钟，归属错的 0 个")])

    def test_panel_audit_uses_the_admitted_input_set_before_independent_ownership(self):
        source = (__import__("pathlib").Path(__file__).resolve().parents[1] / "tools" / "audit.py").read_text(
            encoding="utf-8"
        )
        panel = source.split("def audit_against_panel", 1)[1].split(
            "def audit_parse_layer", 1
        )[0]

        self.assertIn("sessions = apply_user_evidence(", panel)
        self.assertIn("settings=UserSourceStore().settings()", panel)
        self.assertNotIn("_owns_minute", panel)


class OtlpShadowAuditTests(unittest.TestCase):
    """The live check skips on an empty store. That is not a pass of the join."""

    def setUp(self):
        audit._results.clear()

    def tearDown(self):
        audit._results.clear()

    def test_an_empty_store_skips_instead_of_passing(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ):
            audit.audit_telemetry_shadow([])
        self.assertEqual(
            audit._results[-1],
            (audit.SKIP, "OTLP 影子匹配", "尚未收到携带账号身份的官方 OTLP 事件"),
        )

    def test_an_identity_event_with_no_account_conflict_is_a_verdict(self):
        from tests.test_telemetry import _payload

        usage = TokenUsage(input_tokens=10, output_tokens=40, cache_read_tokens=20, cache_write_tokens=30, calls=1)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ):
            ingest_otlp_logs(
                _payload(
                    "claude-code",
                    "api_request",
                    {
                        "request_id": "audit-exact",
                        "session.id": "session-exact",
                        "user.account_id": "account-exact",
                        "input_tokens": 10,
                        "cache_read_tokens": 20,
                        "cache_creation_tokens": 30,
                        "output_tokens": 40,
                    },
                ),
                TelemetryStore(),
            )
            event_at = TelemetryStore().records()[0]["event_at"]
            session = SessionRecord(
                provider="claude",
                session_id="session-exact",
                path="local.jsonl",
                usage=usage,
                timeline=[(event_at, usage)],
            )
            audit.audit_telemetry_shadow([session])
        status, name, detail = audit._results[-1]
        self.assertEqual(status, audit.PASS)
        self.assertEqual(name, "OTLP 影子匹配")
        self.assertIn("精确匹配 1", detail)
        self.assertIn("账号冲突 0", detail)

    def test_two_accounts_in_one_minute_fail(self):
        from tests.test_telemetry import _payload

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ):
            store = TelemetryStore()
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
            audit.audit_telemetry_shadow([])
        status, name, detail = audit._results[-1]
        self.assertEqual(status, audit.FAIL)
        self.assertEqual(name, "OTLP 影子匹配")
        self.assertIn("账号冲突 1", detail)


if __name__ == "__main__":
    unittest.main()
