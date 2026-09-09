"""In-process WPF composition panel for the Windows desktop shell.

Only Microsoft-signed WebView2 assemblies are loaded from disk. Sandglass's
presentation logic stays as Python source in the existing desktop process, so
Smart App Control never has to trust a freshly compiled unsigned executable.
"""

from __future__ import annotations

import json
import hashlib
import os
import sys
import threading
import traceback
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote

from sandglass.orb import (EdgeDock, monitor_work_area, move_window_physical,
                           window_rect, window_scale)
from sandglass.paths import meter_home
from sandglass.resources import SOURCE_ROOT, WEB_DIR, source_native_bridge_path
from sandglass.runtime_provenance import (
    build_runtime_identity,
    record_api_bridge_result,
    record_runtime_identity,
)


_BRIDGE_CONTRACT = (
    "window.fetch = function",
    "__sandglassApiResolve",
    'send("api\\t"',
    "API_DEADLINE_MS",
)

# A reply that was built but never handed to the page. Not a status any request
# carries: it says the bridge itself did not deliver, which is the one outcome
# the page cannot observe for itself, so the evidence has to carry it instead.
_BRIDGE_UNDELIVERED = 599


def native_shell_path() -> Path | None:
    """Directory containing the official WebView2 WPF runtime."""
    override = os.environ.get("SANDGLASS_NATIVE_SHELL")
    roots = [Path(override)] if override else []
    root = Path(__file__).resolve().parent.parent
    roots.extend((root / "native" / "SandglassShell" / "bin",
                  Path(__file__).resolve().parent / "native"))
    needed = ("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.Wpf.dll",
              "WebView2Loader.dll", "PanelShell.js")
    return next((folder for folder in roots
                 if all((folder / name).is_file() for name in needed)), None)


def native_bridge_path(runtime: Path) -> tuple[Path, str]:
    """Resolve the one bridge that this process must actually execute.

    Editable checkouts load the canonical source directly. Installed bundles do
    not contain that source tree and therefore load the packaged publishing
    copy. Normal desktop startup never writes either location.
    """

    source = source_native_bridge_path()
    if source is not None:
        return source, "source"
    return runtime / "PanelShell.js", "packaged"


def load_native_bridge(runtime: Path) -> tuple[Path, str, bytes]:
    path, mode = native_bridge_path(runtime)
    content = path.read_bytes()
    text = content.decode("utf-8")
    missing = [marker for marker in _BRIDGE_CONTRACT if marker not in text]
    if missing:
        digest = hashlib.sha256(content).hexdigest()
        raise RuntimeError(
            f"native panel bridge contract is incomplete ({digest[:12]})"
        )
    return path, mode, content


