import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sandglass.native_panel import (
    NativePanel,
    load_native_bridge,
    native_bridge_path,
    native_shell_path,
)
from sandglass.resources import WEB_DIR


ROOT = Path(__file__).resolve().parents[1]


class NativePanelTests(unittest.TestCase):
    def panel(self):
        return NativePanel(
            "http://127.0.0.1:7740/",
            WEB_DIR / "assets" / "logo-mark.png",
            '"pythonw.exe" "sandglass-desktop.pyw"',
            on_minimized=Mock(), on_closed=Mock(), on_locale=Mock(),
            window_icon=WEB_DIR / "assets" / "orb.ico",
        )

    def test_show_waits_for_page_measurement_before_opening(self):
        panel = self.panel()
        panel.ready = True

        self.assertEqual(panel.hwnd, 0)

        opened = panel.show(1200, 700, 84, 560)

        self.assertFalse(opened)
        self.assertEqual(panel.pending_show, (1200, 700, 84, 560))

    def test_minimize_cancels_a_deferred_show(self):
        panel = self.panel()
        panel.ready = True
        panel.show(1200, 700, 84, 560)
        panel.minimize()
        self.assertIsNone(panel.pending_show)

    def test_hide_to_tray_releases_an_edge_dock(self):
        panel = self.panel()
        panel.dock = Mock()
        panel.window = SimpleNamespace(Hide=Mock(), Opacity=1.0)
        panel.frame = SimpleNamespace(CacheMode="cached")
        panel.open = True
        panel.animating = True
        panel._clear_animations = Mock()

        panel._hide_to_tray()

        panel.dock.release.assert_called_once_with()
        panel.window.Hide.assert_called_once_with()
        panel.on_closed.assert_called_once_with()
        self.assertFalse(panel.open)
        self.assertFalse(panel.animating)

    def test_show_while_already_open_raises_instead_of_replaying_open(self):
        panel = self.panel()
        panel.open = True
        panel.animating = False
        panel._activate = Mock()
        panel._set_orb_geometry = Mock()

        panel._show(100, 200, 56, 560)

        panel._activate.assert_called_once_with()
        panel._set_orb_geometry.assert_not_called()

    def test_show_while_closing_waits_then_opens_from_finish_close(self):
        panel = self.panel()
        panel.animating = True
        panel.open = False
        panel._set_orb_geometry = Mock()

        panel._show(1824, 944, 56, 1040)

        self.assertEqual(panel.pending_show, (1824, 944, 56, 1040))
        panel._set_orb_geometry.assert_not_called()

        panel.window = SimpleNamespace(Hide=Mock(), Opacity=1.0)
        panel.frame = SimpleNamespace(CacheMode="cached")
        panel._clear_animations = Mock()
        panel._apply_pending_height = Mock()
        panel._show = Mock()

        panel._finish_close()

        panel.on_minimized.assert_called_once_with()
        panel._show.assert_called_once_with(1824, 944, 56, 1040)

    def test_a_late_close_animation_does_not_restore_the_orb_after_tray_hide(self):
        panel = self.panel()
        panel.window = SimpleNamespace(Hide=Mock(), Opacity=1.0)
        panel.frame = SimpleNamespace(CacheMode="cached")
        panel._clear_animations = Mock()
        panel._apply_pending_height = Mock()
        panel.dock = Mock()
        panel._hide_to_tray()
        panel.on_closed.assert_called_once_with()

        panel._finish_close()

        panel.on_minimized.assert_not_called()

    def test_quit_requested_during_startup_stops_before_wpf_main_loop(self):
        panel = self.panel()
        panel.quitting = True
        panel.app = Mock()
        panel.window = Mock()
        panel._build_window = Mock()

        panel._thread_main()

        panel.app.Shutdown.assert_called_once_with()
        panel.app.Run.assert_not_called()
        self.assertTrue(panel._started.is_set())
        self.assertTrue(panel._stopped.is_set())

    def test_first_page_measurement_opens_at_final_height(self):
        panel = self.panel()
        panel.ready = True
        panel.pending_show = (1200, 700, 84, 560)
        panel._clamp_height = Mock(return_value=777.0)
        panel._show = Mock()

        panel._fit("777")

        self.assertTrue(panel.has_measured_height)
        self.assertEqual(panel.panel_height, 777.0)
        self.assertIsNone(panel.pending_show)
        panel._show.assert_called_once_with(1200, 700, 84, 560)

    @patch("sandglass.native_panel.monitor_work_area",
           return_value=(2560, 0, 4480, 1350))
    @patch("sandglass.native_panel.window_rect",
           return_value=(-32000, -32000, 380, 560))
    def test_first_open_height_cap_follows_the_orb_not_the_hidden_hwnd(
            self, _rect, work_area):
        """The hidden HWND is created on the primary monitor at -32000.

        Clamping against that HWND clipped a panel opening beside an orb on
        the secondary to the primary's work area. _show stores the orb's
        work area first; _clamp_height must not re-query the HWND.
        """
        panel = self.panel()
        panel.hwnd = 101
        panel.has_measured_height = True
        panel.panel_height = 2000.0
        panel.dock = None
        panel.window = SimpleNamespace(
            Width=380, Height=2000, ShowInTaskbar=True, Opacity=0,
            IsVisible=False, Show=Mock(), Activate=Mock(),
        )
        panel._place_next_to_orb = Mock()
        panel._update_geometry = Mock()
        panel._prepare_open = Mock(return_value=False)
        panel._finish_open = Mock()

        panel._show(2700, 150, 56, 2000)

        self.assertEqual(panel.panel_height, 1310.0)
        self.assertEqual(panel.window.Height, 1310.0)
        work_area.assert_called_with(2700, 150, 56, 56)
        _rect.assert_not_called()

    def test_visible_panel_grows_upward_instead_of_clipping_tall_page(self):
        panel = self.panel()
        panel.window = SimpleNamespace(IsVisible=True, Top=1100.0, Height=240.0)
        panel.open = True
        panel.panel_height = 240.0
        panel.scale = SimpleNamespace(ScaleX=0.0, ScaleY=0.0)
        panel.clip = SimpleNamespace(RadiusX=0.0, RadiusY=0.0)
        panel._current_work_area = Mock(return_value=(0.0, 0.0, 2560.0, 1392.0))
        panel._update_geometry = Mock()

        panel._apply_height(560.0)

        self.assertEqual(panel.panel_height, 560.0)
        self.assertEqual(panel.window.Height, 560.0)
        self.assertEqual(panel.window.Top, 822.0)
        panel._update_geometry.assert_called_once_with()

    def test_reposition_updates_geometry_even_when_height_is_unchanged(self):
        panel = self.panel()
        panel.window = SimpleNamespace(IsVisible=True, Top=900.0, Height=480.0)
        panel.open = True
        panel.panel_height = 480.0
        panel._current_work_area = Mock(return_value=(0.0, 0.0, 2560.0, 1000.0))
        panel._update_geometry = Mock()

        panel._apply_height(480.0)

        self.assertEqual(panel.window.Top, 510.0)
        panel._update_geometry.assert_called_once_with()

    @patch("sandglass.native_panel.monitor_work_area",
           return_value=(2560, 0, 4480, 1350))
    def test_orb_geometry_carries_secondary_monitor_work_area(self, work_area):
        panel = self.panel()

        orb_x, orb_y = panel._set_orb_geometry(2700, 150, 56)

        self.assertEqual((orb_x, orb_y), (2700, 150))
        self.assertEqual(panel.work_area, (2560, 0, 4480, 1350))
        work_area.assert_called_once_with(2700, 150, 56, 56)

    @patch("sandglass.native_panel.monitor_work_area",
           return_value=(2560, 0, 4480, 1350))
    @patch("sandglass.native_panel.move_window_physical")
    def test_initial_placement_moves_hidden_hwnd_in_destination_physical_pixels(
            self, move_window, _work):
        panel = self.panel()
        panel.window = SimpleNamespace(Left=0.0, Top=0.0)
        panel.hwnd = 101
        panel.panel_height = 600.0
        orb_x, orb_y = panel._set_orb_geometry(2700, 150, 56)

        with patch("sandglass.native_panel.window_rect",
                   return_value=(100, 100, 380, 600)):
            panel._place_next_to_orb(orb_x, orb_y)

        move_window.assert_called_once_with(101, 2766, 10)
        self.assertEqual((panel.window.Left, panel.window.Top), (0.0, 0.0))

    def test_runtime_uses_only_official_webview2_files(self):
        runtime = native_shell_path()

        self.assertIsNotNone(runtime)
        self.assertTrue((runtime / "Microsoft.Web.WebView2.Core.dll").is_file())
        self.assertTrue((runtime / "Microsoft.Web.WebView2.Wpf.dll").is_file())
        self.assertFalse((runtime / "SandglassShell.exe").exists())

    def test_pinned_webview2_runtime_exposes_composition_dpi_hook(self):
        runtime = native_shell_path()
        import clr

        clr.AddReference(str(runtime / "Microsoft.Web.WebView2.Wpf.dll"))
        from System import Type
        from System.Reflection import BindingFlags

        control = Type.GetType(
            "Microsoft.Web.WebView2.Wpf.WebView2CompositionControl, "
            "Microsoft.Web.WebView2.Wpf"
        )
        flags = BindingFlags.Instance | BindingFlags.NonPublic
        base_field = control.GetField("m_webview2Base", flags)
        self.assertIsNotNone(base_field)
        base_type = base_field.FieldType
        self.assertIsNotNone(base_type.GetProperty(
            "CoreWebView2Controller", flags | BindingFlags.Public))
        self.assertIsNotNone(control.GetMethod(
            "WebView2CompositionControl_SizeChanged", flags))

    def test_panel_source_locks_stable_geometry_border_and_docking(self):
        source = (ROOT / "sandglass" / "native_panel.py").read_text(encoding="utf-8")
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("if self.animating:", source)
        self.assertIn("self.pending_height = wanted", source)
        self.assertIn("self._update_geometry()", source)
        self.assertIn("self.window.LocationChanged += self._on_location_changed", source)
        self.assertIn("self.window.DpiChanged += self._on_dpi_changed", source)
        self.assertIn("controller.RasterizationScale = scale", source)
        self.assertIn("Switch.System.Windows.DoNotScaleForDpiChanges", source)
        self.assertIn("if not self._user_dragging", source)
        show_fn = source.index("def _show(")
        self.assertLess(
            source.index("animate = self._prepare_open()", show_fn),
            source.index("self.window.Show()", show_fn),
        )
        self.assertIn('self.t["DispatcherPriority"].ContextIdle', source)
        self.assertIn('self.frame.CacheMode = t["BitmapCache"]()', source)
        self.assertIn("EdgeDock", source)
        self.assertIn("BorderBrush", source)
        self.assertIn("self.window.Icon = icon_bitmap", source)
        self.assertIn("self.window.ShowInTaskbar = False", source)
        self.assertIn('"pythonnet>=3,<4; sys_platform == \'win32\'"', project)
        self.assertIn("native/SandglassShell/bin/", ignore)

    def test_native_panel_maps_static_files_and_bridges_api_in_process(self):
        source = (ROOT / "sandglass" / "native_panel.py").read_text(encoding="utf-8")
        runtime = native_shell_path()
        bridge_path, mode, bridge_bytes = load_native_bridge(runtime)
        bridge = bridge_bytes.decode("utf-8")

        self.assertIn("SetVirtualHostNameToFolderMapping", source)
        self.assertIn('"sandglass.local"', source)
        self.assertIn('action == "api"', source)
        self.assertIn("window.fetch = function", bridge)
        self.assertIn("__sandglassApiResolve", bridge)
        self.assertIn('send("api\\t"', bridge)
        self.assertIn("API_DEADLINE_MS = 20000", bridge)
        self.assertIn("status: 599", bridge)
        self.assertEqual(native_bridge_path(runtime), (bridge_path, mode))
        self.assertEqual(mode, "source")

    def test_tests_and_runtime_resolve_the_same_bridge(self):
        runtime = native_shell_path()
        bridge, mode = native_bridge_path(runtime)

        self.assertEqual(mode, "source")
        self.assertNotEqual(bridge, runtime / "PanelShell.js")
        self.assertEqual(
            bridge.read_bytes(), load_native_bridge(runtime)[2]
        )

    def test_native_api_bridge_resolves_json_on_the_wpf_dispatcher(self):
        panel = self.panel()
        panel.api_request = Mock(return_value={"accounts": [{"provider": "codex"}]})
        panel.web = Mock()
        panel.window = SimpleNamespace(Dispatcher=Mock())
        panel.window.Dispatcher.BeginInvoke.side_effect = lambda action: action()
        panel.t = {"Action": lambda action: action}

        class ImmediateThread:
            def __init__(self, *, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        with patch("sandglass.native_panel.threading.Thread", ImmediateThread):
            panel._api("7", "GET", "/api/quota", "")

        panel.api_request.assert_called_once_with("GET", "/api/quota", "")
        script = panel.web.ExecuteScriptAsync.call_args.args[0]
        self.assertIn('window.__sandglassApiResolve("7",200,', script)
        self.assertIn("codex", script)

    def _wired_panel(self):
        panel = self.panel()
        panel.api_request = Mock(return_value={"accounts": []})
        panel.web = Mock()
        panel.window = SimpleNamespace(Dispatcher=Mock())
        panel.window.Dispatcher.BeginInvoke.side_effect = lambda action: action()
        panel.t = {"Action": lambda action: action}
        return panel

    @staticmethod
    def _bridged(panel):
        class ImmediateThread:
            def __init__(self, *, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        with patch("sandglass.native_panel.threading.Thread", ImmediateThread):
            panel._api("7", "GET", "/api/quota", "")

    def test_every_bridge_path_answers_because_the_page_cannot_time_out(self):
        """__sandglassApiResolve is the only thing that settles the page's fetch.

        Over loopback these two failures are a 503 and a 500 the page reacts to.
        On the bridge they used to be silence, and silence here is a promise
        that never settles: a spinner with no way back except a restart.
        """
        cases = (
            ("no api to call", lambda p: setattr(p, "api_request", None), 503),
            (
                "payload that cannot be serialized",
                lambda p: setattr(p, "api_request", Mock(return_value={"at": object()})),
                500,
            ),
        )
        for name, break_it, expected in cases:
            with self.subTest(name):
                panel = self._wired_panel()
                break_it(panel)

                self._bridged(panel)

                panel.web.ExecuteScriptAsync.assert_called_once()
                self.assertIn(
                    f'window.__sandglassApiResolve("7",{expected},',
                    panel.web.ExecuteScriptAsync.call_args.args[0],
                )

    def test_a_reply_that_never_reached_the_page_is_not_evidence_of_a_served_call(self):
        """record_api_bridge_result is what a packaged smoke reads as proof.

        Counting the call before the reply is handed over lets the evidence say
        the bridge served a request the page never received.
        """
        cases = (
            (
                "webview refused the script",
                lambda p: setattr(
                    p.web.ExecuteScriptAsync, "side_effect", RuntimeError("gone")
                ),
            ),
            (
                "dispatcher refused the work",
                lambda p: setattr(
                    p.window.Dispatcher.BeginInvoke, "side_effect", RuntimeError("gone")
                ),
            ),
        )
        for name, break_it in cases:
            with self.subTest(name):
                panel = self._wired_panel()
                break_it(panel)

                with patch("sandglass.native_panel.record_api_bridge_result") as recorded:
                    self._bridged(panel)

                self.assertEqual([call.args[0] for call in recorded.call_args_list], [599])

    def test_a_served_call_is_recorded_only_once_the_reply_is_handed_over(self):
        panel = self._wired_panel()
        order = []
        panel.web.ExecuteScriptAsync.side_effect = lambda _script: order.append("sent")

        with patch(
            "sandglass.native_panel.record_api_bridge_result",
            side_effect=lambda status: order.append(f"recorded {status}"),
        ):
            self._bridged(panel)

        self.assertEqual(order, ["sent", "recorded 200"])


if __name__ == "__main__":
    unittest.main()
