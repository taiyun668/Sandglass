import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from sandglass.models import Account, SessionRecord, TokenUsage


class AttributionSelfCheckTests(unittest.TestCase):
    def setUp(self):
        from sandglass import live_snapshot, serve

        self.serve = serve
        self.live_snapshot = live_snapshot
        self.old_cache = serve._local_cache
        self.old_role = live_snapshot._ROLE
        live_snapshot.set_role("panel")
        self.tmp = tempfile.TemporaryDirectory()
        self.meter = Path(self.tmp.name)
        self.meter.mkdir(exist_ok=True)
        self.now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        self.account = Account(
            provider="codex", account_id="acct", email="acct@example.com",
            extra={"windows": [{"label": "5h"}]},
        )
        self.minute = self.now - timedelta(minutes=2)
        self.session = SessionRecord(
            provider="codex", session_id="s", path="fixture",
            timeline=[(self.minute.isoformat(), TokenUsage(input_tokens=10, calls=1))],
        )
        day = self.minute.astimezone().date().isoformat()
        self.window = {
            "label": "5h",
            "counted_from": (self.now - timedelta(hours=1)).isoformat(),
            "counted_to": (self.now + timedelta(hours=4)).isoformat(),
            "days": [{"day": day, "spent": 10}],
            "usage": {"total_tokens": 10},
            "boundary_source": "one",
        }
        self.payload = {"accounts": [{
            "provider": "codex", "account_id": "acct", "windows": [self.window],
            "activity": {},
        }]}
        (self.meter / "codex-official-identity-events-v2.json").write_text(
            json.dumps({"schema": 2, "events": [{
                "at": (self.now - timedelta(hours=2)).isoformat(),
                "kind": "observed", "account_id": "acct@example.com",
            }]}), encoding="utf-8"
        )
        self.stamp = (("codex", 1),)
        self.serve._local_cache = {
            "at": time.monotonic(), "payload": self.payload,
            "identity_stamp": self.stamp, "source_stamp": (),
            "inputs": {
                "accounts": [self.account], "sessions": [self.session],
                "mode": "",
            },
        }

    def tearDown(self):
        self.serve._local_cache = self.old_cache
        self.live_snapshot.set_role(self.old_role)
        self.tmp.cleanup()

    def _check(self):
        with patch("sandglass.paths.meter_home", return_value=self.meter), \
             patch("sandglass.diagnostics.meter_home", return_value=self.meter):
            return self.serve._check_attribution_self_check()

    @contextmanager
    def _fresh_heartbeat(self):
        from sandglass import diagnostics

        with diagnostics._ATTRIBUTION_SELF_CHECK_HEARTBEAT_LOCK:
            saved = dict(diagnostics._ATTRIBUTION_SELF_CHECK_HEARTBEAT)
            diagnostics._ATTRIBUTION_SELF_CHECK_HEARTBEAT.update(
                last_ok_at=None, check_count=0
            )
        try:
            yield diagnostics
        finally:
            with diagnostics._ATTRIBUTION_SELF_CHECK_HEARTBEAT_LOCK:
                diagnostics._ATTRIBUTION_SELF_CHECK_HEARTBEAT.clear()
                diagnostics._ATTRIBUTION_SELF_CHECK_HEARTBEAT.update(saved)

    def test_a_boundary_and_vendor_window_fields_are_ignored(self):
        self.window["counted_from"] = (self.now - timedelta(hours=1, seconds=1)).isoformat()
        self.window["counted_to"] = (self.now + timedelta(hours=4, seconds=1)).isoformat()
        self.window["boundary_source"] = "anything-else"
        self.assertTrue(self._check())

    def test_b_total_change_is_recorded(self):
        self.window["usage"]["total_tokens"] = 11
        self.assertFalse(self._check())

    def test_c_day_change_is_recorded_even_when_total_is_same(self):
        self.window["days"][0]["spent"] = 11
        self.assertFalse(self._check())

    def test_d_account_or_window_addition_is_recorded(self):
        added = Account(
            provider="codex", account_id="new", email="new@example.com",
            extra={"windows": [{"label": "5h"}]},
        )
        self.serve._local_cache["inputs"]["accounts"].append(added)
        self.assertFalse(self._check())

    def test_e_existing_failure_is_cleared_after_recovery(self):
        with patch("sandglass.diagnostics.meter_home", return_value=self.meter):
            from sandglass.diagnostics import record_component_failure, runtime_diagnostics
            record_component_failure("attribution_self_check", RuntimeError("bad"))
            self.assertIn("attribution_self_check", runtime_diagnostics()["components"])
            self.assertTrue(self._check())
            self.assertNotIn("attribution_self_check", runtime_diagnostics()["components"])

    def test_f_malformed_disk_does_not_clear_and_records(self):
        path = self.meter / "codex-official-identity-events-v2.json"
        path.write_text("{", encoding="utf-8")
        with patch("sandglass.diagnostics.record_component_failure") as record, \
             patch("sandglass.paths.meter_home", return_value=self.meter), \
             patch("sandglass.accounts.identity_source_stamp", return_value=self.stamp):
            self.assertFalse(self.serve._check_attribution_self_check())
            record.assert_called_once()

    def test_f_unstable_disk_does_not_clear_and_records(self):
        with patch.object(self.serve, "_read_disk_identity_snapshot", return_value={
            "runs": [], "missing": False,
            "evidence": {"readable": True, "stable_snapshot": False},
        }), patch("sandglass.diagnostics.record_component_failure") as record, \
             patch("sandglass.paths.meter_home", return_value=self.meter), \
             patch("sandglass.accounts.identity_source_stamp", return_value=self.stamp):
            self.assertFalse(self.serve._check_attribution_self_check())
            record.assert_called_once()

    def test_f_missing_inputs_does_not_clear_and_records(self):
        self.serve._local_cache["inputs"] = None
        with patch("sandglass.diagnostics.record_component_failure") as record:
            self.assertFalse(self._check())
            record.assert_called_once()

    def test_f_identity_stamp_is_not_a_self_check_dependency(self):
        with patch("sandglass.paths.meter_home", return_value=self.meter), \
             patch("sandglass.diagnostics.meter_home", return_value=self.meter), \
             patch("sandglass.accounts.identity_source_stamp", side_effect=AssertionError), \
             patch("sandglass.diagnostics.clear_component_failure") as clear:
            self.assertTrue(self.serve._check_attribution_self_check())
            clear.assert_called_once_with("attribution_self_check")

    def test_f_disk_owner_disagreement_is_detected(self):
        path = self.meter / "codex-official-identity-events-v2.json"
        path.write_text(json.dumps({"schema": 2, "events": [{
            "at": (self.now - timedelta(hours=2)).isoformat(),
            "kind": "observed", "account_id": "other@example.com",
        }]}), encoding="utf-8")
        self.assertFalse(self._check())

    def test_g_observer_never_executes_or_clears_panel_failure(self):
        self.live_snapshot.set_role("observer")
        with patch("sandglass.diagnostics.record_component_failure") as record, \
             patch("sandglass.diagnostics.clear_component_failure") as clear:
            self.assertFalse(self.serve._check_attribution_self_check())
            record.assert_not_called()
            clear.assert_not_called()

    def test_h_replay_does_not_use_collectors_or_account_cache(self):
        from sandglass import accounts

        with patch.object(self.serve, "collect_all", side_effect=AssertionError), \
             patch.object(self.serve, "load_accounts", side_effect=AssertionError), \
             patch.object(self.serve, "attach_live_quota", side_effect=AssertionError), \
             patch.object(accounts, "codex_identity_runs", side_effect=AssertionError):
            self.assertTrue(self._check())

    def test_i_projection_key_keeps_provider_dimension(self):
        projection = self.serve._attribution_projection({"accounts": [
            {"provider": "codex", "account_id": "same", "windows": [self.window]},
            {"provider": "claude", "account_id": "same", "windows": [self.window]},
        ]})
        self.assertEqual(len(projection), 2)
        self.assertIn(("codex", "same", "5h"), projection)
        self.assertIn(("claude", "same", "5h"), projection)

    def test_j_watcher_stops_on_event(self):
        stop = threading.Event()
        checks = []

        def check():
            checks.append("check")
            stop.set()

        with patch.object(self.serve, "_local_windows_payload", side_effect=AssertionError), \
             patch.object(self.serve, "collect_all", side_effect=AssertionError), \
             patch.object(self.serve, "load_accounts", side_effect=AssertionError), \
             patch.object(self.serve, "attach_live_quota", side_effect=AssertionError), \
             patch.object(self.serve, "_check_attribution_self_check", side_effect=check):
            watcher = self.serve._start_attribution_self_check_watch(stop)
            watcher.join(timeout=2)
        self.assertFalse(watcher.is_alive())
        self.assertEqual(checks, ["check"])

    def test_j_direct_checker_does_not_advance_success_heartbeat(self):
        with self._fresh_heartbeat() as diagnostics:
            self.assertTrue(self._check())
            self.assertEqual(
                diagnostics.attribution_self_check_heartbeat(),
                {"last_ok_at": None, "check_count": 0},
            )

    def test_j_success_heartbeat_is_a_copy_and_failure_does_not_reset_it(self):
        with self._fresh_heartbeat() as diagnostics, patch(
            "sandglass.diagnostics.meter_home", return_value=self.meter
        ):
            diagnostics.record_attribution_self_check_success()
            snapshot = diagnostics.attribution_self_check_heartbeat()
            self.assertEqual(snapshot["check_count"], 1)
            self.assertIsNotNone(snapshot["last_ok_at"])
            snapshot["check_count"] = 99
            diagnostics.record_component_failure("other", RuntimeError("unrelated"))
            self.assertEqual(
                diagnostics.attribution_self_check_heartbeat()["check_count"], 1
            )

    def test_j_observer_watcher_cannot_advance_success_heartbeat(self):
        self.live_snapshot.set_role("observer")
        stop = threading.Event()
        with self._fresh_heartbeat() as diagnostics, patch.object(
            self.serve, "_check_attribution_self_check", side_effect=AssertionError
        ):
            watcher = self.serve._start_attribution_self_check_watch(stop)
            watcher.join(timeout=2)
            self.assertFalse(watcher.is_alive())
            self.assertEqual(
                diagnostics.attribution_self_check_heartbeat(),
                {"last_ok_at": None, "check_count": 0},
            )

    def test_j_real_http_watcher_heartbeat_advances_and_stops(self):
        from sandglass import diagnostics

        interval = 0.05
        with self._fresh_heartbeat() as diagnostics, \
             patch("sandglass.paths.meter_home", return_value=self.meter), \
             patch("sandglass.diagnostics.meter_home", return_value=self.meter), \
             patch.object(self.serve, "_SNAPSHOT_WATCH_SECONDS", interval):
            diagnostics.record_component_failure(
                "preexisting", RuntimeError("keep these bytes")
            )
            diagnostics_bytes = diagnostics.diagnostics_path().read_bytes()
            calls = []

            def check():
                calls.append(time.monotonic())
                return True

            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                partial(self.serve.Handler, since=None, live_quota=False),
            )
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            stop = threading.Event()
            with patch.object(
                self.serve, "_check_attribution_self_check", side_effect=check
            ):
                watcher = self.serve._start_attribution_self_check_watch(stop)

                def read_diagnostics():
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{server.server_port}/api/runtime-diagnostics"
                    ) as response:
                        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
                        return json.loads(response.read().decode("utf-8"))

                try:
                    first = read_diagnostics()
                    deadline = time.monotonic() + 3
                    second = first
                    while second["attribution_self_check"]["check_count"] <= first[
                        "attribution_self_check"
                    ]["check_count"] and time.monotonic() < deadline:
                        time.sleep(interval / 2)
                        second = read_diagnostics()
                    self.assertGreaterEqual(len(calls), 2, "watcher did not call checker")
                    self.assertGreater(
                        second["attribution_self_check"]["check_count"],
                        first["attribution_self_check"]["check_count"],
                        "healthy and dead watcher are indistinguishable: success heartbeat did not advance",
                    )
                    self.assertIsNotNone(second["attribution_self_check"]["last_ok_at"])

                    stop.set()
                    watcher.join(timeout=2)
                    self.assertFalse(watcher.is_alive())
                    time.sleep(interval * 2)
                    frozen = read_diagnostics()
                    time.sleep(interval * 2)
                    frozen_again = read_diagnostics()
                    self.assertEqual(
                        frozen["attribution_self_check"]["check_count"],
                        frozen_again["attribution_self_check"]["check_count"],
                    )
                    self.assertEqual(
                        frozen["attribution_self_check"]["last_ok_at"],
                        frozen_again["attribution_self_check"]["last_ok_at"],
                    )
                    self.assertEqual(diagnostics_bytes, diagnostics.diagnostics_path().read_bytes())
                finally:
                    stop.set()
                    watcher.join(timeout=2)
                    server.shutdown()
                    server.server_close()
                    server_thread.join(timeout=2)

    def test_j_watcher_with_no_payload_waits_silently(self):
        self.serve._local_cache["payload"] = None
        stop = threading.Event()

        def check():
            stop.set()
            self.assertFalse(self.serve._local_cache["payload"])

        with patch.object(self.serve, "_check_attribution_self_check", side_effect=check), \
             patch("sandglass.diagnostics.record_component_failure") as record:
            watcher = self.serve._start_attribution_self_check_watch(stop)
            watcher.join(timeout=2)
        self.assertFalse(watcher.is_alive())
        record.assert_not_called()

    def test_j_watch_context_joins_when_loop_raises(self):
        stop_seen = []

        def check():
            stop_seen.append(True)

        with patch.object(self.serve, "_check_attribution_self_check", side_effect=check):
            with self.assertRaises(RuntimeError):
                with self.serve._attribution_self_check_watch_context():
                    raise RuntimeError("ui loop failed")
        self.assertTrue(stop_seen)

    def test_k_no_codex_is_not_applicable_without_disk_read(self):
        from sandglass import serve

        day = self.now.date().isoformat()
        claude_payload = {"accounts": [{
            "provider": "claude", "account_id": "claude-only", "windows": [{
                "label": "5h", "usage": {"total_tokens": 0},
                "days": [{"day": day, "spent": 0}],
                "counted_from": (self.now - timedelta(hours=1)).isoformat(),
                "counted_to": (self.now + timedelta(hours=4)).isoformat(),
            }], "activity": {},
        }]}
        serve._local_cache = {
            "at": time.monotonic(), "payload": claude_payload,
            "identity_stamp": None, "source_stamp": (), "inputs": {
                "accounts": [], "sessions": [], "mode": "",
            },
        }
        with patch("sandglass.paths.meter_home", return_value=self.meter), \
             patch("sandglass.diagnostics.meter_home", return_value=self.meter), \
             patch.object(serve, "_read_disk_identity_snapshot", side_effect=AssertionError), \
             patch("sandglass.diagnostics.record_component_failure") as record, \
             patch("sandglass.diagnostics.clear_component_failure") as clear:
            self.assertTrue(serve._check_attribution_self_check())
        record.assert_not_called()
        clear.assert_called_once_with("attribution_self_check")


