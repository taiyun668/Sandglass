import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from sandglass.desktop import (
    APP_USER_MODEL_ID,
    DESKTOP_TEXT,
    INSTANCE_MUTEX_NAME,
    PANEL_CHROME,
    NATIVE_APP_URL,
    PanelApi,
    Shell,
    TelemetryReceiver,
    _desktop_api,
    apply_update_request,
    _claim_single_instance,
    _handle_native_panel_failure,
    _initial_orb_geometry,
    _load_state,
    _launcher_command,
    _panel_usable_height,
    _record_fallback_runtime_identity,
    _save_state,
    _signal_update_ready,
    _signal_update_ready_if_window_visible,
    _update_ready_token,
    _set_app_user_model_id,
    _tray,
    _wait_for_orb,
    _webview_start_options,
    main as desktop_main,
)
from sandglass.orb import WM_ORB_ACTIVATE, activate_existing_orb, animate_window_reveal
from sandglass.paths import StateHomeAttestationError


class UpdateReadySignalTests(unittest.TestCase):
    def setUp(self):
        import sandglass.desktop as desktop
        desktop._UPDATE_READY_SENT = False

    def test_signal_sets_only_the_installer_named_event(self):
        class Fn:
            def __init__(self, result):
                self.result = result
                self.calls = []
            def __call__(self, *args):
                self.calls.append(args)
                return self.result

        class Kernel:
            def __init__(self):
                self.opened = []
                self.OpenEventW = Fn(123)
                self.SetEvent = Fn(1)
                self.CloseHandle = Fn(1)

        kernel = Kernel()
        class Open(Fn):
            def __call__(self, *args):
                kernel.opened.append(args)
                return self.result
        kernel.OpenEventW = Open(123)
        with patch.object(sys, "argv", ["Sandglass.exe", "/UPDATE_TOKEN=0123456789abcdef0123456789abcdef"]), \
                patch("sandglass.desktop.ctypes.WinDLL", return_value=kernel):
            self.assertEqual(_update_ready_token(), "0123456789abcdef0123456789abcdef")
            _signal_update_ready()
        self.assertEqual(kernel.opened[0][2], "Local\\Sandglass.UpdateReady.0123456789abcdef0123456789abcdef")
        self.assertEqual(len(kernel.SetEvent.calls), 1)
        self.assertEqual(len(kernel.CloseHandle.calls), 1)

    def test_ready_signal_is_one_shot_within_the_new_desktop_process(self):
        class Fn:
            def __init__(self, result):
                self.result, self.calls = result, []
            def __call__(self, *args):
                self.calls.append(args)
                return self.result

        kernel = type("Kernel", (), {})()
        kernel.OpenEventW = Fn(123)
        kernel.SetEvent = Fn(1)
        kernel.CloseHandle = Fn(1)
        with patch.object(
            sys, "argv",
            ["Sandglass.exe", "/UPDATE_TOKEN=0123456789abcdef0123456789abcdef"],
        ), patch("sandglass.desktop.ctypes.WinDLL", return_value=kernel):
            _signal_update_ready()
            _signal_update_ready()
        self.assertEqual(len(kernel.OpenEventW.calls), 1)
        self.assertEqual(len(kernel.SetEvent.calls), 1)

    def test_malformed_token_is_a_noop_and_cannot_name_a_path(self):
        with patch.object(sys, "argv", ["Sandglass.exe", "/UPDATE_TOKEN=C:\\sensitive\\file"]), \
                patch("sandglass.desktop.ctypes.WinDLL") as win_dll:
            self.assertIsNone(_update_ready_token())
            _signal_update_ready()
        win_dll.assert_not_called()

    def test_without_update_argument_signal_is_a_noop(self):
        with patch.object(sys, "argv", ["Sandglass.exe"]), \
                patch("sandglass.desktop.ctypes.WinDLL") as win_dll:
            _signal_update_ready()
        win_dll.assert_not_called()

    @patch("sandglass.desktop._signal_update_ready")
    @patch("sandglass.desktop.ctypes.windll.user32.IsWindowVisible", return_value=0)
    def test_fallback_does_not_signal_before_windows_reports_visible(
            self, _visible, signal):
        self.assertFalse(_signal_update_ready_if_window_visible(101))
        signal.assert_not_called()


class _Panel:
    def __init__(self):
        self.hidden = 0
        self.shown = 0
        self.resizes = []
        self.destroyed = 0

    def show(self):
        self.shown += 1

    def resize(self, width, height):
        self.resizes.append((width, height))

    def hide(self):
        self.hidden += 1

    def destroy(self):
        self.destroyed += 1


class _Dock:
    def __init__(self):
        self.tucked = False
        self.edge = None
        self.released = 0

    def release(self):
        self.released += 1
        self.tucked = False
        self.edge = None


class _Orb:
    def __init__(self):
        self.shown = 0
        self.hidden = 0
        self.quit = 0
        self.x = 10
        self.y = 20
        self.image = type("Image", (), {"size": (56, 56)})()
        self.dock = _Dock()
        self.visible = True
        self.moves = []

    def post_show(self):
        self.shown += 1
        self.visible = True

    def post_hide(self):
        self.hidden += 1
        self.visible = False

    def post_quit(self):
        self.quit += 1

    def post_move(self, x, y, *, animate=False):
        self.x, self.y = x, y
        self.moves.append((x, y, animate))


class _NativePanel:
    def __init__(self, docked=False):
        self.docked = docked
        self.open = False
        self.shown = []
        self.followed = []
        self.detached = []
        self.activated = 0
        self.tray_hides = 0
        self.minimizes = 0

    def show(self, x, y, side, height):
        self.shown.append((x, y, side, height))
        self.open = True
        return True

    def activate(self):
        self.activated += 1

    def hide_to_tray(self):
        self.tray_hides += 1
        self.open = False

    def minimize(self):
        self.minimizes += 1
        self.open = False

    def is_docked(self):
        return self.docked

    def follow_orb(self, x, y, side):
        self.followed.append((x, y, side))

    def detach_to_orb(self, x, y, side):
        self.detached.append((x, y, side))


class _Icon:
    def __init__(self):
        self.menu_updates = 0
        self.stopped = 0

    def update_menu(self):
        self.menu_updates += 1

    def stop(self):
        self.stopped += 1


class _TrayShell:
    def __init__(self):
        self.mark = None
        self.calls = []

    def text(self, key):
        return key

    def show_panel(self):
        self.calls.append("open_panel")

    def toggle_orb(self):
        self.calls.append("toggle_orb")

    def toggle_autostart(self):
        self.calls.append("autostart")

    def quit(self):
        self.calls.append("quit")

    def stop_monitoring(self):
        self.calls.append("stop_monitoring")


