import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sandglass import observer


class ObserverTests(unittest.TestCase):
    def test_coverage_records_clean_and_interrupted_runs_atomically(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "sandglass.observer.meter_home", return_value=Path(tmp)
        ), patch(
            "sandglass.observer._now",
            side_effect=["start-a", "beat-a", "start-b", "end-b", "end-b"],
        ):
            first = observer._begin_coverage()
            observer._heartbeat(first)
            second = observer._begin_coverage()
            observer._heartbeat(second, ended=True)

            payload = json.loads(
                (Path(tmp) / "observer-coverage.json").read_text(encoding="utf-8")
            )

        self.assertEqual(payload["runs"][0]["started_at"], "start-a")
        self.assertEqual(payload["runs"][0]["ended_at"], "beat-a")
        self.assertEqual(payload["runs"][0]["end_reason"], "observer_interrupted")
        self.assertEqual(payload["runs"][1]["started_at"], "start-b")
        self.assertEqual(payload["runs"][1]["ended_at"], "end-b")
        self.assertEqual(payload["runs"][1]["end_reason"], "observer_stopped")

    def test_source_desktop_delegates_observer_before_ui_creation(self):
        source = (Path(__file__).resolve().parents[1] / "sandglass" / "desktop.py").read_text(
            encoding="utf-8"
        )
        main_source = source.split("def main() -> int:", 1)[1]

        self.assertLess(main_source.index('if "--observer" in sys.argv:'), main_source.index("_set_app_user_model_id()"))
        self.assertIn("ensure_observer_running()", source)
        self.assertNotIn("_start_quota_watch(threading.Event(), True)", source)

        serve_source = (
            Path(__file__).resolve().parents[1] / "sandglass" / "serve.py"
        ).read_text(encoding="utf-8")
        self.assertIn('if live_quota and os.name == "nt":', serve_source)
        self.assertIn("ensure_observer_running()", serve_source)
        self.assertIn("monitor.next_poll_delay(_SIGNAL_IDLE_SECONDS)", serve_source)
        self.assertNotIn("_SIGNAL_SECONDS = 1.0", serve_source)


