"""Guards for the audit tool itself, which is not safer than what it audits.

Two of its checks excused themselves for three days while the thing they check
was running the whole time: the panel's port had been handed to the OTLP
receiver, and that receiver's 404 arrives through urllib as a URLError -- the
same exception a machine with nothing listening produces. A check that cannot
tell those apart reports "not running" and skips, and a skip reads like a pass.

The other half is worse because it is silent: an app container gives its package
a redirected %LOCALAPPDATA%, so two Sandglass processes can run the same
meter_home() and write to different directories. Every other check then reports
confidently on whichever copy the audit happened to open.
"""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from datetime import datetime, timezone

from sandglass.models import Account

import tools.audit as audit


class _Panel(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/api/product-mode":
            self.send_error(404)
            return
        body = json.dumps({"mode": "test"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


class _NotThePanel(BaseHTTPRequestHandler):
    """Stands in for the OTLP receiver: listening, and 404 on every route."""

    def do_GET(self):  # noqa: N802
        self.send_error(404)

    def log_message(self, *args):
        return


class _AuditCase(unittest.TestCase):
    def setUp(self):
        audit._results.clear()
        audit._PANEL_ORIGIN = None
        self.addCleanup(audit._results.clear)
        self.addCleanup(setattr, audit, "_PANEL_ORIGIN", None)

    def verdicts(self):
        return [status for status, _, _ in audit._results]

    def _ledger(self, home: Path, at: str) -> None:
        home.mkdir(parents=True, exist_ok=True)
        (home / "codex-identity-runs.json").write_text(
            json.dumps([{"at": at}]), encoding="utf-8"
        )


class StateHomeTests(_AuditCase):
    """What makes a second state home dangerous is what only it knows."""

    def _runs(self, home: Path, rows) -> None:
        self._events(
            home,
            [{"at": at, "kind": "observed", "account_id": who} for at, who in rows],
        )

    def _events(self, home: Path, events) -> None:
        home.mkdir(parents=True, exist_ok=True)
        (home / "codex-official-identity-events-v2.json").write_text(
            json.dumps({"schema": 2, "events": events}), encoding="utf-8"
        )

    def _verdict(self, canonical_rows, shadow_rows):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "sandglass"
            self._runs(home, canonical_rows)
            local = Path(tmp) / "localappdata"
            if shadow_rows is not None:
                self._runs(
                    local / "Packages" / "Vendor.App_abc" / "LocalCache" / "Local" / "sandglass",
                    shadow_rows,
                )
            else:
                local.mkdir(parents=True)
            with patch.dict(os.environ, {"SANDGLASS_HOME": str(home),
                                         "LOCALAPPDATA": str(local)}):
                audit.audit_state_home_singleton()
        return audit._results[0]

    ONE = ("2026-09-01T00:00:00+00:00", "alice")
    TWO = ("2026-09-01T02:00:00+00:00", "bob")

    def test_a_single_home_passes(self):
        self.assertEqual(self._verdict([self.ONE, self.TWO], None)[0], audit.PASS)

    def test_a_home_holding_an_owner_the_canonical_lacks_fails(self):
        """The Grok-shaped hazard: real identity history, only in the container."""
        status, _, detail = self._verdict(
            [self.TWO], [("2026-08-30T00:00:00+00:00", "carol"), self.TWO]
        )
        self.assertEqual(status, audit.FAIL)
        self.assertIn("codex-official-identity-events-v2.json", detail)

    def test_a_home_naming_a_different_owner_for_the_same_span_fails(self):
        status, _, _ = self._verdict(
            [self.ONE, self.TWO], [self.ONE, ("2026-09-01T02:00:00+00:00", "dave")]
        )
        self.assertEqual(status, audit.FAIL)

    def test_a_partial_copy_is_not_a_divergence(self):
        """Holding less loses nothing: there is no evidence only it has."""
        self.assertEqual(self._verdict([self.ONE, self.TWO], [self.ONE])[0], audit.PASS)

    def test_repeat_observations_that_open_no_new_run_are_not_a_divergence(self):
        """The case that made an earlier version of this check permanently red.

        A ledger drops an observation whose identity matches the one before it --
        it opens no run and moves no boundary. Comparing rows called that a
        divergence no merge could ever clear; comparing timelines does not.
        """
        status, _, _ = self._verdict(
            [self.ONE, self.TWO],
            [self.ONE, ("2026-09-01T01:00:00+00:00", "alice"), self.TWO],
        )
        self.assertEqual(status, audit.PASS)

    def test_v2_observation_only_in_shadow_is_a_divergence(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = Path(tmp) / "canonical"
            shadow = Path(tmp) / "localappdata" / "Packages" / "Vendor.App_abc" / "LocalCache" / "Local" / "sandglass"
            self._events(canonical, [{"at": self.ONE[0], "kind": "unassigned", "reason": "gap"}])
            self._events(shadow, [{"at": self.ONE[0], "kind": "observed", "account_id": "shadow-only"}])
            with patch.dict(os.environ, {"SANDGLASS_HOME": str(canonical), "LOCALAPPDATA": str(Path(tmp) / "localappdata")}):
                audit.audit_state_home_singleton()
                status, _, detail = audit._results[0]
        self.assertEqual(status, audit.FAIL)
        self.assertIn("codex-official-identity-events-v2.json", detail)

    def test_v2_unassigned_boundary_clears_owner_and_matching_gap_passes(self):
        events = [
            {"at": self.ONE[0], "kind": "observed", "account_id": "alice"},
            {"at": "2026-09-01T01:00:00+00:00", "kind": "unassigned", "reason": "observer_stopped"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            canonical = Path(tmp) / "canonical"
            shadow = Path(tmp) / "localappdata" / "Packages" / "Vendor.App_abc" / "LocalCache" / "Local" / "sandglass"
            self._events(canonical, events)
            self._events(shadow, events)
            with patch.dict(os.environ, {"SANDGLASS_HOME": str(canonical), "LOCALAPPDATA": str(Path(tmp) / "localappdata")}):
                audit.audit_state_home_singleton()
                status = audit._results[0][0]
                timeline = audit._owner_timeline(canonical / "codex-official-identity-events-v2.json")
        self.assertEqual(status, audit.PASS)
        self.assertEqual(audit._owner_at(timeline, "2026-09-01T01:00:00+00:00"), "")

    def test_legacy_v1_codex_roster_is_not_an_ownership_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "codex-official-identity-runs.json"
            path.write_text(json.dumps([{"at": self.ONE[0], "to": "legacy"}]), encoding="utf-8")
            self.assertEqual(audit._owner_timeline(path), [])

    def test_timeline_orders_utc_semantically_for_valid_v1_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude-official-identity-runs.json"
            path.write_text(json.dumps([
                {"at": "2026-09-01T00:00:00.100000+00:00", "account_id": "fraction"},
                {"at": "2026-09-01T00:00:00Z", "account_id": "whole"},
            ]), encoding="utf-8")
            timeline = audit._owner_timeline(path)
        self.assertEqual(audit._owner_at(timeline, timeline[0][0]), "whole")
        self.assertEqual(
            audit._owner_at(timeline, "2026-09-01T00:00:00.050000Z"), "whole"
        )
        self.assertEqual(
            audit._owner_at(timeline, "2026-09-01T00:00:00.200000+00:00"), "fraction"
        )
        self.assertEqual(audit._owner_at(timeline, "bad"), "")

    def test_shadow_divergence_compares_equivalent_mixed_utc_spellings(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = Path(tmp) / "canonical"
            shadow = Path(tmp) / "localappdata" / "Packages" / "Vendor.App_abc" / "LocalCache" / "Local" / "sandglass"
            self._events(canonical, [
                {"at": "2026-09-01T00:00:00Z", "kind": "observed", "account_id": "alice"},
                {"at": "2026-09-01T00:00:00.500000Z", "kind": "observed", "account_id": "bob"},
            ])
            self._events(shadow, [
                {"at": "2026-09-01T00:00:00+00:00", "kind": "observed", "account_id": "alice"},
                {"at": "2026-09-01T00:00:00.500000+00:00", "kind": "observed", "account_id": "bob"},
            ])
            with patch.dict(os.environ, {"SANDGLASS_HOME": str(canonical), "LOCALAPPDATA": str(Path(tmp) / "localappdata")}):
                audit.audit_state_home_singleton()
                status, _, detail = audit._results[0]
        self.assertEqual(status, audit.PASS, detail)


class RecordedAnswerTests(_AuditCase):
    """The panel cannot be asked, so what it answered has to be on disk."""

    def _home(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name) / "sandglass"
        home.mkdir()
        patcher = patch.dict(os.environ, {"SANDGLASS_HOME": str(home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        from sandglass import live_snapshot

        live_snapshot.set_role("test")
        self.addCleanup(live_snapshot.set_role, "")
        return home

    def test_the_recorded_answer_is_the_answer_the_panel_was_given(self):
        """Not a re-derivation: the same object api_payload returned."""
        from sandglass import live_snapshot, serve

        self._home()
        payload = {"accounts": [{"account_id": "a1", "windows": [
            {"label": "7d", "usage": {"total_tokens": 4242}}]}]}
        with patch.object(serve, "_local_windows_payload", lambda live: payload):
            returned = serve.api_payload("/api/local-windows", live_quota=False)
        self.assertIs(returned, payload)
        recorded = live_snapshot.live_snapshots()
        self.assertEqual(len(recorded), 1)
        self.assertEqual(
            recorded[0]["readings"]["local_windows"][-1]["value"],
            {"a1|7d": {"total": 4242, "from": "", "to": "", "boundary": "", "days": {}}},
        )

    def test_a_snapshot_from_a_dead_process_is_deleted_not_reported(self):
        """A dead process's last answer looks exactly like a live one gone quiet."""
        from sandglass import live_snapshot

        home = self._home()
        stale = home / f"{live_snapshot.PREFIX}999999.json"
        stale.write_text(json.dumps({
            "schema": 1, "process_id": 999999, "role": "panel",
            "readings": {"local_windows": [{"at": "2999-01-01T00:00:00+00:00",
                                            "value": {}}]},
        }), encoding="utf-8")
        self.assertEqual(live_snapshot.live_snapshots(), [])
        self.assertFalse(stale.exists())

    def test_a_recycled_pid_does_not_revive_a_dead_writer(self):
        """Windows hands pids out again; the number alone identifies nothing.

        Without the creation time, a snapshot left by a writer that died would
        read as a living process still answering as soon as anything else was
        given its number -- a substitution of exactly the kind this file exists
        to rule out.
        """
        from sandglass import live_snapshot

        home = self._home()
        mine = os.getpid()
        path = home / f"{live_snapshot.PREFIX}{mine}.json"
        path.write_text(json.dumps({
            "schema": 1, "process_id": mine, "role": "panel",
            "process_started_at": "an-older-process-with-this-pid",
            "readings": {"local_windows": [{"at": "2999-01-01T00:00:00+00:00",
                                            "value": {}}]},
        }), encoding="utf-8")

        self.assertEqual(live_snapshot.live_snapshots(), [])
        self.assertFalse(path.exists())

    def test_this_live_process_is_reported(self):
        """The counterpart: the check must not reject a genuinely live writer."""
        from sandglass import live_snapshot

        self._home()
        live_snapshot.record("local_windows", {"a1|7d": {"total": 1}})
        found = live_snapshot.live_snapshots()
        self.assertEqual([row["process_id"] for row in found], [os.getpid()])

    def test_a_running_app_that_wrote_nothing_fails(self):
        self._home()
        with patch.object(audit, "_sandglass_running", lambda: True):
            self.assertIsNone(audit._live_snapshot("面板对账", "local_windows"))
        self.assertEqual(self.verdicts(), [audit.FAIL])

    def test_a_running_app_missing_one_reading_fails_and_names_it(self):
        from sandglass import live_snapshot

        self._home()
        live_snapshot.record("product_mode", {"attribution_mode": "x"})
        with patch.object(audit, "_sandglass_running", lambda: True):
            audit._live_snapshot("面板对账", "local_windows", "product_mode")
        status, _, detail = audit._results[0]
        self.assertEqual(status, audit.FAIL)
        self.assertIn("local_windows", detail)

    def test_a_stopped_app_is_the_one_honest_skip(self):
        self._home()
        with patch.object(audit, "_sandglass_running", lambda: False):
            self.assertIsNone(audit._live_snapshot("面板对账", "local_windows"))
        self.assertEqual(self.verdicts(), [audit.SKIP])

    def test_a_complete_snapshot_is_returned(self):
        from sandglass import live_snapshot

        self._home()
        live_snapshot.record("local_windows", {"a1|7d": {"total": 1}})
        live_snapshot.record("product_mode", {"attribution_mode": "x"})
        with patch.object(audit, "_sandglass_running", lambda: True):
            found = audit._live_snapshot("面板对账", "local_windows", "product_mode")
        self.assertIsNotNone(found)
        self.assertEqual(self.verdicts(), [])
        self.assertEqual(audit._window_totals(audit._reading(found, "local_windows")),
                         {("a1", "7d"): 1})


class PromisedDayTests(_AuditCase):
    """The pre-intervention allowance must not become a blanket excuse.

    An earlier version compared window totals and asked whether the shortfall
    was small enough to fit inside the pre-intervention minutes. It always
    could, whenever that pool was large -- so a panel that genuinely dropped
    promised tokens passed, as long as it had enough unattributable history to
    hide them behind. Totals cannot separate the two; dated days can.
    """

    OPEN = "2026-08-29"

    def _value(self, days):
        return {"a1|7d": {"total": sum(days.values()), "days": days}}

    def test_only_days_after_the_opening_are_counted(self):
        value = self._value({"2026-08-28": 600, "2026-08-30": 400})
        self.assertEqual(audit._promised_days(value, ("a1", "7d"), self.OPEN), 400)

    def test_the_opening_day_itself_is_excluded_as_partial(self):
        value = self._value({"2026-08-29": 500, "2026-08-30": 400})
        self.assertEqual(audit._promised_days(value, ("a1", "7d"), self.OPEN), 400)

    def test_a_provider_with_no_opening_puts_every_day_inside_the_promise(self):
        value = self._value({"2026-08-28": 600, "2026-08-30": 400})
        self.assertEqual(audit._promised_days(value, ("a1", "7d"), ""), 1000)

    def test_a_large_pre_intervention_history_hides_nothing(self):
        """The case the old formula let through.

        The panel withholds 500 of 1000; 600 of that window predates the
        opening, so a total-based test says the shortfall fits. But the days
        say the panel showed 300 of the 400 promised tokens, and the missing
        100 were spent after the books opened.
        """
        value = self._value({"2026-08-28": 600, "2026-08-30": 300})
        self.assertEqual(audit._promised_days(value, ("a1", "7d"), self.OPEN), 300)
        self.assertNotEqual(audit._promised_days(value, ("a1", "7d"), self.OPEN), 400)

    def test_a_window_with_no_daily_breakdown_promises_nothing(self):
        self.assertEqual(audit._promised_days({"a1|7d": {"total": 9}},
                                              ("a1", "7d"), self.OPEN), 0)


class WindowBoundaryTests(_AuditCase):
    def _run(self, local, quota):
        snap = {"readings": {"local_windows": [{"at": "x", "value": local}],
                             "quota_windows": {"at": "x", "value": quota}}}
        with patch.object(audit, "_live_snapshot", lambda *a, **k: snap):
            audit.audit_window_boundaries()

    def test_it_still_records_a_line_when_there_is_nothing_to_read(self):
        """Hung off the panel check it produced no line at all in this case.

        Not a pass, not a fail, not even a skip -- the check simply vanished
        from the report, which is the one outcome nobody can notice.
        """
        with patch.object(audit, "_sandglass_running", lambda: True), patch(
            "sandglass.live_snapshot.live_snapshots", lambda *a, **k: []
        ):
            audit.audit_window_boundaries()
        self.assertEqual([name for _, name, _ in audit._results], ["配额窗口边界"])
        self.assertEqual(self.verdicts(), [audit.FAIL])

    def test_a_rolled_local_boundary_is_not_a_defect(self):
        """A 5h window whose official period expired is re-rolled by design."""
        self._run(
            {"a1|5h": {"total": 5, "from": "2026-09-02T06:00:00+00:00",
                       "to": "2026-09-02T11:00:00+00:00",
                       "boundary": "local_token_timeline"}},
            {"a1|5h": ["2026-08-28T22:40:00+00:00", "2026-08-29T03:40:00+00:00"]})
        self.assertEqual(self.verdicts(), [audit.PASS])

    def test_claiming_the_official_window_while_counting_elsewhere_fails(self):
        self._run(
            {"a1|7d": {"total": 5, "from": "2026-09-01T00:00:00+00:00",
                       "to": "2026-09-08T00:00:00+00:00",
                       "boundary": "official_quota_window"}},
            {"a1|7d": ["2026-08-30T00:00:00+00:00", "2026-09-06T00:00:00+00:00"]})
        status, _, detail = audit._results[0]
        self.assertEqual(status, audit.FAIL)
        self.assertIn("自称官方窗口", detail)

    def test_an_inverted_span_fails(self):
        self._run(
            {"a1|7d": {"total": 5, "from": "2026-09-08T00:00:00+00:00",
                       "to": "2026-09-01T00:00:00+00:00",
                       "boundary": "local_token_timeline"}}, {})
        self.assertEqual(self.verdicts(), [audit.FAIL])

    def test_an_unknown_boundary_source_fails(self):
        self._run(
            {"a1|7d": {"total": 5, "from": "2026-09-01T00:00:00+00:00",
                       "to": "2026-09-08T00:00:00+00:00",
                       "boundary": "invented"}}, {})
        self.assertEqual(self.verdicts(), [audit.FAIL])


class ObservationAccountTests(_AuditCase):
    """quota-observations.json drives reset detection and nothing validates it.

    An under-isolated test recorded claude:adapter-account and
    codex:adapter-account into the real store, where they sat looking exactly
    like evidence. The same boundary applies here as everywhere else: before a
    provider's books opened there was no ledger to write an account to, so one
    that has since been signed out can be genuine and yet appear nowhere else.
    After they opened there was.
    """

    OPENED = datetime(2026, 8, 29, 13, 14, 51, tzinfo=timezone.utc)

    def _verdict(self, store):
        account = Account(provider="codex", account_id="known-id", email="a@example.com")
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "quota-observations.json").write_text(
                json.dumps(store), encoding="utf-8"
            )
            with patch.dict(os.environ, {"SANDGLASS_HOME": tmp}), patch.object(
                audit, "_book_opened_at", lambda provider: self.OPENED
            ), patch.object(audit, "claude_identity_runs", list), patch.object(
                audit, "codex_identity_runs", list
            ), patch.object(audit, "grok_identity_runs", list):
                audit.audit_observation_accounts([account])
        return audit._results[0]

    def test_a_known_account_passes(self):
        self.assertEqual(self._verdict({"codex:known-id:7d": {"used": 1}})[0], audit.PASS)

    def test_an_account_signed_out_before_the_books_opened_passes(self):
        status, _, detail = self._verdict(
            {"codex:retired:7d": {"used": 1, "seen_at": "2026-08-29T02:38:27+00:00"}}
        )
        self.assertEqual(status, audit.PASS)
        self.assertIn("开账前退役", detail)

    def test_an_account_first_seen_after_the_books_opened_fails(self):
        status, _, detail = self._verdict(
            {"codex:fixture:7d": {"used": 1, "seen_at": "2026-09-02T00:00:00+00:00"}}
        )
        self.assertEqual(status, audit.FAIL)
        self.assertIn("fixture", detail)

    def test_an_entry_with_no_timestamp_cannot_claim_the_allowance(self):
        """It cannot be shown to predate the opening, so it does not get the benefit."""
        self.assertEqual(self._verdict({"codex:no-stamp:7d": {"used": 1}})[0], audit.FAIL)

    def _codex_home(self, home, *, coverage=None, v2_first="2026-09-05T17:00:00Z"):
        """A Codex state directory with every source this question could use."""
        (home / "codex-observed-accounts.json").write_text(json.dumps([
            {"account_id": "roster", "first_observed_at": "2020-01-01T00:00:00Z"}
        ]), encoding="utf-8")
        (home / "codex-official-identity-runs.json").write_text(json.dumps([
            {"at": "2020-01-02T00:00:00Z", "to": "legacy"}
        ]), encoding="utf-8")
        (home / "codex-official-identity-events-v2.json").write_text(json.dumps({
            "schema": 2,
            "events": [{"at": v2_first, "kind": "observed", "account_id": "later"}],
        }), encoding="utf-8")
        if coverage is not None:
            (home / "observer-coverage.json").write_text(
                json.dumps({"schema": 1, "runs": coverage}), encoding="utf-8"
            )

    def test_codex_book_opening_ignores_legacy_roster_and_observed_accounts(self):
        """Neither the 2020 roster nor the v1 ledger may define the window."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._codex_home(home, coverage=[
                {"started_at": "2026-09-01T00:00:00Z",
                 "last_heartbeat_at": "2026-09-01T00:00:00Z"}
            ])
            with patch.object(audit, "meter_home", lambda: home):
                opened = audit._book_opened_at("codex")
        self.assertEqual(opened, audit.parse_ts("2026-09-01T00:00:00Z"))

    def test_codex_book_opening_is_not_the_stream_it_audits(self):
        """Nor may the v2 stream, which is the thing this check tests.

        A window defined by that file is satisfiable by resetting it: absent,
        the check skips; one event later, it passes on almost nothing. Measured
        outside the app container, the v2-derived window examined a small fraction
        of the Codex tokens the first observer run puts in scope.

        Codex is the only provider with nowhere else to ask -- its rollout files
        record no account at all -- so the window comes from the moment
        Sandglass was first observing, written by another mechanism entirely.
        """
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._codex_home(home, v2_first="2026-09-05T17:00:00Z", coverage=[
                {"started_at": "2026-09-01T00:00:00Z",
                 "last_heartbeat_at": "2026-09-02T00:00:00Z"},
                {"started_at": "2026-09-05T16:00:00Z",
                 "last_heartbeat_at": "2026-09-05T18:00:00Z"},
            ])
            with patch.object(audit, "meter_home", lambda: home):
                opened = audit._book_opened_at("codex")
        self.assertEqual(opened, audit.parse_ts("2026-09-01T00:00:00Z"))

    def test_codex_book_opening_has_no_fallback_without_coverage(self):
        """No observer run recorded means no window, not a window from elsewhere."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._codex_home(home)          # every other source present
            with patch.object(audit, "meter_home", lambda: home):
                self.assertIsNone(audit._book_opened_at("codex"))
            (home / "observer-coverage.json").write_text("{}", encoding="utf-8")
            with patch.object(audit, "meter_home", lambda: home):
                self.assertIsNone(audit._book_opened_at("codex"))

    def test_the_other_providers_keep_their_own_evidence(self):
        """Applying the Codex rule to all three would make the check weaker.

        Grok's ledger is merged out of the vendor's own rotating CLI log and
        legitimately predates the install; measured on this machine, a shared
        per-installation window cut its checked volume to a small fraction of
        what the provider's own ledger covers.
        """
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "grok-official-identity-runs.json").write_text(json.dumps([
                {"at": "2026-08-24T00:00:00Z", "account_id": "who"}
            ]), encoding="utf-8")
            (home / "observer-coverage.json").write_text(json.dumps({
                "schema": 1,
                "runs": [{"started_at": "2026-09-01T00:00:00Z",
                          "last_heartbeat_at": "2026-09-01T00:00:00Z"}],
            }), encoding="utf-8")
            with patch.object(audit, "meter_home", lambda: home):
                opened = audit._book_opened_at("grok")
        self.assertEqual(opened, audit.parse_ts("2026-08-24T00:00:00Z"))


class LedgerReadableTests(_AuditCase):
    """A damaged ledger and a provider never signed in must not look alike."""

    def _verdict(self, write):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write(home)
            with patch.dict(os.environ, {"SANDGLASS_HOME": str(home)}):
                audit.audit_identity_ledgers_readable()
        return audit._results[0]

    def test_absent_ledgers_are_not_a_failure(self):
        """Nothing has been signed in yet; there is nothing to be damaged."""
        self.assertEqual(self._verdict(lambda home: None)[0], audit.PASS)

    def test_a_readable_ledger_passes(self):
        def write(home):
            (home / "codex-official-identity-runs.json").write_text(
                json.dumps([{"at": "2026-08-30T00:00:00+00:00", "to": "a@example.com"}]),
                encoding="utf-8")
        self.assertEqual(self._verdict(write)[0], audit.PASS)

    def test_a_damaged_ledger_fails_and_names_the_file(self):
        def write(home):
            (home / "grok-official-identity-runs.json").write_text(
                "[{\"at\": ", encoding="utf-8")
        status, _, detail = self._verdict(write)
        self.assertEqual(status, audit.FAIL)
        self.assertIn("grok-official-identity-runs.json", detail)

    def test_mixed_valid_and_invalid_legacy_rows_are_unreadable_as_a_whole(self):
        def write(home):
            (home / "grok-official-identity-runs.json").write_text(
                json.dumps([
                    {"at": "2026-09-04T23:00:00Z", "account_id": "valid"},
                    {"at": "2026-09-04T23:05:00Z"},
                ]),
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write(home)
            self.assertEqual(
                audit._identity_ledger_rows(home / "grok-official-identity-runs.json"),
                [],
            )

        status, _, detail = self._verdict(write)
        self.assertEqual(status, audit.FAIL)
        self.assertIn("grok-official-identity-runs.json", detail)

    def test_an_empty_list_is_readable_not_damaged(self):
        """The distinction is parseability, not content."""
        def write(home):
            (home / "codex-official-identity-runs.json").write_text("[]", encoding="utf-8")
        self.assertEqual(self._verdict(write)[0], audit.PASS)

    def test_malformed_codex_v2_is_rejected_by_the_shared_validator(self):
        def write(home):
            (home / "codex-official-identity-events-v2.json").write_text(
                json.dumps({"schema": 2, "events": [
                    {"at": "2026-08-30T00:00:00Z", "kind": "unassigned"},
                ]}),
                encoding="utf-8",
            )
        status, _, detail = self._verdict(write)
        self.assertEqual(status, audit.FAIL)
        self.assertIn("codex-official-identity-events-v2.json", detail)

    def test_v2_readability_is_independent_of_the_product_validator(self):
        valid = {"schema": 2, "events": [
            {"at": "2026-08-30T00:00:00Z", "kind": "observed", "account_id": "a"},
        ]}
        invalid_cases = [
            {"schema": 2, "events": [
                {"at": "2026-08-30T00:01:00Z", "kind": "observed", "account_id": "a"},
                {"at": "2026-08-30T00:00:00Z", "kind": "observed", "account_id": "b"},
            ]},
            {"schema": 2, "events": [
                {"at": "2026-08-30T00:00:00Z", "kind": "observed", "account_id": "a", "extra": 1},
            ]},
            {"schema": 2, "events": [
                {"at": "2026-08-30T00:00:00Z", "kind": "unassigned", "reason": ""},
            ]},
        ]

        def write(payload):
            def _write(home):
                (home / "codex-official-identity-events-v2.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )
            return _write

        with patch("sandglass.accounts._valid_codex_identity_events", return_value=False):
            self.assertEqual(self._verdict(write(valid))[0], audit.PASS)
        for payload in invalid_cases:
            with self.subTest(payload=payload), patch(
                "sandglass.accounts._valid_codex_identity_events", return_value=True
            ):
                audit._results.clear()
                self.assertEqual(self._verdict(write(payload))[0], audit.FAIL)


class IdentityDiskSnapshotTests(_AuditCase):
    def test_codex_disk_self_proof_reads_the_v2_identity_events_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = home / "codex-official-identity-events-v2.json"
            path.write_text(json.dumps({"schema": 2, "events": []}), encoding="utf-8")
            raw = path.read_bytes()
            stat = path.stat()
            evidence = {
                "stable_snapshot": True,
                "file_sha256": hashlib.sha256(raw).hexdigest(),
                "path_sha256": hashlib.sha256(str(path.resolve()).lower().encode()).hexdigest(),
                "bytes": len(raw),
                "mtime_ns": stat.st_mtime_ns,
                "captured_at": "2026-09-05T00:00:00Z",
            }
            snapshot = {
                "state_home": str(home),
                "readings": {"attribution_diagnostics": {
                    "value": {"identity_runs": {"codex": {"disk": evidence}}}
                }},
            }
            with patch.object(audit, "_live_snapshot", lambda *args, **kwargs: snapshot), patch.object(
                audit, "meter_home", lambda: home
            ):
                audit.audit_identity_disk_snapshot()
        verdicts = {name: status for status, name, _ in audit._results}
        self.assertEqual(verdicts["Codex 身份账本磁盘自证"], audit.PASS)


class ReconciliationBracketTests(_AuditCase):
    """Two readings, or no verdict about the numbers.

    With only one, the bracket collapses onto a point and every token spent
    while the audit ran shows up as the panel being wrong. That is the audit's
    own elapsed time, reported as somebody else's arithmetic error.
    """

    def test_a_single_reading_reports_the_stall_not_a_token_difference(self):
        snapshot = {"readings": {
            "local_windows": [{"at": "2026-09-02T10:00:00+00:00",
                               "value": {"a1|7d": {"total": 5, "days": {}}}}],
            "quota_windows": {"at": "x", "value": {}},
            "product_mode": {"at": "x", "value": {}},
        }}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ), patch.object(audit, "_live_snapshot", lambda *a, **k: snapshot), patch.object(
            audit, "_newer_local_windows", lambda after, **k: None
        ), patch.object(audit, "audit_window_boundaries", lambda: None):
            audit.audit_against_panel([])
        # One line, and it is about the missing reading -- not a per-account
        # token difference. Pinning the sentence itself broke the moment the
        # bracket learned to wait for a reading that covers the recount.
        self.assertEqual([name for _, name, _ in audit._results], ["面板对账"])
        self.assertEqual(self.verdicts(), [audit.FAIL])
        self.assertIn("读数", audit._results[0][2])


class CanonicalStateHomeTests(_AuditCase):
    """An audit that cannot see the books must say so, not report on a copy.

    A Windows app container redirects writes under %LOCALAPPDATA% into its own
    package folder, copy-on-write: the directory still resolves to the spelled
    path, and only a file this process creates shows where its bytes land. Run
    from inside one, every check here reads the audit's own copy, compares it
    against itself and agrees -- 51/51 clean for a whole night, while the app
    outside kept its books somewhere this never opened.
    """

    def test_a_direct_write_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SANDGLASS_HOME": tmp}):
                audit.audit_state_home_is_canonical()
        self.assertEqual(self.verdicts(), [audit.PASS])

    def test_a_redirected_write_fails_from_shared_redacted_measurement(self):
        result = {
            "ok": False,
            "reason": "probe_final_path_mismatch",
            "target_path_sha256": "a" * 64,
            "final_path_sha256": "b" * 64,
        }
        with patch.object(audit, "state_home_attestation", return_value=result):
            audit.audit_state_home_is_canonical()

        status, _, detail = audit._results[0]
        self.assertEqual(status, audit.FAIL)
        self.assertIn("probe_final_path_mismatch", detail)
        self.assertNotIn("redirected", detail)

    def test_the_probe_does_not_stay_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SANDGLASS_HOME": tmp}):
                audit.audit_state_home_is_canonical()
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