class UpdateApplyRequestTests(unittest.TestCase):
    def test_client_offer_fields_cannot_bypass_missing_authoritative_offer(self):
        shell = Mock()
        forged = {
            "offer": {
                "version": "9.9.9",
                "manifest_signed": True,
                "url": "https://evil.example/setup.exe",
                "sha256": "0" * 64,
            }
        }
        with patch("sandglass.update.available_update", return_value={}) as check, \
                patch("sandglass.update.apply_update") as apply:
            result = apply_update_request(shell, json.dumps(forged))

        check.assert_called_once_with(force=True)
        apply.assert_not_called()
        shell.quit.assert_not_called()
        self.assertFalse(result["ok"])

    def test_revalidation_version_mismatch_does_not_apply_or_quit(self):
        shell = Mock()
        with patch("sandglass.update.available_update",
                   return_value={"version": "9.9.8"}) as check, \
                patch("sandglass.update.apply_update") as apply:
            result = apply_update_request(shell, json.dumps({"version": "9.9.9"}))

        check.assert_called_once_with(force=True)
        apply.assert_not_called()
        shell.quit.assert_not_called()
        self.assertEqual(result, {"ok": False, "error": "stale_update"})

    def test_success_uses_revalidated_offer_and_quits_once(self):
        shell = Mock()
        shell.quit.return_value = True
        authoritative = {
            "version": "9.9.9",
            "asset": "Sandglass-9.9.9-windows-x64-setup.exe",
            "url": "https://github.com/taiyun668/Sandglass/setup.exe",
            "sha256": "a" * 64,
            "manifest_signed": False,
        }
        # The legacy envelope is accepted only for its version; the forged
        # fields must not reach apply_update.
        body = {"offer": {
            "version": "9.9.9",
            "asset": "evil.exe",
            "url": "https://evil.example/evil.exe",
            "sha256": "0" * 64,
            "manifest_signed": True,
        }}
        with patch("sandglass.update.available_update",
                   return_value=authoritative) as check, \
                patch("sandglass.update.apply_update",
                      return_value={"ok": True, "version": "9.9.9"}) as apply:
            result = apply_update_request(shell, json.dumps(body))

        check.assert_called_once_with(force=True)
        apply.assert_called_once_with(authoritative)
        shell.quit.assert_called_once_with()
        self.assertEqual(result["version"], "9.9.9")

    def test_failed_desktop_shutdown_cancels_waiting_installer_and_reports_failure(self):
        shell = Mock()
        shell.quit.return_value = False
        authoritative = {"version": "9.9.9"}
        internal = {
            "ok": True, "version": "9.9.9", "_process": object(),
            "_pending_record": {"version": "9.9.9"},
        }
        with patch("sandglass.update.available_update",
                   return_value=authoritative), \
                patch("sandglass.update.apply_update", return_value=internal), \
                patch("sandglass.update.cancel_update_handoff",
                      return_value=True) as cancel:
            result = apply_update_request(shell, json.dumps({"version": "9.9.9"}))

        shell.quit.assert_called_once_with()
        cancel.assert_called_once_with(internal)
        self.assertEqual(result, {"ok": False, "error": "desktop_quit_failed"})