class NativePanel:
    PANEL_WIDTH = 380.0
    PANEL_GAP = 10.0

    def __init__(self, url: str, logo: Path, launcher: str, *,
                 on_minimized: Callable[[], None],
                 on_closed: Callable[[], None],
                 on_locale: Callable[[str], None],
                 window_icon: Path | None = None,
                 web_root: Path | None = None,
                 api_request: Callable[[str, str, str], object] | None = None,
                 on_opened: Callable[[], None] | None = None,
                 on_ready: Callable[[], None] | None = None,
                 on_dock: Callable[[str | None, bool,
                                    tuple[int, int, int, int]], None] | None = None,
                 on_move: Callable[[tuple[int, int, int, int]], None] | None = None) -> None:
        self.url, self.logo, self.launcher = url, logo, launcher
        self.window_icon = window_icon
        self.web_root, self.api_request = web_root, api_request
        self.on_minimized, self.on_closed, self.on_locale = (
            on_minimized, on_closed, on_locale)
        self.on_opened, self.on_ready = on_opened, on_ready
        self.on_dock, self.on_move = on_dock, on_move
        self.app = self.window = self.web = None
        self.hwnd = 0
        self.surface = self.frame = self.web_host = self.outline = None
        self.clip = self.scale = self.orb_proxy = None
        self.panel_height, self.orb_side = 560.0, 56.0
        self.display_scale = 1.0
        self.work_area: tuple[float, float, float, float] | None = None
        self.origin_screen_x = self.origin_screen_y = 0.0
        self.ready = self.open = self.animating = self.quitting = False
        self._close_to_tray = False
        self._user_dragging = False
        self.has_measured_height = False
        self.pending_show: tuple[int, int, int, int] | None = None
        self.pending_height: float | None = None
        self._handlers: list[object] = []
        self._started = threading.Event()
        self._stopped = threading.Event()
        self._startup_error: Exception | None = None
        self._init_stage = 0
        self._env_task = self._ensure_task = self._script_task = None
        self._dpi_error: Exception | None = None
        self.dock: EdgeDock | None = None
        self.bridge_path: Path | None = None
        self.bridge_mode = ""
        self.bridge_script = ""

    def start(self) -> None:
        runtime = native_shell_path()
        if runtime is None:
            raise FileNotFoundError("the WebView2 WPF runtime is not prepared")
        self.runtime = runtime
        bridge, mode, content = load_native_bridge(runtime)
        self.bridge_path, self.bridge_mode = bridge, mode
        self.bridge_script = content.decode("utf-8")
        web_index = (self.web_root or WEB_DIR) / "index.html"
        identity = build_runtime_identity(
            source_root=SOURCE_ROOT,
            web_index=web_index,
            native_runtime=runtime,
            panel_bridge=bridge,
            bridge_content=content,
            bridge_mode=mode,
        )
        record_runtime_identity(identity)
        self._load_types(runtime)
        thread = self.t["Thread"](self.t["ThreadStart"](self._thread_main))
        thread.SetApartmentState(self.t["ApartmentState"].STA)
        thread.IsBackground = False
        thread.Name = "sandglass-wpf-panel"
        self._wpf_thread = thread
        thread.Start()
        if not self._started.wait(15):
            raise TimeoutError("the WPF panel did not start")
        if self._startup_error:
            raise RuntimeError(str(self._startup_error)) from self._startup_error

    def _load_types(self, runtime: Path) -> None:
        import clr

        from System import AppContext

        # Python is the executable host, so Sandglass cannot ship a WPF
        # app.config beside a dedicated .exe. Set the documented per-monitor
        # switches before loading any WPF assembly instead.
        AppContext.SetSwitch(
            "Switch.System.Windows.DoNotScaleForDpiChanges", False)
        AppContext.SetSwitch(
            "Switch.System.Windows.DoNotUsePresentationDpiCapabilityTier2OrGreater",
            False,
        )

        framework = Path(os.environ.get("WINDIR", r"C:\Windows")) / \
            "Microsoft.NET" / "Framework64" / "v4.0.30319"
        for assembly in (framework / "WPF" / "PresentationCore.dll",
                         framework / "WPF" / "PresentationFramework.dll",
                         framework / "WPF" / "WindowsBase.dll",
                         framework / "System.Drawing.dll"):
            clr.AddReference(str(assembly))
        clr.AddReference(str(runtime / "Microsoft.Web.WebView2.Core.dll"))
        clr.AddReference(str(runtime / "Microsoft.Web.WebView2.Wpf.dll"))

        from System import Action, Array, Object, TimeSpan, Uri
        from System.Drawing import Color as DrawingColor
        from System.Reflection import BindingFlags
        from System.Threading import ApartmentState, Thread, ThreadStart
        from System.Windows import (Application, CornerRadius, Duration, Point, Rect,
                                   ResizeMode, ShutdownMode, SystemParameters,
                                   Thickness, Visibility, Window, WindowStyle)
        from System.Windows.Controls import Border, Canvas, Grid, Image
        from System.Windows.Interop import WindowInteropHelper
        from System.Windows.Media import (BitmapCache, Brushes, Color,
                                          RectangleGeometry, ScaleTransform,
                                          SolidColorBrush, Stretch)
        from System.Windows.Media.Animation import (CubicEase, DoubleAnimation,
                                                     EasingMode, FillBehavior)
        from System.Windows.Media.Effects import DropShadowEffect
        from System.Windows.Media.Imaging import BitmapCacheOption, BitmapImage
        from System.Windows.Threading import DispatcherPriority, DispatcherTimer
        from Microsoft.Web.WebView2.Core import (
            CoreWebView2Environment, CoreWebView2HostResourceAccessKind,
        )
        from Microsoft.Web.WebView2.Wpf import WebView2CompositionControl

        self.t = locals()

    def _thread_main(self) -> None:
        try:
            self._build_window()
        except Exception as exc:
            self._startup_error = exc
            self._started.set()
            self._stopped.set()
            return
        self._started.set()
        # The tray starts before WPF so users can always reach Quit.  If that
        # action arrives while _build_window() is still creating the
        # Application, quit() cannot post to a dispatcher yet.  Honour the
        # already-recorded request here instead of entering a main loop that no
        # later idempotent quit call can stop.
        if self.quitting:
            try:
                self.app.Shutdown()
            finally:
                self._stopped.set()
            return
        try:
            self.app.Run(self.window)
        finally:
            self._stopped.set()

    def _build_window(self) -> None:
        t = self.t
        self.app = t["Application"]()
        self.app.ShutdownMode = t["ShutdownMode"].OnExplicitShutdown
        self.window = t["Window"]()
        self.window.Title = "sandglass"
        self.window.Width, self.window.Height = self.PANEL_WIDTH, self.panel_height
        self.window.WindowStyle = getattr(t["WindowStyle"], "None")
        self.window.ResizeMode = t["ResizeMode"].NoResize
        self.window.AllowsTransparency = True
        self.window.Background = t["Brushes"].Transparent
        self.window.Topmost, self.window.ShowInTaskbar = True, False
        self.window.Opacity, self.window.Left, self.window.Top = 0.0, -32000, -32000
        if self.window_icon and self.window_icon.is_file():
            icon_bitmap = t["BitmapImage"]()
            icon_bitmap.BeginInit()
            icon_bitmap.CacheOption = t["BitmapCacheOption"].OnLoad
            icon_bitmap.UriSource = t["Uri"](str(self.window_icon))
            icon_bitmap.EndInit()
            icon_bitmap.Freeze()
            self.window.Icon = icon_bitmap

        self.surface = t["Canvas"]()
        self.surface.Background = t["Brushes"].Transparent
        self.surface.ClipToBounds = False
        self.window.Content = self.surface

        self.scale = t["ScaleTransform"](1.0, 1.0)
        self.clip = t["RectangleGeometry"](
            t["Rect"](0, 0, self.PANEL_WIDTH, self.panel_height), 16, 16)
        self.frame = t["Grid"]()
        self.frame.Width, self.frame.Height = self.PANEL_WIDTH, self.panel_height
        self.frame.Background = t["Brushes"].White
        self.frame.RenderTransform, self.frame.Clip = self.scale, self.clip
        self.frame.RenderTransformOrigin = t["Point"](0, 0)
        self.surface.Children.Add(self.frame)

        self.web = t["WebView2CompositionControl"]()
        self.web.Width, self.web.Height = self.PANEL_WIDTH, self.panel_height
        self.web.DefaultBackgroundColor = t["DrawingColor"].Transparent
        self.web_host = t["Grid"]()
        self.web_host.Opacity = 0.0
        self.web_host.Children.Add(self.web)
        self.frame.Children.Add(self.web_host)

        self.outline = t["Border"]()
        self.outline.Width, self.outline.Height = self.PANEL_WIDTH, self.panel_height
        self.outline.Background = t["Brushes"].Transparent
        self.outline.BorderBrush = t["SolidColorBrush"](
            t["Color"].FromArgb(185, 136, 142, 151))
        self.outline.BorderThickness = t["Thickness"](1.0)
        self.outline.CornerRadius = t["CornerRadius"](16)
        self.outline.IsHitTestVisible, self.outline.Opacity = False, 0.0
        self.frame.Children.Add(self.outline)

        mark = t["Image"]()
        mark.Stretch, mark.Margin = t["Stretch"].Uniform, t["Thickness"](9)
        if self.logo.is_file():
            bitmap = t["BitmapImage"]()
            bitmap.BeginInit()
            bitmap.CacheOption = t["BitmapCacheOption"].OnLoad
            bitmap.UriSource = t["Uri"](str(self.logo))
            bitmap.EndInit()
            bitmap.Freeze()
            mark.Source = bitmap
        self.orb_proxy = t["Border"]()
        self.orb_proxy.Width = self.orb_proxy.Height = self.orb_side
        self.orb_proxy.CornerRadius = t["CornerRadius"](self.orb_side / 2)
        self.orb_proxy.Background, self.orb_proxy.Child = t["Brushes"].Black, mark
        self.orb_proxy.Opacity, self.orb_proxy.IsHitTestVisible = 0.0, False
        shadow = t["DropShadowEffect"]()
        shadow.BlurRadius, shadow.ShadowDepth, shadow.Opacity = 13, 3, 0.30
        shadow.Color = t["Color"].FromRgb(0, 0, 0)
        self.orb_proxy.Effect = shadow
        self.surface.Children.Add(self.orb_proxy)

        self.window.Closed += self._on_window_closed
        self.window.SourceInitialized += self._on_source_initialized
        self.window.LocationChanged += self._on_location_changed
        self.window.DpiChanged += self._on_dpi_changed
        self._handlers.extend((self._on_window_closed, self._on_source_initialized,
                               self._on_location_changed, self._on_dpi_changed))
        self.window.Dispatcher.BeginInvoke(t["Action"](self._begin_initialize))

    def _on_source_initialized(self, *_args) -> None:
        helper = self.t["WindowInteropHelper"](self.window)
        self.hwnd = int(helper.Handle.ToInt64())
        self.dock = EdgeDock(lambda: self.hwnd, on_change=self._dock_changed)
        self.dock.start()

    def _dock_changed(self, edge: str | None, tucked: bool) -> None:
        if not self.on_dock or not self.hwnd:
            return
        try:
            self.on_dock(edge, tucked, window_rect(self.hwnd))
        except Exception:
            pass

    def _on_location_changed(self, *_args) -> None:
        """Report only direct panel drags, never its dock peek/tuck animation."""
        self._sync_webview_dpi()
        if not self._user_dragging or not self.on_move or not self.hwnd:
            return
        try:
            self.on_move(window_rect(self.hwnd))
        except Exception:
            pass

    def _on_dpi_changed(self, *_args) -> None:
        self._sync_webview_dpi(force=True)

    def _webview_controller(self):
        if not self.web:
            return None
        flags = self.t["BindingFlags"].Instance | self.t["BindingFlags"].NonPublic
        field = self.web.GetType().GetField("m_webview2Base", flags)
        if field is None:
            return None
        base = field.GetValue(self.web)
        if base is None:
            return None
        all_flags = flags | self.t["BindingFlags"].Public
        prop = base.GetType().GetProperty("CoreWebView2Controller", all_flags)
        return prop.GetValue(base) if prop is not None else None

    def _sync_webview_dpi(self, *, force: bool = False) -> None:
        """Refresh the composition surface when WPF crosses monitor DPI."""
        if not self.hwnd or not self.web:
            return
        scale = window_scale(self.hwnd)
        changed = abs(scale - self.display_scale) > 0.001
        self.display_scale = scale
        x, y, width, height = window_rect(self.hwnd)
        self.work_area = tuple(
            value / scale for value in monitor_work_area(x, y, width, height))
        controller = self._webview_controller()
        if controller is None:
            return
        try:
            controller.ShouldDetectMonitorScaleChanges = False
            controller.RasterizationScale = scale
            if changed or force:
                flags = self.t["BindingFlags"].Instance | self.t["BindingFlags"].NonPublic
                resize = self.web.GetType().GetMethod(
                    "WebView2CompositionControl_SizeChanged", flags)
                if resize is None:
                    raise RuntimeError("WebView2 composition DPI hook is unavailable")
                args = self.t["Array"][self.t["Object"]]([None, None])
                resize.Invoke(self.web, args)
            controller.NotifyParentWindowPositionChanged()
            self._dpi_error = None
        except Exception as exc:
            self._dpi_error = exc

    def _begin_initialize(self) -> None:
        home = meter_home()
        home.mkdir(parents=True, exist_ok=True)
        self._env_task = self.t["CoreWebView2Environment"].CreateAsync(
            None, str(home / "webview-native"))
        self._init_timer = self.t["DispatcherTimer"]()
        self._init_timer.Interval = self.t["TimeSpan"].FromMilliseconds(25)
        self._init_timer.Tick += self._poll_initialize
        self._handlers.append(self._poll_initialize)
        self._init_timer.Start()

    def _poll_initialize(self, *_args) -> None:
        try:
            if self._init_stage == 0 and self._env_task.IsCompleted:
                self._ensure_task = self.web.EnsureCoreWebView2Async(self._env_task.Result)
                self._init_stage = 1
            elif self._init_stage == 1 and self._ensure_task.IsCompleted:
                self._sync_webview_dpi(force=True)
                if self.web_root is not None:
                    self.web.CoreWebView2.SetVirtualHostNameToFolderMapping(
                        "sandglass.local", str(self.web_root),
                        self.t["CoreWebView2HostResourceAccessKind"].DenyCors,
                    )
                self._script_task = self.web.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(
                    self.bridge_script
                )
                self._init_stage = 2
            elif self._init_stage == 2 and self._script_task.IsCompleted:
                self._init_timer.Stop()
                self.web.CoreWebView2.WebMessageReceived += self._on_web_message
                self._handlers.append(self._on_web_message)
                self.web.Source = self.t["Uri"](self.url)
                self.ready = True
                # The injected page reports its exact card height through the
                # first ``fit`` message.  Opening before that direct signal
                # makes the panel animate to the fallback size and jump when
                # the real measurement arrives a frame later.
                self.window.Hide()
        except Exception as exc:
            self._init_timer.Stop()
            self._runtime_error = exc
            traceback.print_exc(file=sys.stderr)
            self.quit()

    def run(self) -> None:
        self._stopped.wait()

    def show(self, x: int, y: int, side: int, height: int) -> bool:
        command = (x, y, side, height)
        if not self.ready or not self.has_measured_height:
            self.pending_show = command
            return False
        self.window.Dispatcher.BeginInvoke(self.t["Action"](lambda: self._show(*command)))
        return True

    def activate(self) -> None:
        """Bring an already-open panel forward without replaying the open animation."""
        if not self.window:
            return
        self.window.Dispatcher.BeginInvoke(self.t["Action"](self._activate))

    def _activate(self) -> None:
        if self.animating or not self.open:
            return
        if self.dock and self.dock.edge:
            self.dock.peek()
        if not self.window.IsVisible:
            self.window.Show()
        self.window.Opacity = 1.0
        self.window.Activate()

    def _show(self, x: int, y: int, side: int, height: int) -> None:
        if self.animating:
            self.pending_show = (x, y, side, height)
            return
        if self.open:
            self._activate()
            return
        orb_x, orb_y = self._set_orb_geometry(x, y, side)
        measured = self.panel_height if self.has_measured_height else height
        self.panel_height = self._clamp_height(measured)
        if not (self.dock and self.dock.edge):
            self._place_next_to_orb(orb_x, orb_y)
        self.window.Width, self.window.Height = self.PANEL_WIDTH, self.panel_height
        self._update_geometry()
        self.window.ShowInTaskbar = False
        animate = self._prepare_open()
        if not self.window.IsVisible:
            self.window.Show()
        self.window.Opacity = 1.0
        self.window.Activate()
        if self.dock and self.dock.edge:
            self.dock.peek()
        if not animate:
            self._finish_open()
            return
        # Let WPF commit the already-collapsed first frame before starting the
        # transition. Starting after Show() used to expose one full-size frame,
        # then pull it back to the orb, which looked like a hitch.
        self.window.Dispatcher.BeginInvoke(
            self.t["DispatcherPriority"].ContextIdle,
            self.t["Action"](self._begin_open),
        )

    def _set_orb_geometry(self, x: int, y: int, side: int) -> tuple[float, float]:
        dpi = max(0.1, side / 56.0)
        self.display_scale = dpi
        self.work_area = tuple(
            value / dpi for value in monitor_work_area(x, y, side, side))
        orb_x, orb_y = x / dpi, y / dpi
        self.orb_side = max(28.0, side / dpi)
        self.origin_screen_x = orb_x + self.orb_side / 2
        self.origin_screen_y = orb_y + self.orb_side / 2
        return orb_x, orb_y

    def _place_next_to_orb(self, orb_x: float, orb_y: float) -> None:
        left, top_edge, right, bottom = self._placement_work_area()
        gap = self.PANEL_GAP
        top = max(top_edge + gap, min(
            orb_y + self.orb_side - self.panel_height,
            bottom - self.panel_height - gap))
        if orb_x - left >= self.PANEL_WIDTH + gap:
            left = orb_x - self.PANEL_WIDTH - gap
        elif right - (orb_x + self.orb_side) >= self.PANEL_WIDTH + gap:
            left = orb_x + self.orb_side + gap
        else:
            left = max(left + gap, min(
                orb_x + self.orb_side - self.PANEL_WIDTH,
                right - self.PANEL_WIDTH - gap))
        # The hidden WPF HWND was created on the primary monitor. Assigning
        # Window.Left here would interpret a secondary monitor's coordinate at
        # that stale primary DPI (for example 5000 -> 7500 at 150%). Move the
        # HWND in physical pixels first; WPF then handles WM_DPICHANGED and
        # updates its logical Left/Top itself.
        move_window_physical(
            self.hwnd,
            round(left * self.display_scale),
            round(top * self.display_scale),
        )

    def _placement_work_area(self) -> tuple[float, float, float, float]:
        if self.work_area is not None:
            return self.work_area
        work = self.t["SystemParameters"].WorkArea
        return work.Left, work.Top, work.Right, work.Bottom

    def _current_work_area(self) -> tuple[float, float, float, float]:
        hwnd = getattr(self, "hwnd", 0)
        if hwnd:
            x, y, width, height = window_rect(hwnd)
            scale = max(0.1, self.display_scale)
            self.work_area = tuple(
                value / scale
                for value in monitor_work_area(x, y, width, height))
        return self._placement_work_area()

    def follow_orb(self, x: int, y: int, side: int) -> None:
        if not self.window:
            return
        self.window.Dispatcher.BeginInvoke(
            self.t["Action"](lambda: self._follow_orb(x, y, side)))

    def _follow_orb(self, x: int, y: int, side: int) -> None:
        if not self.open or self.animating or (self.dock and self.dock.edge):
            return
        orb_x, orb_y = self._set_orb_geometry(x, y, side)
        self._place_next_to_orb(orb_x, orb_y)
        self._update_geometry()

    def is_docked(self) -> bool:
        return bool(self.dock and self.dock.edge)

    def detach_to_orb(self, x: int, y: int, side: int) -> None:
        if not self.window:
            return
        self.window.Dispatcher.BeginInvoke(
            self.t["Action"](lambda: self._detach_to_orb(x, y, side)))

    def _detach_to_orb(self, x: int, y: int, side: int) -> None:
        if self.dock:
            self.dock.release()
        self._set_orb_geometry(x, y, side)
        self._update_geometry()
        self._begin_close()

    def _update_geometry(self) -> None:
        t = self.t
        ox = max(0, min(self.PANEL_WIDTH, self.origin_screen_x - self.window.Left))
        oy = max(0, min(self.panel_height, self.origin_screen_y - self.window.Top))
        self.surface.Width = self.frame.Width = self.PANEL_WIDTH
        self.surface.Height = self.frame.Height = self.panel_height
        self.web.Width, self.web.Height = self.PANEL_WIDTH, self.panel_height
        self.outline.Width, self.outline.Height = self.PANEL_WIDTH, self.panel_height
        self.clip.Rect = t["Rect"](0, 0, self.PANEL_WIDTH, self.panel_height)
        self.scale.CenterX, self.scale.CenterY = ox, oy
        self.orb_proxy.Width = self.orb_proxy.Height = self.orb_side
        self.orb_proxy.CornerRadius = t["CornerRadius"](self.orb_side / 2)
        t["Canvas"].SetLeft(self.orb_proxy, ox - self.orb_side / 2)
        t["Canvas"].SetTop(self.orb_proxy, oy - self.orb_side / 2)

    def _animation(self, start: float, end: float, ms: int, delay: int = 0):
        t = self.t
        animation = t["DoubleAnimation"]()
        animation.From, animation.To = start, end
        animation.Duration = t["Duration"](t["TimeSpan"].FromMilliseconds(ms))
        animation.BeginTime = t["TimeSpan"].FromMilliseconds(delay)
        ease = t["CubicEase"]()
        ease.EasingMode = t["EasingMode"].EaseOut
        animation.EasingFunction, animation.FillBehavior = ease, t["FillBehavior"].HoldEnd
        return animation

    def _prepare_open(self) -> bool:
        if self.animating:
            return False
        t = self.t
        self.animating = self.open = True
        if self.on_opened:
            self.on_opened()
        self.frame.Visibility = t["Visibility"].Visible
        self.orb_proxy.Visibility = t["Visibility"].Hidden
        self.outline.Opacity, self.orb_proxy.Opacity, self.web_host.Opacity = 0.0, 0.0, 0.0
        sx = min(1.0, self.orb_side / self.PANEL_WIDTH)
        sy = min(1.0, self.orb_side / self.panel_height)
        self.scale.ScaleX, self.scale.ScaleY = sx, sy
        self.clip.RadiusX, self.clip.RadiusY = self.PANEL_WIDTH / 2, self.panel_height / 2
        # Freeze the complex WebView visual into one compositor surface while
        # it scales. The live control resumes as soon as the short animation ends.
        self.frame.CacheMode = t["BitmapCache"]()
        return bool(t["SystemParameters"].ClientAreaAnimation)

    def _begin_open(self) -> None:
        if not self.animating or not self.open:
            return
        sx, sy = self.scale.ScaleX, self.scale.ScaleY
        ax, ay = self._animation(sx, 1, 320), self._animation(sy, 1, 320)
        ay.Completed += self._finish_open
        self._handlers.append(self._finish_open)
        self.scale.BeginAnimation(self.t["ScaleTransform"].ScaleXProperty, ax)
        self.scale.BeginAnimation(self.t["ScaleTransform"].ScaleYProperty, ay)
        self.clip.BeginAnimation(self.t["RectangleGeometry"].RadiusXProperty,
                                 self._animation(self.PANEL_WIDTH / 2, 16, 320))
        self.clip.BeginAnimation(self.t["RectangleGeometry"].RadiusYProperty,
                                 self._animation(self.panel_height / 2, 16, 320))
        self.web_host.BeginAnimation(self.web_host.OpacityProperty,
                                     self._animation(0, 1, 225, 35))
        self.outline.BeginAnimation(self.outline.OpacityProperty,
                                    self._animation(0, 1, 140, 150))

    def _finish_open(self, *_args) -> None:
        self._clear_animations()
        self.scale.ScaleX = self.scale.ScaleY = 1.0
        self.clip.RadiusX = self.clip.RadiusY = 16.0
        self.orb_proxy.Opacity = 0.0
        self.orb_proxy.Visibility = self.t["Visibility"].Hidden
        self.web_host.Opacity = self.outline.Opacity = 1.0
        self.frame.CacheMode = None
        self.animating = False
        self._apply_pending_height()
        if (self.on_ready and self.ready and self.has_measured_height
                and self.open and self.window.IsVisible):
            self.on_ready()

    def minimize(self) -> None:
        self.pending_show = None
        if self.window:
            self.window.Dispatcher.BeginInvoke(self.t["Action"](self._begin_close))

    def _begin_close(self) -> None:
        if not self.open or self.animating:
            return
        self._close_to_tray = False
        self.animating, self.open = True, False
        self._update_geometry()
        self.frame.CacheMode = self.t["BitmapCache"]()
        self.orb_proxy.Visibility, self.orb_proxy.Opacity = self.t["Visibility"].Hidden, 0.0
        sx = min(1.0, self.orb_side / self.PANEL_WIDTH)
        sy = min(1.0, self.orb_side / self.panel_height)
        ax, ay = self._animation(1, sx, 315), self._animation(1, sy, 315)
        ay.Completed += self._finish_close
        self._handlers.append(self._finish_close)
        self.scale.BeginAnimation(self.t["ScaleTransform"].ScaleXProperty, ax)
        self.scale.BeginAnimation(self.t["ScaleTransform"].ScaleYProperty, ay)
        self.clip.BeginAnimation(self.t["RectangleGeometry"].RadiusXProperty,
                                 self._animation(16, self.PANEL_WIDTH / 2, 315))
        self.clip.BeginAnimation(self.t["RectangleGeometry"].RadiusYProperty,
                                 self._animation(16, self.panel_height / 2, 315))
        self.web_host.BeginAnimation(self.web_host.OpacityProperty, self._animation(1, 0, 175))
        self.outline.BeginAnimation(self.outline.OpacityProperty, self._animation(1, 0, 115))

    def _finish_close(self, *_args) -> None:
        self._clear_animations()
        self.window.Hide()
        self.window.Opacity, self.animating = 0.0, False
        self.frame.CacheMode = None
        self._apply_pending_height()
        if self._close_to_tray:
            self._close_to_tray = False
            return
        self.on_minimized()
        if self.pending_show and not self.open:
            command, self.pending_show = self.pending_show, None
            self._show(*command)

    def hide_to_tray(self) -> None:
        self.pending_show = None
        if self.window:
            self.window.Dispatcher.BeginInvoke(self.t["Action"](self._hide_to_tray))

    def _hide_to_tray(self) -> None:
        if self.dock:
            self.dock.release()
        self._close_to_tray = True
        self.open = self.animating = False
        self._clear_animations()
        self.window.Hide()
        self.window.Opacity = 0.0
        self.frame.CacheMode = None
        self.on_closed()

    def _clear_animations(self) -> None:
        self.scale.BeginAnimation(self.t["ScaleTransform"].ScaleXProperty, None)
        self.scale.BeginAnimation(self.t["ScaleTransform"].ScaleYProperty, None)
        self.clip.BeginAnimation(self.t["RectangleGeometry"].RadiusXProperty, None)
        self.clip.BeginAnimation(self.t["RectangleGeometry"].RadiusYProperty, None)
        for item in (self.orb_proxy, self.web_host, self.outline):
            item.BeginAnimation(item.OpacityProperty, None)

    def _on_web_message(self, _sender, args) -> None:
        try:
            parts = str(args.TryGetWebMessageAsString()).split("\t")
        except Exception:
            return
        action = parts[0]
        if action == "minimize": self._begin_close()
        elif action == "close": self._hide_to_tray()
        elif action == "drag": self._begin_drag()
        elif action == "fit" and len(parts) > 1: self._fit(parts[1])
        elif action == "locale" and len(parts) > 1: self.on_locale(parts[1])
        elif action == "request" and len(parts) > 2: self._request(parts[1], parts[2])
        elif action == "api" and len(parts) > 4:
            self._api(parts[1], parts[2], unquote(parts[3]), unquote(parts[4]))

    def _begin_drag(self) -> None:
        if not self.open or self.animating:
            return
        self._user_dragging = True
        try:
            self.window.DragMove()
        except Exception:
            return
        finally:
            self._user_dragging = False
        self._current_work_area()
        self._update_geometry()
        if self.dock:
            threading.Thread(target=self.dock.settle, daemon=True).start()

    def _fit(self, raw: str) -> None:
        try:
            wanted = float(raw)
        except ValueError:
            return
        self.has_measured_height = True
        if self.ready and self.pending_show and not self.open:
            command, self.pending_show = self.pending_show, None
            # Cap against the orb's monitor inside _show, not the hidden HWND
            # parked at -32000, which is almost always the primary screen.
            self.panel_height = wanted
            self._show(*command)
            return
        if self.animating:
            self.pending_height = wanted
        else:
            self._apply_height(wanted)

    def _apply_height(self, wanted: float) -> None:
        height = wanted
        moved = False
        if self.window.IsVisible and self.open:
            _left, top, _right, bottom = self._current_work_area()
            height = min(height, max(240.0, bottom - top - 20.0))
            # A short page can leave the panel close to the work area's bottom.
            # When the next page is taller, grow upward instead of clipping the
            # newly rendered controls below the existing Top coordinate.
            if self.window.Top + height > bottom - 10.0:
                new_top = max(top + 10.0, bottom - height - 10.0)
                moved = abs(new_top - self.window.Top) >= 1.0
                self.window.Top = new_top
        if abs(height - self.panel_height) < 2:
            if moved:
                self._update_geometry()
            return
        self.panel_height, self.window.Height = height, height
        self._update_geometry()
        self.scale.ScaleX = self.scale.ScaleY = 1.0
        self.clip.RadiusX = self.clip.RadiusY = 16.0

    def _apply_pending_height(self) -> None:
        if self.pending_height is not None:
            height, self.pending_height = self.pending_height, None
            self._apply_height(height)

    def _clamp_height(self, height: float) -> float:
        """Cap to the destination work area already stored on this panel.

        `_current_work_area` re-reads the HWND. Before the first Show that
        HWND sits at (-32000, -32000), so MonitorFromRect picks the primary
        screen and a panel opening beside an orb on the secondary is clipped
        to the wrong height. `_show` sets `work_area` from the orb first.
        """
        _left, top, _right, bottom = self._placement_work_area()
        return max(240.0, min(height, bottom - top - 40.0))

    def _request(self, request_id: str, action: str) -> None:
        result = self._enable_autostart() if action == "enable_autostart" \
            else self._autostart_enabled()
        quoted = request_id.replace("\\", "\\\\").replace('"', '\\"')
        self.web.ExecuteScriptAsync(
            f'window.__sandglassResolve("{quoted}",{"true" if result else "false"})')

    def _api(self, request_id: str, method: str, target: str, body: str) -> None:
        # The page's fetch over this bridge is settled by __sandglassApiResolve
        # and by nothing else: it has no status of its own to fall back on and no
        # deadline. Every path here therefore has to answer. Returning early, or
        # letting an exception escape between building the reply and sending it,
        # does not surface as a failed request the way the loopback transport's
        # 500 would -- it leaves the panel waiting on a promise that will never
        # settle, with a spinner and no way back except a restart.
        def run() -> None:
            if self.api_request is None:
                payload, status = {"error": "unavailable"}, 503
            else:
                try:
                    payload = self.api_request(method, target, body)
                    status = 200
                except KeyError:
                    payload, status = {"error": "not_found"}, 404
                except Exception:
                    payload, status = {"error": "internal_error"}, 500
            try:
                response_body = json.dumps(payload, ensure_ascii=False)
            except Exception:
                # A payload that cannot be serialized is the same failure the
                # loopback transport answers 500 to. It used to escape this
                # thread instead, to a console a packaged build does not have.
                status = 500
                response_body = json.dumps({"error": "internal_error"})
            script = (
                "window.__sandglassApiResolve("
                f"{json.dumps(str(request_id))},{status},{json.dumps(response_body, ensure_ascii=False)});"
            )

            def resolve() -> None:
                # Recorded here rather than above: this is the evidence a
                # packaged smoke reads as "the bridge served a call", and until
                # the reply is handed to the webview nothing has been served.
                try:
                    self.web.ExecuteScriptAsync(script)
                except Exception:
                    record_api_bridge_result(_BRIDGE_UNDELIVERED)
                    return
                record_api_bridge_result(status)

            try:
                self.window.Dispatcher.BeginInvoke(self.t["Action"](resolve))
            except Exception:
                record_api_bridge_result(_BRIDGE_UNDELIVERED)
                # The dispatcher is gone, so JS will not run. PanelShell.js
                # rejects the fetch at its delivery deadline.

        threading.Thread(target=run, name="sandglass-native-api", daemon=True).start()

    @staticmethod
    def _autostart_enabled() -> bool:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                return bool(winreg.QueryValueEx(key, "sandglass")[0])
        except OSError:
            return False

    def _enable_autostart(self) -> bool:
        if not self.launcher:
            return False
        import winreg
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                  r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                winreg.SetValueEx(key, "sandglass", 0, winreg.REG_SZ, self.launcher)
        except OSError:
            return False
        return self._autostart_enabled()

    def _on_window_closed(self, *_args) -> None:
        if not self.quitting:
            self.on_closed()

    def quit(self) -> bool:
        if self.quitting:
            return True
        self.quitting = True
        if self.dock:
            self.dock.stop()
        if self.app and self.window:
            try:
                self.window.Dispatcher.BeginInvoke(self.t["Action"](self.app.Shutdown))
            except Exception:
                self.quitting = False
                return False
        return True