class CodexObservationWiringTests(unittest.TestCase):
    def test_restart_closes_previous_interval_before_observing_same_account(self):
        previous = "2026-09-01T00:00:15+00:00"
        started = "2026-09-01T00:01:00+00:00"
        calls = []

        with patch.object(
            observer,
            "note_codex_identity_gap",
            side_effect=lambda at, reason: calls.append(("gap", at, reason)),
        ) as gap, patch.object(
            observer,
            "codex_identity_events",
            return_value=[{
                "at": previous,
                "kind": "observed",
                "account_id": "same-account",
            }],
        ), patch.object(
            observer,
            "note_codex_identity",
            side_effect=lambda: calls.append(("observed",)),
        ) as observed:
            observer._begin_codex_observation(started, previous)

        self.assertEqual(
            calls,
            [
                ("gap", "2026-09-01T00:00:15.000001+00:00", "observer_interrupted"),
                ("observed",),
            ],
        )
        gap.assert_called_once_with(
            "2026-09-01T00:00:15.000001+00:00",
            reason="observer_interrupted",
        )
        observed.assert_called_once_with()

    def test_first_run_failure_records_codex_scoped_unassigned_state(self):
        with patch.object(
            observer, "note_codex_identity", side_effect=OSError("auth unavailable")
        ), patch.object(observer, "note_codex_identity_gap") as gap:
            observer._begin_codex_observation("2026-09-01T00:01:00+00:00", "")

        gap.assert_called_once_with(reason="identity_observation_error")

    def test_restart_uses_the_previous_run_last_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            observer, "meter_home", return_value=Path(tmp)
        ):
            (Path(tmp) / "observer-coverage.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "runs": [
                            {
                                "started_at": "2026-09-01T00:00:00+00:00",
                                "last_heartbeat_at": "2026-09-01T00:00:15+00:00",
                                "ended_at": "",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                observer._open_run_last_heartbeat(),
                "2026-09-01T00:00:15+00:00",
            )

    def test_late_identity_event_moves_gap_after_it_before_reopening(self):
        previous = "2026-09-01T00:00:15+00:00"
        with patch.object(
            observer,
            "codex_identity_events",
            return_value=[
                {
                    "at": "2026-09-01T00:00:30+00:00",
                    "kind": "observed",
                    "account_id": "same-account",
                }
            ],
        ), patch.object(observer, "note_codex_identity_gap") as gap, patch.object(
            observer, "note_codex_identity"
        ):
            observer._begin_codex_observation(
                "2026-09-01T00:01:00+00:00", previous
            )

        gap.assert_called_once_with(
            "2026-09-01T00:00:30.000001+00:00",
            reason="observer_interrupted",
        )

    def test_invalid_heartbeat_uses_latest_identity_event_as_safe_boundary(self):
        with patch.object(
            observer,
            "codex_identity_events",
            return_value=[
                {
                    "at": "2026-09-01T00:00:30+00:00",
                    "kind": "observed",
                    "account_id": "same-account",
                }
            ],
        ), patch.object(observer, "note_codex_identity_gap") as gap, patch.object(
            observer, "note_codex_identity"
        ):
            observer._begin_codex_observation(
                "2026-09-01T00:01:00+00:00", "invalid-heartbeat"
            )

        gap.assert_called_once_with(
            "2026-09-01T00:00:30.000001+00:00",
            reason="observer_interrupted",
        )

    def test_future_heartbeat_is_bounded_before_current_observation(self):
        previous = "2099-01-01T00:00:15+00:00"
        started = "2026-09-01T00:01:00+00:00"
        calls = []
        with patch.object(
            observer,
            "codex_identity_events",
            return_value=[{
                "at": "2026-09-01T00:00:30+00:00",
                "kind": "observed",
                "account_id": "same-account",
            }],
        ), patch.object(
            observer,
            "note_codex_identity_gap",
            side_effect=lambda at, reason: calls.append(("gap", at, reason)),
        ), patch.object(
            observer,
            "note_codex_identity",
            side_effect=lambda: calls.append(("observed",)),
        ):
            observer._begin_codex_observation(started, previous)

        self.assertEqual(
            calls,
            [
                ("gap", "2026-09-01T00:00:30.000001+00:00", "observer_interrupted"),
                ("observed",),
            ],
        )

    def test_naive_heartbeat_uses_latest_identity_event_without_type_error(self):
        with patch.object(
            observer,
            "codex_identity_events",
            return_value=[{
                "at": "2026-09-01T00:00:30Z",
                "kind": "observed",
                "account_id": "same-account",
            }],
        ), patch.object(observer, "note_codex_identity_gap") as gap, patch.object(
            observer, "note_codex_identity"
        ):
            observer._begin_codex_observation(
                "2026-09-01T00:01:00+00:00", "2026-09-01T00:00:15"
            )

        gap.assert_called_once_with(
            "2026-09-01T00:00:30.000001+00:00",
            reason="observer_interrupted",
        )

    def test_missing_or_empty_coverage_with_owner_closes_after_latest_event(self):
        with patch.object(
            observer,
            "codex_identity_events",
            return_value=[{
                "at": "2026-09-01T00:00:30Z",
                "kind": "observed",
                "account_id": "same-account",
            }],
        ), patch.object(observer, "note_codex_identity_gap") as gap, patch.object(
            observer, "note_codex_identity"
        ):
            observer._begin_codex_observation("2026-09-01T00:01:00Z", "")

        gap.assert_called_once_with(
            "2026-09-01T00:00:30.000001+00:00",
            reason="observer_interrupted",
        )

    def test_latest_event_equal_to_start_bound_has_no_representable_gap(self):
        bound = "2026-09-01T00:01:00+00:00"
        with patch.object(
            observer,
            "codex_identity_events",
            return_value=[{
                "at": bound,
                "kind": "observed",
                "account_id": "same-account",
            }],
        ):
            self.assertEqual(
                observer._codex_gap_boundary(
                    "2026-09-01T00:00:15+00:00", upper_bound=bound
                ),
                "",
            )

    def test_future_identity_event_is_not_used_before_start_bound(self):
        with patch.object(
            observer,
            "codex_identity_events",
            return_value=[{
                "at": "2026-09-01T00:01:00.000001+00:00",
                "kind": "observed",
                "account_id": "same-account",
            }],
        ):
            self.assertEqual(
                observer._codex_gap_boundary(
                    "2026-09-01T00:00:15+00:00",
                    upper_bound="2026-09-01T00:01:00+00:00",
                ),
                "",
            )

    def test_no_existing_owner_does_not_create_a_gap(self):
        with patch.object(observer, "codex_identity_events", return_value=[]), patch.object(
            observer, "note_codex_identity_gap"
        ) as gap, patch.object(observer, "note_codex_identity"):
            observer._begin_codex_observation(
                "2026-09-01T00:01:00Z", "2026-09-01T00:00:15Z"
            )

        gap.assert_not_called()

    def test_stop_waits_for_inflight_identity_before_final_gap(self):
        """The invariant is the handover, not the vendor stack behind it.

        This starts the real quota watch, and the real one re-parses every
        session file and forces a live request to all three vendors. Those ran
        here: an isolated SANDGLASS_HOME collected 8.9 MB of cache.sqlite, a
        merged Grok identity ledger, and quota readings carrying real account
        ids and reset times -- so with the variable unset they went into the
        state directory of the running product, and the requests came out of
        the user's own quota. Neither is what this test is about.
        """
        from sandglass import accounts, serve

        stop = threading.Event()
        entered = threading.Event()
        release = threading.Event()
        order = []

        def blocked_observation():
            order.append("observed_started")
            entered.set()
            release.wait(timeout=5)
            order.append("observed_finished")

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ), patch.object(
            serve, "api_payload", lambda *_, **__: {}
        ), patch.object(
            serve, "attach_live_quota", lambda accounts_, **__: accounts_
        ), patch.object(
            serve, "_refresh_quota_signals", lambda *_, **__: None
        ), patch.object(
            accounts, "note_current_identity", lambda **_: None
        ), patch.object(
            accounts, "note_codex_identity", blocked_observation
        ), patch.object(
            observer, "note_codex_identity_gap",
            side_effect=lambda *_, **__: order.append("final_unassigned"),
        ):
            watchers = serve._start_quota_watch(stop, True)
            self.assertTrue(entered.wait(timeout=5))
            stopper = threading.Thread(
                target=observer._stop_quota_watchers, args=(stop, watchers)
            )
            stopper.start()
            time.sleep(0.1)
            self.assertTrue(stopper.is_alive())
            release.set()
            stopper.join(timeout=5)
            observer.note_codex_identity_gap("2026-09-01T00:00:01Z")
            self.assertFalse(stopper.is_alive())
            leftovers = sorted(p.name for p in Path(tmp).iterdir())

        self.assertEqual(order, ["observed_started", "observed_finished", "final_unassigned"])
        self.assertEqual(leftovers, [], "停掉之后仍然写了产品状态")

    def test_graceful_stop_closes_codex_at_final_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            observer, "meter_home", return_value=Path(tmp)
        ), patch.object(
            observer,
            "_now",
            side_effect=[
                "2026-09-01T00:00:00+00:00",
                "2026-09-01T00:00:15+00:00",
                "2026-09-01T00:00:15+00:00",
            ],
        ), patch.object(observer, "codex_identity_events", return_value=[{
            "at": "2026-09-01T00:00:00Z",
            "kind": "observed",
            "account_id": "same-account",
        }]), patch.object(
            observer, "note_codex_identity_gap"
        ) as gap:
            started = observer._begin_coverage()
            observer._heartbeat(started, ended=True)
            observer._end_codex_observation(started)

        gap.assert_called_once_with(
            "2026-09-01T00:00:15+00:00", reason="observer_stopped"
        )

    def test_graceful_stop_advances_equal_final_observation_by_one_microsecond(self):
        stop_at = "2026-09-01T00:00:15+00:00"
        with patch.object(
            observer, "_run_last_heartbeat", return_value=stop_at
        ), patch.object(observer, "_now", return_value=stop_at), patch.object(
            observer,
            "codex_identity_events",
            return_value=[{
                "at": stop_at,
                "kind": "observed",
                "account_id": "same-account",
            }],
        ), patch.object(observer, "note_codex_identity_gap") as gap:
            observer._end_codex_observation("run")

        gap.assert_called_once_with(
            "2026-09-01T00:00:15.000001+00:00", reason="observer_stopped"
        )

    def test_graceful_stop_persists_unassigned_event_in_v2_stream(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            observer, "meter_home", return_value=Path(tmp)
        ), patch.object(accounts, "meter_home", return_value=Path(tmp)), patch.object(
            observer,
            "_now",
            side_effect=[
                "2026-09-01T00:00:00+00:00",
                "2026-09-01T00:00:15+00:00",
                "2026-09-01T00:00:15+00:00",
            ],
        ):
            accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
            self.addCleanup(
                lambda: setattr(accounts, "_CODEX_RUNS", None)
            )
            self.addCleanup(
                lambda: setattr(accounts, "_CODEX_RUNS_SRC", None)
            )
            accounts._append_codex_identity_event(
                "2026-09-01T00:00:00+00:00", account_id="same-account"
            )
            started = observer._begin_coverage()
            observer._heartbeat(started, ended=True)
            observer._end_codex_observation(started)

            events = accounts.codex_identity_events()

        self.assertEqual(events[-1]["kind"], "unassigned")
        self.assertEqual(events[-1]["reason"], "observer_stopped")

    def test_status_is_observing_only_for_a_fresh_open_run(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "sandglass.observer.meter_home", return_value=Path(tmp)
        ):
            path = Path(tmp) / "observer-coverage.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "runs": [
                            {
                                "started_at": "2099-01-01T00:00:00+00:00",
                                "last_heartbeat_at": "2099-01-01T00:00:00+00:00",
                                "ended_at": "",
                                "end_reason": "",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch("sandglass.observer.datetime") as clock:
                clock.now.return_value = datetime(2099, 1, 1, tzinfo=timezone.utc)
                clock.fromisoformat.side_effect = datetime.fromisoformat
                status = observer.observer_status()

        self.assertEqual(status["state"], "observing")
        self.assertTrue(status["active"])

    def test_status_becomes_stale_only_after_ninety_seconds(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "sandglass.observer.meter_home", return_value=Path(tmp)
        ):
            now = datetime(2099, 1, 1, tzinfo=timezone.utc)
            heartbeat = now - timedelta(seconds=90)
            (Path(tmp) / "observer-coverage.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "runs": [
                            {
                                "started_at": heartbeat.isoformat(),
                                "last_heartbeat_at": heartbeat.isoformat(),
                                "ended_at": "",
                                "end_reason": "",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch("sandglass.observer.datetime") as clock:
                clock.now.return_value = now
                clock.fromisoformat.side_effect = datetime.fromisoformat
                at_limit = observer.observer_status()
                clock.now.return_value = now + timedelta(seconds=1)
                over_limit = observer.observer_status()

        self.assertTrue(at_limit["active"])
        self.assertFalse(over_limit["active"])

    @patch("sandglass.observer.os.name", "nt")
    @patch("sandglass.observer.subprocess.Popen")
    @patch("sandglass.observer.time.sleep")
    @patch("sandglass.observer.request_observer_stop", return_value=True)
    @patch("sandglass.observer.observer_process_exists", return_value=True)
    @patch(
        "sandglass.observer.observer_status",
        return_value={
            "active": False,
            "started_at": "2026-09-01T00:00:00+00:00",
            "last_end_reason": "",
        },
    )
    def test_startup_replaces_a_stale_open_observer(
        self, _status, _exists, request_stop, sleep, popen
    ):
        # True at the first check, still True once inside the wait, gone after.
        _exists.side_effect = [True, True, False]

        self.assertTrue(observer.ensure_observer_running())

        request_stop.assert_called_once_with()
        popen.assert_called_once()

    @patch("sandglass.observer.os.name", "nt")
    @patch("sandglass.observer.subprocess.Popen")
    @patch("sandglass.observer.time.sleep")
    @patch("sandglass.observer.request_observer_stop", return_value=True)
    @patch("sandglass.observer.observer_process_exists", return_value=True)
    @patch(
        "sandglass.observer.observer_status",
        return_value={"active": False, "started_at": "old", "last_end_reason": ""},
    )
    def test_a_holder_that_keeps_the_mutex_gets_no_doomed_replacement(
        self, _status, _exists, _request_stop, _sleep, popen
    ):
        """Starting one anyway is worse than not starting one.

        main() answers ERROR_ALREADY_EXISTS by returning 0, and the child is
        detached onto DEVNULL, so a replacement launched while the mutex is
        still held disappears without a word -- and the launcher counted it as
        done. The old code slept three quarters of a second and launched
        regardless of what had happened in them.
        """
        with patch.object(observer, "_wait_for_mutex_release", return_value=False):
            self.assertFalse(observer.ensure_observer_running())

        popen.assert_not_called()

    @patch("sandglass.observer.os.name", "nt")
    @patch("sandglass.observer.subprocess.Popen")
    @patch("sandglass.observer.request_observer_stop", return_value=False)
    @patch("sandglass.observer.observer_process_exists", return_value=True)
    @patch(
        "sandglass.observer.observer_status",
        return_value={"active": False, "started_at": "old", "last_end_reason": ""},
    )
    def test_a_stale_holder_that_cannot_be_signalled_is_not_raced(
        self, _status, _exists, _request_stop, popen
    ):
        self.assertFalse(observer.ensure_observer_running())

        popen.assert_not_called()

    @patch("sandglass.observer.os.name", "nt")
    def test_the_wait_ends_with_the_mutex_not_after_a_fixed_delay(self):
        calls = {"n": 0}

        def holder_lets_go():
            calls["n"] += 1
            return calls["n"] < 3

        started = time.monotonic()
        with patch.object(observer, "observer_process_exists", holder_lets_go):
            self.assertTrue(observer._wait_for_mutex_release(30.0))

        self.assertLess(time.monotonic() - started, 5.0, "等的是时长而不是互斥量")

    @patch("sandglass.observer.os.name", "nt")
    def test_the_wait_gives_up_and_says_so(self):
        with patch.object(observer, "observer_process_exists", lambda: True):
            self.assertFalse(observer._wait_for_mutex_release(0.05))

    @patch("sandglass.observer.os.name", "nt")
    @patch("sandglass.observer.subprocess.Popen")
    @patch("sandglass.observer.observer_process_exists", return_value=True)
    @patch(
        "sandglass.observer.observer_status",
        return_value={"active": True, "started_at": "now", "last_end_reason": ""},
    )
    def test_startup_keeps_a_fresh_observer(self, _status, _exists, popen):
        observer.ensure_observer_running()

        popen.assert_not_called()

    @patch("sandglass.observer.os.name", "nt")
    @patch("sandglass.observer.subprocess.Popen")
    @patch("sandglass.observer.request_observer_stop")
    @patch("sandglass.observer.observer_process_exists", return_value=False)
    @patch(
        "sandglass.observer.observer_status",
        return_value={"active": True, "started_at": "now", "last_end_reason": ""},
    )
    def test_a_fresh_heartbeat_from_a_process_that_is_gone_still_starts_one(
        self, _status, _exists, request_stop, popen
    ):
        """Killing an observer leaves its heartbeat looking alive for 90 seconds.

        A desktop restarting inside that window read the ledger, concluded an
        observer was already running, and returned -- and it asks once, at
        startup, so nothing observed anything for as long as that desktop lived.
        Presence is a question for the operating system, not for a file the dead
        process wrote.
        """
        observer.ensure_observer_running()

        popen.assert_called_once()
        request_stop.assert_not_called()

    @patch("sandglass.observer.os.name", "nt")
    @patch("sandglass.observer._kernel32")
    def test_stop_requires_explicit_signal_and_closes_the_event_handle(self, kernel_factory):
        kernel = kernel_factory.return_value
        kernel.OpenEventW.return_value = 123
        kernel.SetEvent.return_value = True

        self.assertTrue(observer.request_observer_stop())

        kernel.SetEvent.assert_called_once_with(123)
        kernel.CloseHandle.assert_called_once_with(123)


class CoverageWriteTests(unittest.TestCase):
    def test_a_temp_file_that_will_not_delete_does_not_kill_the_observer(self):
        """The sweep-up is not allowed to be fatal.

        os.replace has already consumed the temp file on the good path, so this
        only bites when a scanner is holding a fresh file -- and on Windows that
        is PermissionError, which the old FileNotFoundError guard let straight
        through. Neither caller has a handler above it: _begin_coverage runs
        before main()'s try, and _heartbeat_locked runs inside its loop.
        """
        real_unlink = Path.unlink

        def refuse(self_path, *args, **kwargs):
            if self_path.name.startswith(".observer-coverage.json."):
                raise PermissionError("held by another process")
            return real_unlink(self_path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(observer, "meter_home", lambda: Path(tmp)),                  patch.object(Path, "unlink", refuse):
                observer._write_coverage({"schema": 1, "runs": [{"started_at": "a"}]})

            written = json.loads((Path(tmp) / "observer-coverage.json").read_text("utf-8"))

        self.assertEqual(written["runs"], [{"started_at": "a"}])

    def test_a_coverage_file_we_cannot_read_is_not_replaced_with_one_run(self):
        """A failed read is not an empty ledger.

        `_read_coverage` treated every OSError as "no file", so a locked
        observer-coverage.json became `{schema:1, runs:[]}` and the next
        `_begin_coverage` wrote a single new run over the top. That is the
        erasure the file lock closed for overlapping observers, reached
        through a failed read -- and this file is the coverage ledger the
        shadow copy made untrustworthy for four rounds.
        """
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = home / "observer-coverage.json"
            original = json.dumps({
                "schema": 1,
                "runs": [
                    {"started_at": "keep-a", "last_heartbeat_at": "keep-a",
                     "ended_at": "keep-a", "end_reason": "observer_stopped"},
                    {"started_at": "keep-b", "last_heartbeat_at": "keep-b",
                     "ended_at": "", "end_reason": ""},
                ],
            })
            path.write_text(original, encoding="utf-8")
            reported = []
            real_read = Path.read_text

            def read_text(self, *args, **kwargs):
                if self.name == "observer-coverage.json":
                    raise PermissionError("observer-coverage.json is locked")
                return real_read(self, *args, **kwargs)

            with patch.object(observer, "meter_home", lambda: home), \
                    patch.object(Path, "read_text", read_text), \
                    patch.object(observer, "record_component_failure",
                                 lambda name, exc: reported.append(name)):
                observer._begin_coverage()
                status = observer.observer_status()

            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual(reported, ["observer_coverage_write"])
            self.assertEqual(status["state"], "unreadable")
            self.assertIs(status["active"], True)

    def test_a_missing_coverage_file_is_still_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch.object(observer, "meter_home", lambda: home):
                started = observer._begin_coverage()
            payload = json.loads(
                (home / "observer-coverage.json").read_text(encoding="utf-8")
            )
        self.assertEqual(payload["runs"][0]["started_at"], started)


class ClosingBoundaryTests(unittest.TestCase):
    """A refused closing write is not a lost one.

    _end_codex_observation discards what the append returns, which reads like
    the silent-drop family and was reported as one. Every reachable refusal is
    correct, and the property that makes it correct is worth pinning rather than
    re-argued: after the attempt, "now" is either unowned or owned by an
    observation newer than the boundary that was refused.

    The refusals are: a damaged file, which the reader already renders as an
    unassigned boundary at the malformed cutoff and which extending would
    destroy; a last event that is already unassigned; and a newer event. Only a
    concurrent writer produces the third, and on Windows every v2 writer is an
    observer process -- serve() delegates to one rather than watching itself,
    and the desktop's startup call passes observe_codex=False.
    """

    def _events(self, home):
        return json.loads(
            (home / "codex-official-identity-events-v2.json").read_text("utf-8")
        )["events"]

    def _write(self, home, events):
        (home / "codex-official-identity-events-v2.json").write_text(
            json.dumps({"schema": 2, "events": events}), encoding="utf-8"
        )

    def test_a_handover_observation_leaves_ownership_backed_not_dangling(self):
        """The case that was reported as a defect."""
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._write(home, [
                {"at": "2026-09-01T00:00:00+00:00", "kind": "observed",
                 "account_id": "acct-a"},
                # the incoming observer got there first
                {"at": "2026-09-01T00:10:00+00:00", "kind": "observed",
                 "account_id": "acct-b"},
            ])
            with patch.object(accounts, "meter_home", lambda: home):
                accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
                refused = accounts.note_codex_identity_gap(
                    "2026-09-01T00:05:00+00:00", reason="observer_stopped")
                accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
                events = self._events(home)

        self.assertFalse(refused, "早于既有事件的边界必须被拒")
        # And the reason the refusal is safe: the newest event is an observation,
        # so the minutes after it are owned by something that was observing.
        self.assertEqual(events[-1]["kind"], "observed")
        self.assertEqual(len(events), 2, "被拒的写入不能留下痕迹")

    def test_a_second_gap_is_refused_because_it_is_already_unassigned(self):
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._write(home, [
                {"at": "2026-09-01T00:00:00+00:00", "kind": "observed",
                 "account_id": "acct-a"},
                {"at": "2026-09-01T00:10:00+00:00", "kind": "unassigned",
                 "reason": "observer_stopped"},
            ])
            with patch.object(accounts, "meter_home", lambda: home):
                accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
                refused = accounts.note_codex_identity_gap(
                    "2026-09-01T00:20:00+00:00", reason="observer_stopped")
                accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None

        self.assertFalse(refused)

    def test_a_boundary_after_the_last_observation_is_written(self):
        """The ordinary stop still closes the interval."""
        from sandglass import accounts

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._write(home, [
                {"at": "2026-09-01T00:00:00+00:00", "kind": "observed",
                 "account_id": "acct-a"},
            ])
            with patch.object(accounts, "meter_home", lambda: home):
                accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
                wrote = accounts.note_codex_identity_gap(
                    "2026-09-01T00:10:00+00:00", reason="observer_stopped")
                accounts._CODEX_RUNS = accounts._CODEX_RUNS_SRC = None
                events = self._events(home)

        self.assertTrue(wrote)
        self.assertEqual(events[-1]["kind"], "unassigned")
        self.assertEqual(events[-1]["reason"], "observer_stopped")


class ObserverWatchdogTests(unittest.TestCase):
    """An observer that dies mid-session has to be replaced without a restart."""

    def setUp(self):
        observer._SUPERVISE_STOP.clear()
        self.addCleanup(observer._SUPERVISE_STOP.clear)

    def test_it_keeps_ensuring_until_told_to_stop(self):
        calls = threading.Semaphore(0)
        with patch.object(observer, "ensure_observer_running", calls.release):
            observer.supervise_observer(interval=0.01)
            for _ in range(3):
                self.assertTrue(calls.acquire(timeout=5), "看门狗应持续检查")
            observer.stop_supervising_observer()
            time.sleep(0.05)
            while calls.acquire(blocking=False):
                pass
            self.assertFalse(calls.acquire(timeout=0.2), "停止监测后不应再重启观测器")

    def test_a_raising_check_does_not_kill_the_watchdog(self):
        seen = threading.Semaphore(0)
        state = {"n": 0}

        def flaky():
            state["n"] += 1
            seen.release()
            if state["n"] == 1:
                raise OSError("transient")

        with patch.object(observer, "ensure_observer_running", flaky):
            observer.supervise_observer(interval=0.01)
            self.assertTrue(seen.acquire(timeout=5))
            self.assertTrue(seen.acquire(timeout=5), "一次异常不该让看门狗退出")
        observer.stop_supervising_observer()

    def test_an_observer_that_was_started_and_never_appeared_is_recorded(self):
        """The only place that can notice a launch that went nowhere.

        The child is detached with its handles on DEVNULL: application control
        killing it on sight, or a crash before it claims the mutex, leaves
        nothing behind. Popen returned, so the launcher counted it as done.
        """
        recorded = threading.Semaphore(0)
        seen: list[tuple] = []

        def record(component, error):
            seen.append((component, type(error).__name__))
            recorded.release()

        with patch.object(observer, "ensure_observer_running", lambda: True), \
             patch.object(observer, "observer_process_exists", lambda: False), \
             patch.object(observer, "clear_component_failure", lambda _: None), \
             patch.object(observer, "record_component_failure", record):
            observer.supervise_observer(interval=0.01)
            self.assertTrue(recorded.acquire(timeout=5), "看门狗应记下这次没起来的启动")
        observer.stop_supervising_observer()

        self.assertEqual(seen[0][0], "observer")

    def test_a_running_observer_clears_the_record(self):
        cleared = threading.Semaphore(0)

        with patch.object(observer, "ensure_observer_running", lambda: False), \
             patch.object(observer, "observer_process_exists", lambda: True), \
             patch.object(observer, "record_component_failure",
                          lambda *_: self.fail("健康的观测器不该被记成失败")), \
             patch.object(observer, "clear_component_failure",
                          lambda _: cleared.release()):
            observer.supervise_observer(interval=0.01)
            self.assertTrue(cleared.acquire(timeout=5))
        observer.stop_supervising_observer()


class CoverageHandoverTests(unittest.TestCase):
    """Two observers overlap for a moment, and the ledger has one row each.

    They both read the file, edit it and write it back. Without a lock the one
    that read first writes the other's run out of existence -- and _heartbeat
    used to return quietly when it could not find its own row, so the observer
    that lost the race beat into nothing for as long as it ran while the ledger
    said it had stopped. That is the shape this machine was found in: a live
    observer process, a coverage ledger frozen at the handover, and a status of
    "stopped".
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        patcher = patch.object(observer, "meter_home", lambda: self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def starts(self):
        return [row["started_at"] for row in observer._read_coverage()["runs"]]

    def test_a_run_erased_by_the_handover_comes_back_on_the_next_beat(self):
        outgoing = observer._begin_coverage()
        stale = observer._read_coverage()          # read before the next run exists
        incoming = observer._begin_coverage()
        observer._write_coverage(stale)            # written after: the race
        self.assertNotIn(incoming, self.starts())

        observer._heartbeat(incoming)
        self.assertIn(incoming, self.starts(), "活着的观测器必须把自己的段补回去")
        self.assertIn(outgoing, self.starts())

    def test_status_recovers_with_it(self):
        observer._begin_coverage()
        stale = observer._read_coverage()
        incoming = observer._begin_coverage()
        observer._write_coverage(stale)
        observer._heartbeat(incoming)
        status = observer.observer_status()
        self.assertTrue(status["active"], f"账本应重新反映在跑的观测器：{status}")

    def test_a_finished_run_is_not_resurrected(self):
        """Only a run still going gets put back; ended means ended."""
        before = len(self.starts())
        observer._heartbeat("2000-01-01T00:00:00+00:00", ended=True)
        self.assertEqual(len(self.starts()), before)

    def test_the_ledger_write_is_serialized(self):
        source = (Path(__file__).resolve().parents[1] / "sandglass" / "observer.py").read_text(
            encoding="utf-8"
        )
        begin = source.split("def _begin_coverage", 1)[1].split("def ", 1)[0]
        self.assertIn("state_file_lock", begin)
        beat = source.split("def _heartbeat(", 1)[1].split("def _heartbeat_locked", 1)[0]
        self.assertIn("state_file_lock", beat)


if __name__ == "__main__":
    unittest.main()