class DesktopWindowControlTests(unittest.TestCase):
    def setUp(self):
        self.shell = Shell("http://127.0.0.1:7740/")
        self.shell.panel = _Panel()
        self.shell.panel.native = type("Native", (), {
            "Handle": type("Handle", (), {"ToInt64": lambda self: 101})(),
        })()
        self.shell.orb = _Orb()
        self.api = PanelApi(self.shell)
        self._visible_patch = patch(
            "sandglass.desktop.ctypes.windll.user32.IsWindowVisible",
            return_value=1,
        )
        self._visible_patch.start()
        self.addCleanup(self._visible_patch.stop)

    @patch("sandglass.desktop._signal_update_ready")
    def test_native_ready_signal_is_emitted_only_from_ready_callback(self, signal):
        self.shell.native_ready()
        signal.assert_called_once_with()

    @patch("sandglass.desktop._signal_update_ready_if_window_visible")
    @patch("sandglass.desktop._save_state")
    def test_fallback_ready_signal_follows_a_successful_visible_show(self, _save, signal):
        signal.side_effect = lambda hwnd: self.assertEqual(self.shell.panel.shown, 1) or True
        self.shell.show_panel()
        signal.assert_called_once_with(101)

    @patch("sandglass.desktop.clamp_to_screen", side_effect=lambda x, y, side: (x, y))
    @patch("sandglass.desktop.monitor_scale", return_value=1.0)
    @patch("sandglass.desktop.primary_work_area", return_value=(0, 0, 1920, 1040))
    @patch("sandglass.desktop._default_orb_pos", return_value=(1824, 944))
    def test_startup_orb_uses_the_work_area_corner_not_the_last_drag(
            self, default_pos, _work, monitor_scale, _clamp):
        for state in ({}, {"orb_x": 1101, "orb_y": 220}):
            with self.subTest(state=state):
                default_pos.reset_mock()
                self.assertEqual(_initial_orb_geometry(state), (1824, 944, 56))
                default_pos.assert_called_once_with(56)
        monitor_scale.assert_called_with(1919, 1039)

    @patch("sandglass.desktop.clamp_to_screen", side_effect=lambda x, y, side: (x, y))
    @patch("sandglass.desktop.monitor_scale", return_value=1.5)
    @patch("sandglass.desktop.primary_work_area", return_value=(0, 0, 3840, 2088))
    @patch("sandglass.desktop._default_orb_pos", return_value=(3716, 1964))
    def test_startup_orb_is_sized_for_the_primary_monitor(
            self, default_pos, _work, monitor_scale, _clamp):
        self.assertEqual(
            _initial_orb_geometry({"orb_x": 2700, "orb_y": 200}),
            (3716, 1964, 84),
        )
        default_pos.assert_called_once_with(84)
        monitor_scale.assert_called_once_with(3839, 2087)

    @patch("sandglass.desktop.clamp_to_screen", return_value=(100, 100))
    @patch("sandglass.desktop.monitor_scale", return_value=1.0)
    @patch("sandglass.desktop.primary_work_area", return_value=(0, 0, 800, 600))
    @patch("sandglass.desktop._default_orb_pos", return_value=(-10, -10))
    def test_startup_orb_is_clamped_to_the_live_work_area(
            self, default_pos, _work, _scale, clamp):
        self.assertEqual(_initial_orb_geometry({}), (100, 100, 56))
        default_pos.assert_called_once_with(56)
        clamp.assert_called_once_with(-10, -10, 56)

    @patch("sandglass.desktop.window_scale", return_value=1.0)
    @patch("sandglass.desktop.window_rect", return_value=(3000, 100, 380, 800))
    @patch("sandglass.desktop.monitor_work_area", return_value=(2560, 0, 4480, 1350))
    def test_panel_height_uses_its_secondary_monitor_work_area(
            self, monitor_work_area, _window_rect, _window_scale):
        self.assertEqual(_panel_usable_height(101, self.shell.orb), 1310)
        monitor_work_area.assert_called_once_with(3000, 100, 380, 800)

    @patch("sandglass.desktop.window_scale", return_value=1.5)
    @patch("sandglass.desktop.window_rect", return_value=(100, 100, 570, 1200))
    @patch("sandglass.desktop.monitor_work_area", return_value=(0, 0, 3840, 2088))
    def test_panel_height_converts_primary_work_area_to_logical_pixels(
            self, _monitor_work_area, _window_rect, _window_scale):
        self.assertEqual(_panel_usable_height(101, self.shell.orb), 1352)

    def test_state_loader_rejects_json_scalars_and_lists_before_save(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch("sandglass.desktop.meter_home", return_value=home):
                for raw in ("[]", '"wrong shape"', "0", "null"):
                    (home / "desktop.json").write_text(raw, encoding="utf-8")
                    self.assertEqual(_load_state(), {})

                _save_state(panel_x=120, panel_y=240)
                saved = json.loads((home / "desktop.json").read_text(encoding="utf-8"))

        self.assertEqual(saved, {"panel_x": 120, "panel_y": 240})

    @patch("sandglass.desktop.clear_component_failure")
    @patch("sandglass.desktop.record_component_failure")
    def test_a_state_save_we_cannot_do_is_reported_not_dropped(
        self, record, clear
    ):
        """`except OSError: pass` here discarded the user's panel state in silence.

        A read-only state directory, a locked file or a full disk took window
        position, size and which panel was open, and said nothing anywhere.
        Still not raised -- these saves are opportunistic and must not take the
        panel down -- but now it is a named component failure, the same way an
        identity ledger write and the diagnostics file already are.
        """
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch("sandglass.desktop.meter_home", return_value=home),                     patch("sandglass.desktop.os.replace",
                          side_effect=OSError(13, "state home is read-only")):
                _save_state(panel_x=120)

            self.assertEqual([call.args[0] for call in record.call_args_list],
                             ["desktop_state_write"])
            self.assertEqual(clear.call_args_list, [])

            with patch("sandglass.desktop.meter_home", return_value=home):
                _save_state(panel_x=121)

            self.assertEqual([call.args[0] for call in clear.call_args_list],
                             ["desktop_state_write"])

    def test_a_state_file_we_cannot_read_is_not_replaced_with_one_key(self):
        """A failed read is not empty state.

        `_load_state` treated every OSError as "no file", so a locked or
        briefly unreadable desktop.json became `{}` and the next save wrote
        only the new keys over the top. That is a wipe, and it reports
        nothing: the write itself succeeded. The concurrent test's missing
        key with `replace 调用 8 次` and no recorded failure is that shape.
        """
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = home / "desktop.json"
            original = json.dumps({"keep": 1, "also": 2})
            path.write_text(original, encoding="utf-8")
            reported = []
            real_read = Path.read_text

            def read_text(self, *args, **kwargs):
                if self.name == "desktop.json":
                    raise PermissionError("desktop.json is locked")
                return real_read(self, *args, **kwargs)

            with patch("sandglass.desktop.meter_home", return_value=home), \
                    patch.object(Path, "read_text", read_text), \
                    patch("sandglass.desktop.record_component_failure",
                          lambda name, exc: reported.append(name)), \
                    patch("sandglass.desktop.clear_component_failure",
                          lambda name: None):
                _save_state(new=3)

            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual(reported, ["desktop_state_write"])

    def test_concurrent_state_updates_keep_all_keys_and_replace_atomically(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            replace_calls = []
            errors = []
            barrier = threading.Barrier(8)
            real_replace = os.replace

            def replace(source, destination):
                replace_calls.append((Path(source), Path(destination)))
                return real_replace(source, destination)

            def writer(index):
                try:
                    barrier.wait(timeout=5)
                    _save_state(**{f"concurrent_{index}": index})
                except BaseException as exc:  # noqa: BLE001 - report thread failures
                    errors.append(exc)

            reported = []
            with patch("sandglass.desktop.meter_home", return_value=home), \
                    patch("sandglass.desktop.os.replace", side_effect=replace), \
                    patch("sandglass.desktop.record_component_failure",
                          lambda name, exc: reported.append((name, repr(exc)))), \
                    patch("sandglass.desktop.clear_component_failure",
                          lambda name: None):
                threads = [threading.Thread(target=writer, args=(index,))
                           for index in range(8)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=30)
                    # join() returning is not the writer having finished. A
                    # loaded machine made one of these still be waiting on the
                    # file lock, and reading the file here then reported the
                    # missing key as a lost write -- which is the defect this
                    # test exists to catch, so it has to be able to tell them
                    # apart. Seen once in a full suite on 2026-09-06.
                    self.assertFalse(
                        thread.is_alive(),
                        "写入线程还没结束,下面读到的不是最终状态",
                    )

                saved = json.loads((home / "desktop.json").read_text(encoding="utf-8"))

        self.assertEqual(errors, [])
        # Say what was found, not just that one key was missing. This failed
        # twice in full suites -- concurrent_7 once, concurrent_0 once -- and
        # the bare KeyError left nothing to tell a single lost write from a
        # file that had been wiped and rebuilt, nor to say whether the write
        # reported itself. Both are answerable, so answer them here.
        present = {
            int(value) for key, value in saved.items()
            if key.startswith("concurrent_")
        }
        self.assertEqual(
            present, set(range(8)),
            f"写入丢了。文件里有 {sorted(present)}；"
            f"报出来的写入失败 {reported}；"
            f"replace 调用 {len(replace_calls)} 次",
        )
        self.assertEqual(reported, [], "有写入失败被报出来,它就是丢掉的那一次")
        self.assertEqual(len(replace_calls), 8)
        for source, destination in replace_calls:
            self.assertTrue(source.name.startswith(".desktop.json."))
            self.assertTrue(source.name.endswith(".tmp"))
            self.assertEqual(destination, home / "desktop.json")

    @patch("sandglass.desktop.clear_component_failure")
    @patch("sandglass.desktop.record_component_failure")
    def test_orb_timeout_is_recorded_and_startup_can_continue(
            self, record_failure, clear_failure):
        orb = Mock()
        orb.wait_ready.return_value = False

        self.assertIs(_wait_for_orb(orb), False)

        orb.wait_ready.assert_called_once_with()
        record_failure.assert_called_once()
        self.assertEqual(record_failure.call_args.args[0], "orb")
        self.assertIsInstance(record_failure.call_args.args[1], TimeoutError)
        clear_failure.assert_not_called()

    @patch("sandglass.desktop.clear_component_failure")
    @patch("sandglass.desktop.record_component_failure")
    def test_orb_ready_clears_previous_failure(
            self, record_failure, clear_failure):
        orb = Mock()
        orb.wait_ready.return_value = True

        self.assertIs(_wait_for_orb(orb), True)

        clear_failure.assert_called_once_with("orb")
        record_failure.assert_not_called()

    def test_close_hides_panel_and_leaves_only_tray(self):
        self.api.close_panel()

        self.assertEqual(self.shell.panel.hidden, 1)
        self.assertEqual(self.shell.orb.hidden, 1)
        self.assertEqual(self.shell.orb.shown, 0)

    def test_named_mutex_replaces_the_single_instance_listener_port(self):
        source = (Path(__file__).resolve().parents[1] / "sandglass" / "desktop.py").read_text(
            encoding="utf-8"
        )

        self.assertEqual(INSTANCE_MUTEX_NAME, r"Local\Sandglass.Desktop.SingleInstance")
        self.assertNotIn("LOCK_PORT", source)
        self.assertNotIn("socket.socket", source)
        self.assertIn("on_activate=shell.activate", source)
        self.assertIn("on_click=shell.toggle_panel", source)
        self.assertEqual(NATIVE_APP_URL, "https://sandglass.local/index.html")
        self.assertIn("api_request=lambda method, target, body: _desktop_api", source)

    @patch("sandglass.desktop.shell32")
    def test_desktop_sets_stable_windows_app_identity(self, shell32):
        shell32.SetCurrentProcessExplicitAppUserModelID.return_value = 0

        _set_app_user_model_id()

        self.assertEqual(APP_USER_MODEL_ID, "Ayun.Sandglass.Desktop")
        shell32.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with(
            APP_USER_MODEL_ID
        )

    @patch("sandglass.desktop.kernel32")
    def test_existing_named_mutex_is_closed_and_reported(self, kernel32):
        kernel32.CreateMutexW.return_value = 123
        kernel32.GetLastError.return_value = 183

        self.assertIsNone(_claim_single_instance())
        kernel32.CloseHandle.assert_called_once_with(123)

    def test_second_launch_posts_dedicated_activate_message(self):
        user32 = Mock()
        user32.FindWindowW.return_value = 456
        user32.PostMessageW.return_value = True

        with patch("sandglass.orb.user32", user32):
            self.assertIs(activate_existing_orb(timeout=0), True)

        user32.FindWindowW.assert_called_once_with("SandglassOrb", "sandglass")
        user32.PostMessageW.assert_called_once_with(456, WM_ORB_ACTIVATE, 0, 0)

    @patch("sandglass.desktop.activate_existing_orb")
    @patch("sandglass.desktop.runtime_identity_matches", return_value=True)
    @patch("sandglass.desktop.runtime_provenance", return_value={"running": True})
    @patch("sandglass.desktop._expected_runtime_identity", return_value={"current": True})
    @patch("sandglass.desktop._claim_single_instance", return_value=None)
    @patch("sandglass.desktop.sys.argv", ["sandglass-desktop"])
    @patch("sandglass.desktop.require_canonical_state_home", return_value=Path("C:/test-state"))
    def test_second_desktop_launch_wakes_matching_instance(
            self, _attest, _claim, expected, actual, matches, activate):
        self.assertEqual(desktop_main(), 0)
        activate.assert_called_once_with()
        matches.assert_called_once_with(expected.return_value, actual.return_value)

    @patch("sandglass.desktop.activate_existing_orb")
    @patch("sandglass.desktop.runtime_identity_matches", return_value=True)
    @patch("sandglass.desktop.runtime_provenance", return_value={"running": True})
    @patch("sandglass.desktop._expected_runtime_identity", return_value={"current": True})
    @patch("sandglass.desktop._claim_single_instance", return_value=None)
    @patch("sandglass.desktop.sys.argv", ["sandglass-desktop", "--background"])
    @patch("sandglass.desktop.require_canonical_state_home", return_value=Path("C:/test-state"))
    def test_background_second_launch_does_not_open_existing_panel(
            self, _attest, _claim, expected, actual, matches, activate):
        self.assertEqual(desktop_main(), 0)
        activate.assert_not_called()
        matches.assert_called_once_with(expected.return_value, actual.return_value)

    @patch("sandglass.desktop._warn_different_runtime")
    @patch("sandglass.desktop.activate_existing_orb")
    @patch("sandglass.desktop.runtime_identity_matches", return_value=False)
    @patch("sandglass.desktop.runtime_provenance", return_value={})
    @patch("sandglass.desktop._expected_runtime_identity", return_value={"current": True})
    @patch("sandglass.desktop._claim_single_instance", return_value=None)
    @patch("sandglass.desktop.sys.argv", ["sandglass-desktop"])
    @patch("sandglass.desktop.require_canonical_state_home", return_value=Path("C:/test-state"))
    def test_second_desktop_launch_rejects_unidentified_or_different_instance(
            self, _attest, _claim, _expected, _actual, _matches, activate, warn):
        self.assertEqual(desktop_main(), 5)
        activate.assert_not_called()
        warn.assert_called_once_with()

    @patch("sandglass.desktop._claim_single_instance")
    @patch("sandglass.desktop.require_canonical_state_home",
           side_effect=StateHomeAttestationError("blocked"))
    @patch("sandglass.desktop.sys.argv", ["sandglass-desktop", "--help"])
    def test_help_cannot_reach_mutex_when_state_home_is_unproven(
            self, _attest, claim):
        self.assertEqual(desktop_main(), 2)
        claim.assert_not_called()

    def test_activate_keeps_an_already_open_panel_open(self):
        self.shell._panel_open = True

        self.shell.activate()

        self.assertEqual(self.shell.panel.hidden, 0)
        self.assertEqual(self.shell.panel.shown, 1)
        self.assertIs(self.shell._panel_open, True)

    def test_activation_before_native_panel_is_ready_is_replayed(self):
        with TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ):
            self.shell.panel = None

            self.shell.activate()
            self.assertIs(self.shell._activation_pending, True)

            native = _NativePanel()
            self.shell.native_panel = native
            self.shell.activate_when_ready()

        self.assertIs(self.shell._activation_pending, False)
        self.assertEqual(native.shown, [(10, 20, 56, 1040)])

    def test_minimize_hides_panel_and_restores_orb(self):
        self.api.minimize_panel()

        self.assertEqual(self.shell.panel.hidden, 1)
        self.assertEqual(self.shell.orb.shown, 1)
        self.assertEqual(self.shell.orb.hidden, 0)

    @patch("sandglass.desktop._save_state")
    @patch("sandglass.desktop.clamp_to_screen", side_effect=lambda x, y, side: (x, y))
    @patch("sandglass.desktop._default_orb_pos", return_value=(1824, 944))
    def test_tray_open_restores_native_panel_from_the_work_area_corner(
            self, default_pos, _clamp, _save):
        native = _NativePanel()
        self.shell.native_panel = native
        self.shell.orb.x, self.shell.orb.y = 400, 200

        self.shell.native_closed()
        self.shell.show_panel()

        default_pos.assert_called_once_with(56)
        self.assertEqual(self.shell.orb.hidden, 1)
        self.assertEqual(self.shell.orb.shown, 1)
        self.assertEqual(self.shell.orb.moves, [(1824, 944, False)])
        self.assertEqual(native.shown, [(1824, 944, 56, 1040)])
        self.assertIs(self.shell._panel_open, True)
        self.assertIs(self.shell._in_tray, False)

    @patch("sandglass.desktop._save_state")
    @patch("sandglass.desktop._default_orb_pos")
    def test_open_from_a_visible_orb_does_not_jump_to_the_corner(self, default_pos, _save):
        native = _NativePanel()
        self.shell.native_panel = native
        self.shell.orb.x, self.shell.orb.y = 400, 200

        self.shell.show_panel()

        default_pos.assert_not_called()
        self.assertEqual(native.shown, [(400, 200, 56, 1040)])
        self.assertEqual(self.shell.orb.moves, [])

    @patch("sandglass.desktop._save_state")
    def test_tray_click_while_open_raises_the_native_panel(self, _save):
        native = _NativePanel()
        self.shell.native_panel = native
        self.shell._panel_open = True

        self.shell.show_panel()

        self.assertEqual(native.activated, 1)
        self.assertEqual(native.shown, [])
        self.assertIs(self.shell._panel_open, True)

    @patch("sandglass.desktop._save_state")
    @patch("sandglass.desktop.clamp_to_screen", side_effect=lambda x, y, side: (x, y))
    @patch("sandglass.desktop._default_orb_pos", return_value=(1824, 944))
    def test_close_to_tray_then_open_does_not_wait_for_the_closed_callback(
            self, default_pos, _clamp, _save):
        """hide_panel used to leave _panel_open True until on_closed.

        A tray click in that gap took the already-open branch and did nothing,
        then on_closed hid the orb anyway.
        """
        native = _NativePanel()
        self.shell.native_panel = native
        self.shell._panel_open = True
        self.shell.orb.x, self.shell.orb.y = 400, 200

        self.shell.hide_panel(show_orb=False)
        self.assertFalse(self.shell._panel_open)
        self.assertTrue(self.shell._in_tray)
        self.assertEqual(native.tray_hides, 1)

        self.shell.show_panel()

        default_pos.assert_called_once_with(56)
        self.assertEqual(native.shown, [(1824, 944, 56, 1040)])
        self.assertIs(self.shell._panel_open, True)

    @patch("sandglass.desktop.clear_window_region")
    @patch("sandglass.desktop.animate_window_reveal")
    @patch.object(Shell, "_panel_hwnd", return_value=101)
    def test_minimize_morphs_panel_into_orb_before_hiding(
            self, _hwnd, animate, clear_region):
        self.api.minimize_panel()

        animate.assert_called_once_with(101, (10, 20, 56, 56), opening=False)
        self.assertEqual(self.shell.orb.shown, 1)
        self.assertEqual(self.shell.panel.hidden, 1)
        clear_region.assert_called_once_with(101)

    @patch("sandglass.desktop._save_state")
    @patch("sandglass.desktop.clear_window_region")
    @patch("sandglass.desktop.animate_window_reveal")
    @patch("sandglass.desktop.set_window_reveal")
    @patch("sandglass.desktop.hide_from_taskbar")
    @patch("sandglass.desktop.place_beside")
    @patch.object(Shell, "_panel_hwnd", return_value=101)
    def test_orb_click_morphs_orb_into_positioned_panel(
            self, _hwnd, place, hide_taskbar, set_reveal, animate, _clear, _save):
        with patch.object(self.shell.panel_dock, "start"):
            self.shell.show_panel()

        place.assert_called_once_with(101, 10, 20, 56)
        hide_taskbar.assert_called_once_with(101)
        set_reveal.assert_called_once_with(101, (10, 20, 56, 56), 0.0)
        animate.assert_called_once_with(101, (10, 20, 56, 56), opening=True)
        self.assertEqual(self.shell.panel.shown, 1)
        self.assertEqual(self.shell.panel.resizes, [(380, 1040)])
        self.assertEqual(self.shell.orb.hidden, 0)
        self.assertIs(self.shell._panel_open, True)

    def test_persistent_orb_toggles_open_panel_closed(self):
        self.shell._panel_open = True

        self.shell.toggle_panel()

        self.assertEqual(self.shell.panel.hidden, 1)
        self.assertEqual(self.shell.orb.hidden, 0)
        self.assertEqual(self.shell.orb.shown, 1)

    def test_open_native_panel_follows_orb_drag(self):
        native = _NativePanel()
        self.shell.native_panel = native
        self.shell._panel_open = True

        self.shell.orb_moved(140, 260)

        self.assertEqual(native.followed, [(140, 260, 56)])

    def test_dragging_orb_detaches_and_closes_docked_native_panel(self):
        native = _NativePanel(docked=True)
        self.shell.native_panel = native
        self.shell._panel_open = True

        self.shell.orb_drag_started()

        self.assertEqual(native.detached, [(10, 20, 56)])

    def test_dragging_orb_while_panel_is_hidden_releases_old_monitor_dock(self):
        native = _NativePanel(docked=True)
        self.shell.native_panel = native
        self.shell._panel_open = False

        self.shell.orb_drag_started()

        self.assertEqual(native.detached, [(10, 20, 56)])

    @patch("sandglass.desktop.system_scale", return_value=1.0)
    def test_docked_panel_parks_orb_beside_visible_edge(self, _scale):
        self.shell.native_panel_docked("left", False, (0, 30, 380, 600))
        self.assertEqual(self.shell.orb.moves, [(18, 574, True)])

    @patch("sandglass.desktop.system_scale", return_value=1.0)
    def test_tucking_panel_keeps_orb_at_same_edge_point(self, _scale):
        self.shell.native_panel_docked("left", False, (0, 30, 380, 600))
        self.shell.native_panel_docked("left", True, (-372, 30, 380, 600))

        self.assertEqual(self.shell.orb.moves, [
            (18, 574, True),
            (18, 574, True),
        ])

    @patch("sandglass.desktop.system_scale", return_value=1.0)
    def test_tucking_on_the_secondary_left_edge_keeps_the_orb_there(self, _scale):
        """A tucked rect is mostly on the neighbour. Selecting the monitor
        from that rectangle parks the orb on the wrong screen."""
        left = (0, 0, 2560, 1392)
        right = (2560, 0, 5120, 1392)

        def work_area(x, y, width=1, height=1):
            best, area = None, -1
            for work in (left, right):
                overlap = max(0, min(x + width, work[2]) - max(x, work[0]))
                if overlap > area:
                    best, area = work, overlap
            return best

        with patch("sandglass.desktop.monitor_work_area", side_effect=work_area):
            self.shell.native_panel_docked("left", False, (2560, 30, 380, 600))
            self.shell.native_panel_docked("left", True, (2188, 30, 380, 600))

        self.assertEqual(self.shell.orb.moves, [
            (2578, 574, True),
            (2578, 574, True),
        ])

    @patch.object(Shell, "_queue_panel_position")
    @patch("sandglass.desktop.system_scale", return_value=1.0)
    def test_dragging_free_panel_carries_orb_beside_it(self, _scale, _queue):
        self.shell.native_panel_moved((100, 50, 380, 600))

        self.assertEqual(self.shell.orb.moves, [(490, 594, False)])

    @patch("sandglass.desktop._save_state")
    @patch.object(Shell, "_move_orb_beside_panel")
    def test_panel_move_debounces_and_flushes_the_final_position(
            self, move_orb, save_state):
        class DeferredTimer:
            instances = []

            def __init__(self, _delay, target, args=()):
                self.target = target
                self.args = args
                self.started = False
                self.cancelled = False
                self.instances.append(self)

            def start(self):
                self.started = True

            def cancel(self):
                self.cancelled = True

            def is_alive(self):
                return self.started and not self.cancelled

        # Pin the scale: without it this asserts on whatever the developer's
        # display happens to be set to, which is the class of defect this whole
        # round has been removing.
        with patch("sandglass.desktop.threading.Timer", DeferredTimer),              patch("sandglass.desktop.monitor_scale", lambda *_a: 1.5):
            self.shell.native_panel_moved((100, 50, 380, 600))
            self.shell.native_panel_moved((120, 70, 380, 600))
            # logical, from pywebview -- stored as the physical 210, 135
            self.shell.panel_moved(140, 90)

            save_state.assert_not_called()
            self.assertEqual(len(DeferredTimer.instances), 3)
            self.assertTrue(DeferredTimer.instances[0].cancelled)
            self.assertTrue(DeferredTimer.instances[1].cancelled)
            DeferredTimer.instances[2].target(*DeferredTimer.instances[2].args)

        save_state.assert_called_once_with(panel_x=210, panel_y=135)
        self.assertEqual(move_orb.call_count, 2)

    @patch("sandglass.desktop.system_scale", return_value=1.0)
    def test_releasing_panel_away_from_edge_keeps_orb_with_it(self, _scale):
        self.shell.native_panel_docked(None, False, (100, 50, 380, 600))

        self.assertEqual(self.shell.orb.moves, [(490, 594, True)])

    @patch("sandglass.desktop.monitor_work_area", return_value=(2560, 0, 4480, 1350))
    @patch("sandglass.desktop.system_scale", return_value=1.0)
    def test_secondary_monitor_dock_keeps_orb_on_that_monitor(self, _scale, _work):
        self.shell.native_panel_docked("right", False, (4100, 30, 380, 600))

        self.assertEqual(self.shell.orb.moves, [(4406, 574, True)])

    @patch("sandglass.desktop.monitor_work_area", return_value=(2560, 0, 4480, 1350))
    @patch.object(Shell, "_queue_panel_position")
    @patch("sandglass.desktop.system_scale", return_value=1.0)
    def test_secondary_monitor_free_panel_keeps_orb_beside_it(self, _scale, _queue, _work):
        self.shell.native_panel_moved((4000, 50, 380, 600))

        self.assertEqual(self.shell.orb.moves, [(4390, 594, False)])

    @patch("sandglass.desktop._save_state")
    def test_quit_closes_webview_before_main_loop_stops_auxiliary_surfaces(self, _save):
        icon = _Icon()
        self.shell.icon = icon

        self.shell.quit()

        self.assertEqual(self.shell.panel.destroyed, 1)
        self.assertEqual(self.shell.orb.quit, 0)
        self.assertEqual(icon.stopped, 0)

        self.shell.stop_auxiliary()

        self.assertEqual(self.shell.orb.quit, 1)
        self.assertEqual(icon.stopped, 1)

    @patch("sandglass.desktop._save_state")
    def test_quit_reports_destroy_failure_instead_of_claiming_handoff(self, _save):
        self.shell.panel.destroy = Mock(side_effect=RuntimeError("destroy failed"))

        self.assertFalse(self.shell.quit())

        self.assertFalse(self.shell._quitting)
        self.shell.panel.destroy.assert_called_once_with()

    def test_a_failed_show_does_not_turn_the_orb_into_a_dead_switch(self):
        """One failure must not cost the rest of the session.

        The flag was set before the native show could fail, and the orb is the
        only switch: toggle_panel reads it, so every later click took the hide
        branch, minimize() on a panel that was never shown fires no
        on_minimized, and the flag stayed True with nothing on screen. The orb
        swallows what show() raises, so nothing reported it either.
        """
        recorded = []
        self.shell.native_panel = _NativePanel()
        self.shell.native_panel.show = Mock(side_effect=OSError("no webview"))

        with TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ), patch("sandglass.desktop.record_component_failure",
                 lambda name, exc: recorded.append(name)),              patch("sandglass.desktop.clear_component_failure", lambda _n: None):
            with self.assertRaises(OSError):
                self.shell.show_panel()

            self.assertFalse(self.shell._panel_open,
                             "显示失败之后不能把面板记成开着的")
            self.assertEqual(recorded, ["native_panel_show"])

            # The next click must still try to open, not silently take the hide
            # branch for the rest of the session.
            self.shell.native_panel.show = Mock(return_value=True)
            self.shell.toggle_panel()
            self.shell.native_panel.show.assert_called_once()
            self.assertTrue(self.shell._panel_open)

    def test_a_failed_fallback_show_is_reported_and_does_not_turn_the_orb_into_a_dead_switch(self):
        """The native path records a failed show. The pywebview path swallowed it.

        The flag is already not set on this path, so the next click retries --
        that half of the native finding does not apply here. The orb still
        swallows what this returns, so a failed show used to look like a
        quiet click with nothing in diagnostics.
        """
        recorded = []
        self.shell.panel.show = Mock(side_effect=OSError("webview show failed"))

        with TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ), patch("sandglass.desktop._save_state"), patch(
            "sandglass.desktop.record_component_failure",
            lambda name, exc: recorded.append(name),
        ), patch("sandglass.desktop.clear_component_failure", lambda _n: None):
            self.shell.show_panel()

            self.assertFalse(self.shell._panel_open,
                             "显示失败之后不能把面板记成开着的")
            self.assertEqual(recorded, ["fallback_panel_show"])

            self.shell.panel.show = Mock()
            self.shell.toggle_panel()
            self.shell.panel.show.assert_called_once()
            self.assertTrue(self.shell._panel_open)

    def test_a_failed_fallback_raise_does_not_turn_the_tray_into_a_dead_switch(self):
        """Tray click on an already-open panel is raise, not toggle.

        `_raise_panel` swallowed a failed show and left `_panel_open` True,
        so every later tray click took the raise branch and did nothing.
        The orb's toggle would hide first and recover; the tray never would.
        """
        recorded = []
        self.shell._panel_open = True
        self.shell.panel.show = Mock(side_effect=OSError("webview show failed"))

        with TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ), patch("sandglass.desktop._save_state"), patch(
            "sandglass.desktop.record_component_failure",
            lambda name, exc: recorded.append(name),
        ), patch("sandglass.desktop.clear_component_failure", lambda _n: None):
            self.shell.show_panel()

            self.assertFalse(self.shell._panel_open,
                             "唤起失败之后不能把面板记成开着的")
            self.assertEqual(recorded, ["fallback_panel_show"])

            self.shell.panel.show = Mock()
            self.shell.show_panel()
            self.shell.panel.show.assert_called_once()
            self.assertTrue(self.shell._panel_open)

    def test_a_failed_native_raise_does_not_turn_the_tray_into_a_dead_switch(self):
        recorded = []
        native = _NativePanel()
        native.activate = Mock(side_effect=OSError("dispatcher gone"))
        self.shell.native_panel = native
        self.shell._panel_open = True

        with TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ), patch("sandglass.desktop._save_state"), patch(
            "sandglass.desktop.record_component_failure",
            lambda name, exc: recorded.append(name),
        ), patch("sandglass.desktop.clear_component_failure", lambda _n: None):
            self.shell.show_panel()

            self.assertFalse(self.shell._panel_open,
                             "唤起失败之后不能把面板记成开着的")
            self.assertEqual(recorded, ["native_panel_show"])
            self.assertEqual(native.shown, [])

            native.activate = Mock()
            native.show = Mock(return_value=True)
            self.shell.show_panel()
            native.show.assert_called_once()
            self.assertTrue(self.shell._panel_open)

    @patch("sandglass.desktop._save_state")
    def test_a_deferred_native_show_does_not_turn_the_orb_into_a_dead_switch(self, _save):
        """show() can return before the window exists.

        The first orb click almost always queues until the page measures.
        Treating that as open made hide() a no-op: native close requires
        self.open, on_minimized never ran, and the flag stayed True.
        """
        native = _NativePanel()
        native.show = Mock(return_value=False)
        self.shell.native_panel = native

        self.shell.show_panel()

        self.assertFalse(self.shell._panel_open)
        self.shell.toggle_panel()
        self.assertEqual(native.show.call_count, 2)

    def test_the_saved_panel_origin_is_one_unit_not_two(self):
        """Two writers, two units, one field.

        The native shell reports GetWindowRect, which is physical; pywebview's
        moved event is logical. Measured on a 150% display: the same window is
        physical (1200, 300) and logical (800, 200), so a position written by
        one path and read by the other lands 600px away. Physical is stored --
        it is absolute, and the same number means the same place on whichever
        screen the window is restored.
        """
        saved = {}
        with patch("sandglass.desktop._save_state",
                   lambda **kw: saved.update(kw)),              patch("sandglass.desktop.monitor_scale", lambda *_a: 1.5):
            # the native path already has physical pixels
            self.shell.native_panel_moved((1200, 300, 380, 560))
            self.shell._flush_panel_position()
            self.assertEqual((saved["panel_x"], saved["panel_y"]), (1200, 300))

            saved.clear()
            # pywebview hands over logical ones for the same window
            self.shell.panel_moved(800, 200)
            self.shell._flush_panel_position()
            self.assertEqual((saved["panel_x"], saved["panel_y"]), (1200, 300),
                             "两条路径写的必须是同一个量")

    def test_the_stored_origin_is_handed_back_as_logical(self):
        """pywebview places by logical, so the one reader converts."""
        from sandglass.desktop import _logical_panel_origin

        with patch("sandglass.desktop.monitor_scale", lambda *_a: 1.5):
            self.assertEqual(
                _logical_panel_origin({"panel_x": 1200, "panel_y": 300}),
                {"x": 800, "y": 200},
            )
        # Nothing stored stays nothing, rather than becoming a corner.
        self.assertEqual(_logical_panel_origin({}), {"x": None, "y": None})

    def test_quit_is_idempotent(self):
        with TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SANDGLASS_HOME": tmp}
        ):
            self.shell.quit()
            self.shell.quit()

        self.assertEqual(self.shell.panel.destroyed, 1)

    def test_page_locale_updates_desktop_text_and_tray_menu(self):
        icon = _Icon()
        self.shell.icon = icon

        self.api.set_locale("en-US")

        self.assertEqual(self.shell.locale, "en-US")
        self.assertEqual(self.shell.text("open_panel"), "Open panel")
        self.assertEqual(icon.menu_updates, 1)

    @patch("sandglass.desktop.autostart_enabled", return_value=False)
    def test_panel_reports_autostart_without_changing_it(self, _enabled):
        self.assertIs(self.api.autostart_status(), False)

    @patch("sandglass.desktop.autostart_enabled", return_value=True)
    @patch("sandglass.desktop.set_autostart")
    def test_panel_enables_autostart_only_after_explicit_api_call(self, set_start, _enabled):
        icon = _Icon()
        self.shell.icon = icon

        result = self.api.enable_autostart()

        self.assertIs(result, True)
        set_start.assert_called_once_with(True)
        self.assertEqual(icon.menu_updates, 1)

    def test_supported_desktop_locales_have_complete_copy(self):
        expected = {
            "open_panel", "toggle_orb", "autostart", "quit", "stop_monitoring"
        }

        for locale in (
            "zh-CN", "zh-TW", "en-US", "es-ES", "fr-FR",
            "de-DE", "pt-BR", "ru-RU", "ja-JP", "ko-KR",
        ):
            self.assertEqual(set(DESKTOP_TEXT[locale]), expected)
            self.assertIn(locale, PANEL_CHROME)

    def test_panel_height_changes_are_animated_and_respect_reduced_motion(self):
        self.assertIn("var animateHeight = function (target)", PANEL_CHROME)
        self.assertIn("var duration = 180", PANEL_CHROME)
        self.assertIn("prefers-reduced-motion: reduce", PANEL_CHROME)
        self.assertIn("cancelAnimationFrame(resizeFrame)", PANEL_CHROME)

    def test_desktop_outer_scrollbar_is_hidden_without_disabling_scroll(self):
        self.assertIn("overflow-y:auto!important", PANEL_CHROME)
        self.assertIn("scrollbar-width:none!important", PANEL_CHROME)
        self.assertIn("html::-webkit-scrollbar,body::-webkit-scrollbar", PANEL_CHROME)

    def test_unknown_locale_falls_back_to_chinese(self):
        self.api.set_locale("it-IT")

        self.assertEqual(self.shell.locale, "zh-CN")
        self.assertEqual(self.shell.text("quit"), "关闭界面")

    def test_webview_preferences_persist_only_under_sandglass_home(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch("sandglass.desktop.meter_home", return_value=home):
                options = _webview_start_options()

        self.assertEqual(options["gui"], "edgechromium")
        self.assertIs(options["private_mode"], False)
        self.assertEqual(Path(options["storage_path"]), home / "webview")

    def test_frozen_desktop_autostart_relaunches_the_packaged_executable(self):
        with patch("sandglass.desktop.sys.executable", r"C:\Program Files\Sandglass\Sandglass.exe"), \
                patch("sandglass.desktop.sys.frozen", True, create=True):
            command = _launcher_command()
            background = _launcher_command(background=True)

        self.assertEqual(command, '"C:\\Program Files\\Sandglass\\Sandglass.exe"')
        self.assertEqual(
            background,
            '"C:\\Program Files\\Sandglass\\Sandglass.exe" --background',
        )

    def test_direct_launch_opens_panel_while_autostart_is_explicitly_background(self):
        source = (Path(__file__).resolve().parents[1] / "sandglass" / "desktop.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('start_visible = "--background" not in sys.argv', source)
        self.assertIn("shell.activate()", source)
        self.assertIn("shell._in_tray = True", source)
        self.assertIn("start_visible=start_visible", source)
        self.assertIn("_launcher_command(background=True)", source)
        self.assertIn('window_icon=WEB_DIR / "assets" / "orb.ico"', source)

    def test_desktop_startup_only_discovers_codex_without_attributing_usage(self):
        source = (Path(__file__).resolve().parents[1] / "sandglass" / "desktop.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("note_current_identity(observe_codex=False)", source)

    def test_codex_discovery_only_does_not_append_an_observation_event(self):
        from sandglass import accounts
        from sandglass.models import Account

        current = Account(provider="codex", account_id="codex-a")
        with patch.object(
            accounts, "_claude_signed_in", return_value=("", "", None)
        ), patch.object(accounts, "claude_identity_runs", return_value=[]), patch.object(
            accounts, "_codex_signed_in", return_value=("2026-09-01T00:00:00Z", "codex-a")
        ), patch.object(accounts, "_official_codex_account", return_value=current), patch.object(
            accounts, "_remember_codex_account"
        ) as remember, patch.object(accounts, "_append_codex_identity_event") as event, patch.object(
            accounts, "_grok_signed_in", return_value=("", "")
        ), patch.object(accounts, "grok_identity_runs", return_value=[]), patch.object(
            accounts, "_append_run", return_value=False
        ):
            accounts.note_current_identity(observe_codex=False)

        remember.assert_called_once()
        event.assert_not_called()

    @patch("sandglass.desktop.autostart_enabled", return_value=False)
    def test_every_tray_action_accepts_pystray_callback_arguments(self, _enabled):
        shell = _TrayShell()
        icon = _tray(shell)

        for item in icon.menu.items:
            if item is not None:
                item(icon)

        self.assertEqual(
            shell.calls,
            ["open_panel", "toggle_orb", "autostart", "quit", "stop_monitoring"],
        )

    def test_tray_left_click_accepts_vista_select_as_well_as_mouse_up(self):
        source = (Path(__file__).resolve().parents[1] / "sandglass" / "desktop.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("NIN_SELECT", source)
        self.assertIn("NIN_KEYSELECT", source)
        self.assertIn("class TrayIcon", source)

    def test_the_orb_stays_visible_while_the_panel_is_open(self):
        self.shell._panel_open = True
        self.shell.orb.visible = True

        self.shell.toggle_orb()

        self.assertEqual(self.shell.orb.hidden, 0)
        self.assertTrue(self.shell.orb.visible)

    @patch("sandglass.desktop._save_state")
    @patch("sandglass.desktop.clamp_to_screen", side_effect=lambda x, y, side: (x, y))
    @patch("sandglass.desktop._default_orb_pos", return_value=(1824, 944))
    def test_showing_the_orb_from_the_tray_parks_at_the_corner(
            self, default_pos, _clamp, _save):
        self.shell.native_closed()
        self.assertTrue(self.shell._in_tray)
        self.assertFalse(self.shell.orb.visible)

        self.shell.toggle_orb()

        default_pos.assert_called_once_with(56)
        self.assertEqual(self.shell.orb.moves, [(1824, 944, False)])
        self.assertTrue(self.shell.orb.visible)
        self.assertIs(self.shell._in_tray, False)


class DesktopStartupRecoveryTests(unittest.TestCase):
    def test_failed_native_panel_is_quit_before_fallback_discard(self):
        shell = Mock()
        panel = Mock()
        shell.native_panel = panel
        failure = RuntimeError("startup failed")

        with patch("sandglass.desktop.record_component_failure") as record, \
                patch("sandglass.desktop.print") as print_failure:
            _handle_native_panel_failure(shell, failure)

        panel.quit.assert_called_once_with()
        record.assert_called_once_with("native_panel", failure)
        print_failure.assert_called_once()
        self.assertIsNone(shell.native_panel)

    @patch("sandglass.desktop.record_runtime_identity")
    @patch("sandglass.desktop.build_web_runtime_identity", return_value={"runtime_role": "web_server"})
    def test_fallback_records_web_identity_after_dashboard_binding(
            self, build_identity, record_identity):
        _record_fallback_runtime_identity()

        build_identity.assert_called_once_with(
            source_root=Path(__file__).resolve().parents[1],
            web_index=Path(__file__).resolve().parents[1] / "sandglass" / "web" / "index.html",
        )
        record_identity.assert_called_once_with({"runtime_role": "web_server"})


class NativeTransitionTests(unittest.TestCase):
    @patch("sandglass.orb.clear_window_region")
    @patch("sandglass.orb.animations_enabled", return_value=False)
    def test_panel_orb_transition_respects_reduced_motion(self, _enabled, clear):
        animate_window_reveal(101, (10, 20, 56, 56), opening=True)

        clear.assert_called_once_with(101)


class NativeComponentDiagnosticsTests(unittest.TestCase):
    def test_native_startup_records_failure_and_success_clears_it(self):
        source = (Path(__file__).resolve().parents[1] / "sandglass" / "desktop.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('record_component_failure("native_panel", exc)', source)
        self.assertIn('clear_component_failure("native_panel")', source)
        self.assertIn('record_component_failure("fallback_panel", exc)', source)
        self.assertIn('clear_component_failure("fallback_panel")', source)


class TelemetryReceiverTests(unittest.TestCase):
    def test_receiver_is_disabled_until_explicit_enable_and_exposes_no_dashboard(self):
        receiver = TelemetryReceiver(0)
        self.assertEqual(receiver.status()["state"], "disabled")

        try:
            self.assertTrue(receiver.enable())
            status = receiver.status()
            self.assertEqual(status["state"], "ready")
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(
                    str(status["endpoint"]).replace("/v1/logs", "/api/quota"),
                    timeout=3,
                )
            self.assertEqual(raised.exception.code, 404)
        finally:
            receiver.close()

    def test_explicit_native_enable_is_persisted_only_in_sandglass_home(self):
        receiver = TelemetryReceiver(0)
        try:
            with TemporaryDirectory() as tmp, patch(
                "sandglass.desktop.meter_home", return_value=Path(tmp)
            ):
                result = _desktop_api(
                    receiver,
                    "POST",
                    "/api/telemetry-receiver",
                    '{"enabled":true}',
                )
                saved = json.loads((Path(tmp) / "desktop.json").read_text(encoding="utf-8"))

            self.assertTrue(result["ok"])
            self.assertEqual(result["receiver"]["state"], "ready")
            self.assertIs(saved["telemetry_receiver_enabled"], True)

            with TemporaryDirectory() as tmp, patch(
                "sandglass.desktop.meter_home", return_value=Path(tmp)
            ):
                result = _desktop_api(
                    receiver,
                    "POST",
                    "/api/telemetry-receiver",
                    '{"enabled":false}',
                )
                saved = json.loads((Path(tmp) / "desktop.json").read_text(encoding="utf-8"))

            self.assertTrue(result["ok"])
            self.assertEqual(result["receiver"]["state"], "disabled")
            self.assertIs(saved["telemetry_receiver_enabled"], False)
        finally:
            receiver.close()

    def test_provider_enable_is_independent_and_persisted(self):
        receiver = TelemetryReceiver(0)
        try:
            with TemporaryDirectory() as tmp, patch(
                "sandglass.desktop.meter_home", return_value=Path(tmp)
            ):
                first = _desktop_api(
                    receiver,
                    "POST",
                    "/api/telemetry-receiver",
                    '{"enabled":true,"provider":"claude"}',
                )
                saved = json.loads((Path(tmp) / "desktop.json").read_text(encoding="utf-8"))
                self.assertTrue(first["ok"])
                self.assertTrue(first["enabled"])
                self.assertEqual(first["receiver"]["providers"], {
                    "claude": True, "codex": False, "grok": False,
                })
                self.assertEqual(saved["telemetry_providers_enabled"], {
                    "claude": True, "codex": False, "grok": False,
                })

                second = _desktop_api(
                    receiver,
                    "POST",
                    "/api/telemetry-receiver",
                    '{"enabled":true,"provider":"grok"}',
                )
                self.assertTrue(second["receiver"]["providers"]["claude"])
                self.assertTrue(second["receiver"]["providers"]["grok"])
                self.assertFalse(second["receiver"]["providers"]["codex"])

                third = _desktop_api(
                    receiver,
                    "POST",
                    "/api/telemetry-receiver",
                    '{"enabled":false,"provider":"claude"}',
                )
                self.assertFalse(third["enabled"])
                self.assertFalse(third["receiver"]["providers"]["claude"])
                self.assertTrue(third["receiver"]["providers"]["grok"])
        finally:
            receiver.close()

    def test_native_panel_routes_user_source_configuration(self):
        receiver = TelemetryReceiver(0)
        expected = {"ok": True, "product_use_stage": "displayed"}
        with patch("sandglass.desktop.configure_user_source", return_value=expected) as call:
            result = _desktop_api(
                receiver,
                "POST",
                "/api/user-sources/configure",
                '{"source":"example.tool","display_enabled":true}',
            )
        self.assertEqual(result, expected)
        call.assert_called_once_with(
            {"source": "example.tool", "display_enabled": True}
        )


if __name__ == "__main__":
    unittest.main()