class _ContextProbe:
    def __init__(self):
        self.events = []

    def __enter__(self):
        self.events.append("enter")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.events.append(("exit", exc_type))
        return False


class DesktopLifecycleContextTests(unittest.TestCase):
    def _desktop_common(self):
        from sandglass import desktop

        class FakeEvent:
            def __iadd__(self, callback):
                return self

        class FakePanel:
            def __init__(self):
                self.events = type("Events", (), {
                    "loaded": FakeEvent(), "moved": FakeEvent(),
                })()

        class FakeShell:
            instances = []

            def __init__(self, _url):
                self.native_panel = None
                self.panel = None
                self.orb = type("Orb", (), {"run": lambda _self: None})()
                self.mark = None
                self.icon = None
                self._in_tray = False
                self.stopped = 0
                type(self).instances.append(self)

            def activate(self):
                return None

            def quit(self):
                return True

            def toggle_panel(self):
                return None

            def toggle_orb(self):
                return None

            def orb_drag_started(self):
                return None

            def orb_moved(self, *_args):
                return None

            def activate_when_ready(self):
                return None

            def stop_auxiliary(self):
                self.stopped += 1

            def dress_panel(self, *_args):
                return None

            def panel_moved(self, *_args):
                return None

            def native_minimized(self, *_args):
                return None

            def native_opened(self, *_args):
                return None

            def native_ready(self, *_args):
                return None

            def native_closed(self, *_args):
                return None

            def set_locale(self, *_args):
                return None

            def native_panel_docked(self, *_args):
                return None

            def native_panel_moved(self, *_args):
                return None

        class FakeReceiver:
            instances = []

            def __init__(self, **_kwargs):
                self.closed = 0
                type(self).instances.append(self)

            def enable(self):
                return None

            def close(self):
                self.closed += 1

        return desktop, FakeEvent, FakePanel, FakeShell, FakeReceiver

    def _base_patches(self, desktop, FakeShell, FakeReceiver, probe):
        state_root = Path(self.tmp.name if hasattr(self.tmp, "name") else self.tmp)
        return [
            patch.object(desktop, "require_canonical_state_home", return_value=state_root),
            patch.object(desktop, "sys", **{"argv": ["sandglass-desktop"]}),
            patch.object(desktop, "_set_app_user_model_id"),
            patch.object(desktop, "_claim_single_instance", return_value=object()),
            patch.object(desktop, "_load_state", return_value={}),
            patch.object(desktop, "live_snapshot"),
            patch("sandglass.accounts.note_current_identity"),
            patch("sandglass.observer.ensure_observer_running"),
            patch("sandglass.observer.supervise_observer"),
            patch.object(desktop, "TelemetryReceiver", FakeReceiver),
            patch.object(desktop, "Shell", FakeShell),
            patch.object(desktop, "render", return_value=object()),
            patch.object(desktop, "Orb", return_value=FakeShell("x").orb),
            patch.object(desktop, "_wait_for_orb", return_value=True),
            patch.object(desktop, "_tray", return_value=type("Icon", (), {
                "run_detached": lambda _self: None,
            })()),
            patch.object(desktop, "_initial_orb_geometry", return_value=(1, 2, 56)),
            patch.object(desktop, "_release_single_instance"),
            patch.object(desktop, "clear_component_failure"),
            patch.object(desktop, "record_component_failure"),
            patch.object(desktop, "threading", **{
                "Thread": lambda **kwargs: type("Thread", (), {
                    "start": lambda _self: None,
                })(),
            }),
            patch.object(desktop, "_attribution_self_check_watch_context", return_value=probe),
        ]

    def test_k_native_main_wraps_real_panel_loop_and_cleans_up(self):
        desktop, _Event, _Panel, FakeShell, FakeReceiver = self._desktop_common()
        probe = _ContextProbe()
        failure = RuntimeError("native loop failed")

        class Native:
            def start(self):
                return None

            def run(self):
                raise failure

            def quit(self):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = tmp
            patches = self._base_patches(desktop, FakeShell, FakeReceiver, probe)
            patches.extend([
                patch.object(desktop, "native_shell_path", return_value=Path(tmp) / "native"),
                patch.object(desktop, "NativePanel", return_value=Native()),
            ])
            for item in patches:
                item.start()
            try:
                with self.assertRaises(RuntimeError) as raised:
                    desktop.main()
            finally:
                for item in reversed(patches):
                    item.stop()
        self.assertIs(raised.exception, failure)
        self.assertEqual(probe.events, ["enter", ("exit", RuntimeError)])
        self.assertEqual(FakeShell.instances[-1].stopped, 1)
        self.assertEqual(FakeReceiver.instances[-1].closed, 1)

    def test_k_fallback_main_wraps_real_webview_loop_and_cleans_up(self):
        desktop, FakeEvent, FakePanel, FakeShell, FakeReceiver = self._desktop_common()
        probe = _ContextProbe()
        failure = RuntimeError("webview loop failed")
        httpd = Mock()
        fake_webview = type("Webview", (), {
            "create_window": staticmethod(lambda *args, **kwargs: FakePanel()),
            "start": staticmethod(lambda **kwargs: (_ for _ in ()).throw(failure)),
        })
        with tempfile.TemporaryDirectory() as tmp:
            self.tmp = tmp
            patches = self._base_patches(desktop, FakeShell, FakeReceiver, probe)
            patches.extend([
                patch.object(desktop, "native_shell_path", return_value=None),
                patch.object(desktop, "_bind_dashboard_server", return_value=(httpd, 43210)),
                patch.object(desktop, "_record_fallback_runtime_identity"),
                patch.dict(sys.modules, {"webview": fake_webview}),
            ])
            for item in patches:
                item.start()
            try:
                with self.assertRaises(RuntimeError) as raised:
                    desktop.main()
            finally:
                for item in reversed(patches):
                    item.stop()
        self.assertIs(raised.exception, failure)
        self.assertEqual(probe.events, ["enter", ("exit", RuntimeError)])
        self.assertEqual(FakeShell.instances[-1].stopped, 1)
        self.assertEqual(FakeReceiver.instances[-1].closed, 1)
        httpd.shutdown.assert_called_once_with()
        httpd.server_close.assert_called_once_with()

    def test_k_serve_wraps_real_server_loop_and_cleans_up(self):
        from sandglass import serve

        probe = _ContextProbe()
        failure = RuntimeError("server loop failed")
        httpd = Mock()
        httpd.serve_forever.side_effect = failure
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"SANDGLASS_HOME": tmp}
        ):
            fake_server = Mock(return_value=httpd)
            with patch.object(serve, "require_canonical_state_home"), \
                 patch.object(serve, "ThreadingHTTPServer", fake_server), \
                 patch.object(serve, "record_runtime_identity"), \
                 patch.object(serve, "_attribution_self_check_watch_context", return_value=probe), \
                 patch("sandglass.live_snapshot.set_role"):
                with self.assertRaises(RuntimeError) as raised:
                    serve.serve(open_browser=False, live_quota=False)
        self.assertIs(raised.exception, failure)
        self.assertEqual(probe.events, ["enter", ("exit", RuntimeError)])
        httpd.server_close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
