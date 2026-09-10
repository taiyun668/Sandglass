"""The floating orb, drawn as a Win32 layered window.

Not a webview. pywebview clamps windows to a 200px minimum width, recomputes
small sizes inconsistently under display scaling, and loses its transparent
layer whenever the window is resized -- all of which a 56px circle runs into
head first. The orb is a static picture, so PIL renders it once and
UpdateLayeredWindow puts it on screen with real per-pixel alpha.

The panel stays a pywebview window, where none of those limits bite.
"""

from __future__ import annotations

import contextlib
import ctypes
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
shcore = ctypes.windll.shcore

WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080  # keeps it out of the taskbar and alt-tab
WS_EX_APPWINDOW = 0x00040000
GWL_EXSTYLE = -20
WS_POPUP = 0x80000000
SW_SHOW, SW_HIDE = 5, 0
ULW_ALPHA = 0x00000002
AC_SRC_OVER, AC_SRC_ALPHA = 0x00, 0x01
WM_DESTROY, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE = 0x0002, 0x0201, 0x0202, 0x0200
WM_RBUTTONUP, WM_CLOSE, WM_APP = 0x0205, 0x0010, 0x8000
WM_DPICHANGED = 0x02E0
WM_ORB_HIDE, WM_ORB_SHOW, WM_ORB_QUIT, WM_ORB_MOVE, WM_ORB_ACTIVATE, WM_ORB_UNINSTALL = (
    WM_APP + 1, WM_APP + 2, WM_APP + 3, WM_APP + 4, WM_APP + 5, WM_APP + 6)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
HWND_TOPMOST = -1
DRAG_SLOP = 4  # px of travel that turns a click into a drag
SPI_GETCLIENTAREAANIMATION = 0x1042

# Without these, ctypes defaults every return to c_int and silently truncates
# 64-bit handles, which fails in ways that look like the window "just not showing".
user32.CreateWindowExW.restype = wintypes.HWND
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.DefWindowProcW.restype = ctypes.c_longlong
user32.GetDC.restype = wintypes.HDC
user32.LoadCursorW.restype = wintypes.HANDLE
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.restype = wintypes.HANDLE
gdi32.SelectObject.restype = wintypes.HANDLE
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
dwmapi = ctypes.windll.dwmapi


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


WM_SETICON = 0x0080
ICON_SMALL, ICON_BIG = 0, 1


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                             ctypes.c_ulonglong, ctypes.c_longlong)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


kernel32.GetModuleHandleW.restype = wintypes.HMODULE
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT,
                               wintypes.UINT, ctypes.c_void_p]
gdi32.CreateBitmap.restype = wintypes.HBITMAP
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.CreateIconIndirect.restype = wintypes.HICON
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                ctypes.c_ulonglong, ctypes.c_longlong]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE),
    wintypes.HDC, ctypes.POINTER(POINT), wintypes.DWORD,
    ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD,
]
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, ctypes.c_ulonglong, ctypes.c_longlong,
]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]


gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.CreateRoundRectRgn.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int,
]
gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.SetCapture.argtypes = [wintypes.HWND]
user32.GetKeyState.argtypes = [ctypes.c_int]
user32.GetKeyState.restype = ctypes.c_short
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
user32.PostMessageW.argtypes = [
    wintypes.HWND, wintypes.UINT, ctypes.c_ulonglong, ctypes.c_longlong,
]
user32.SystemParametersInfoW.argtypes = [
    wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT,
]
user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
user32.MonitorFromRect.argtypes = [ctypes.POINTER(RECT), wintypes.DWORD]
user32.MonitorFromRect.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
if hasattr(user32, "GetDpiForWindow"):
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
if hasattr(user32, "SetProcessDpiAwarenessContext"):
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
if hasattr(shcore, "GetDpiForMonitor"):
    shcore.GetDpiForMonitor.argtypes = [
        wintypes.HANDLE, ctypes.c_int,
        ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT),
    ]
    shcore.GetDpiForMonitor.restype = ctypes.c_long


def enable_per_monitor_v2() -> None:
    """Declare the process per-monitor-v2 aware before any UI is created."""
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass


def system_scale() -> float:
    enable_per_monitor_v2()
    try:
        return user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


def window_scale(hwnd: int) -> float:
    """Return a live window's effective scale without using system DPI."""
    try:
        dpi = int(user32.GetDpiForWindow(hwnd))
    except (AttributeError, OSError):
        return system_scale()
    return max(0.1, dpi / 96.0)


def move_window_physical(hwnd: int, x: int, y: int) -> None:
    """Move a live window using physical screen coordinates.

    WPF ``Window.Left`` is interpreted using the window's current monitor DPI.
    A hidden window created on a 150% primary screen therefore turns a 100%
    secondary-screen x coordinate into 150% of that value and can land outside
    the virtual desktop.  SetWindowPos crosses the monitor boundary first, so
    WPF receives WM_DPICHANGED and adopts the destination scale.
    """
    user32.SetWindowPos(
        hwnd, 0, int(x), int(y), 0, 0,
        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
    )


def monitor_scale(x: int, y: int, width: int = 1, height: int = 1) -> float:
    """Return the effective scale of the monitor nearest a physical rectangle.

    This is the pre-window counterpart to ``window_scale``.  It is needed when
    restoring the orb because no HWND exists yet, so GetDpiForWindow cannot be
    used to size it for the monitor containing its saved position.
    """
    enable_per_monitor_v2()
    rect = RECT(x, y, x + max(1, width), y + max(1, height))
    try:
        monitor = user32.MonitorFromRect(
            ctypes.byref(rect), MONITOR_DEFAULTTONEAREST)
        dpi_x = wintypes.UINT()
        dpi_y = wintypes.UINT()
        if monitor and shcore.GetDpiForMonitor(
                monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
            return max(0.1, int(dpi_x.value) / 96.0)
    except (AttributeError, OSError):
        pass
    return system_scale()


def render(diameter: int, mark_path: Path, disc: tuple[int, int, int] = (10, 10, 10),
           ratio: float | None = None) -> Image.Image:
    """The orb as RGBA, drawn at 4x and downsampled so the rim is clean.

    Fit the visible mark by its longest side so both wide and tall artwork stays
    inside the disc. Tray-sized icons get a bigger share of the disc or the
    glyph turns into a smudge.
    """
    ss = 4
    big = diameter * ss
    if ratio is None:
        ratio = 0.86 if diameter < 40 else 0.78
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    from PIL import ImageDraw

    ImageDraw.Draw(img).ellipse((0, 0, big - 1, big - 1), fill=disc + (255,))
    mark = Image.open(mark_path).convert("RGBA")
    alpha_box = mark.getchannel("A").getbbox()
    if alpha_box:
        mark = mark.crop(alpha_box)
    limit = int(big * ratio)
    scale = min(limit / mark.width, limit / mark.height)
    mark = mark.resize((max(1, round(mark.width * scale)),
                        max(1, round(mark.height * scale))), Image.LANCZOS)
    img.alpha_composite(mark, ((big - mark.width) // 2, (big - mark.height) // 2))
    return img.resize((diameter, diameter), Image.LANCZOS)


WM_CAPTURECHANGED = 0x0215
VK_LBUTTON = 0x01
MONITOR_DEFAULTTONEAREST = 2
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

# An HMONITOR is a pointer. Without these, ctypes returns it as a C int and the
# top half of a 64-bit handle is gone -- GetMonitorInfoW then fails and every
# caller silently falls back to the whole virtual desktop, so the orb would
# clamp to the bounding box of every monitor rather than to one screen. It has
# not bitten because Windows hands out low values here, which is luck, not a
# contract, and EdgeDock now keeps one of these handles rather than using it
# and discarding it.
user32.MonitorFromRect.argtypes = [ctypes.POINTER(RECT), wintypes.DWORD]
user32.MonitorFromRect.restype = ctypes.c_void_p
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = ctypes.c_void_p
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
MONITOR_DEFAULTTOPRIMARY = 1
ORB_INSET = 40  # physical px from the work-area edges on first launch


def _work_area_of(monitor) -> tuple[int, int, int, int] | None:
    """One monitor's work area, or None if that monitor is gone."""
    if not monitor:
        return None
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    work = info.rcWork
    return work.left, work.top, work.right, work.bottom


def monitor_at(x: int, y: int, width: int = 1, height: int = 1):
    """The monitor a rectangle mostly sits on, as a handle worth keeping."""
    rect = RECT(x, y, x + max(1, width), y + max(1, height))
    return user32.MonitorFromRect(ctypes.byref(rect), MONITOR_DEFAULTTONEAREST)


def monitor_work_area(x: int, y: int, width: int = 1,
                      height: int = 1) -> tuple[int, int, int, int]:
    """Return the nearest monitor's taskbar-free work area in physical pixels."""
    rect = RECT(x, y, x + max(1, width), y + max(1, height))
    monitor = user32.MonitorFromRect(ctypes.byref(rect), MONITOR_DEFAULTTONEAREST)
    work = _work_area_of(monitor)
    if work is not None:
        return work
    left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    return (left, top,
            left + user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
            top + user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))


def primary_work_area() -> tuple[int, int, int, int]:
    """The primary monitor's taskbar-free work area in physical pixels.

    MonitorFromWindow(NULL) is the documented primary-monitor handle. Asking
    MonitorFromRect at (0, 0) is not: on a layout whose primary sits to the
    right, the virtual origin lives on a different screen.
    """
    enable_per_monitor_v2()
    work = _work_area_of(user32.MonitorFromWindow(0, MONITOR_DEFAULTTOPRIMARY))
    if work is not None:
        return work
    return monitor_work_area(0, 0)


def default_orb_pos(side: int) -> tuple[int, int]:
    """Bottom-right of the primary work area, inset from both edges."""
    _left, _top, right, bottom = primary_work_area()
    return (right - side - ORB_INSET, bottom - side - ORB_INSET)


def clamp_to_screen(x: int, y: int, side: int) -> tuple[int, int]:
    """Never let the orb leave the desktop -- once off screen it cannot be dragged back."""
    left, top, right, bottom = monitor_work_area(x, y, side, side)
    margin = side // 2
    return (max(left - margin, min(x, right - side + margin)),
            max(top, min(y, bottom - side + margin)))


DOCK_SNAP = 26          # how near an edge counts as "put it there"
DOCK_PEEK = 8           # sliver left on screen once it has tucked away
DOCK_SETTLE = 0.10      # pause between landing on the edge and tucking away
DOCK_LEAVE = 0.12       # pointer must be clear this long before it tucks again
DOCK_GRAB = 6           # how far outside the sliver still counts as touching it
DOCK_LET_GO = 26        # ...but it has to get this far clear before it tucks
SLIDE_SECONDS = 0.12    # one slide, start to finish
DOCK_POLL = 0.04        # hover response cannot be quicker than this


@contextlib.contextmanager
def precise_timers():
    """Ask Windows for 1ms timers for the duration of a move.

    The default scheduling tick is 15.6ms, so a sleep of "12ms" really lasts
    15.6ms and varies -- which is exactly what a slide made of small sleeps looks
    like when it stutters. Raised only while animating or dragging; leaving it on
    costs battery.
    """
    winmm = ctypes.windll.winmm
    winmm.timeBeginPeriod(1)
    try:
        yield
    finally:
        winmm.timeEndPeriod(1)


def slide_to(hwnd: int, x: int, y: int, duration: float = SLIDE_SECONDS) -> None:
    """Move over a fixed wall-clock time, not a fixed number of frames.

    Frame-counted animation runs long whenever a frame is late, and every frame
    here competes with the message pump, the poll loop and an HTTP server for the
    GIL. Driving it from the clock keeps the duration honest and drops frames
    instead of dragging the whole slide out.
    """
    x0, y0, _, _ = window_rect(hwnd)
    if (x0, y0) == (x, y):
        return
    flags = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
    with precise_timers():
        start = time.perf_counter()
        while True:
            t = (time.perf_counter() - start) / duration
            if t >= 1.0:
                break
            e = 1 - (1 - t) ** 3        # ease out
            user32.SetWindowPos(hwnd, 0, round(x0 + (x - x0) * e),
                                round(y0 + (y - y0) * e), 0, 0, flags)
            time.sleep(0.004)
        user32.SetWindowPos(hwnd, 0, x, y, 0, 0, flags)


def animations_enabled() -> bool:
    """Respect the Windows accessibility preference for client animations."""
    enabled = wintypes.BOOL()
    if not user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0):
        return True
    return bool(enabled.value)


def clear_window_region(hwnd: int) -> None:
    """Restore a window's normal rectangular region."""
    user32.SetWindowRgn(hwnd, None, True)


def set_window_reveal(hwnd: int, orb_rect: tuple[int, int, int, int],
                      progress: float) -> None:
    """Clip a full-sized window to a rounded rectangle growing from the orb."""
    panel_x, panel_y, width, height = window_rect(hwnd)
    orb_x, orb_y, orb_w, orb_h = orb_rect
    origin_x = orb_x + orb_w / 2 - panel_x
    origin_y = orb_y + orb_h / 2 - panel_y
    amount = max(0.0, min(float(progress), 1.0))
    left = round(origin_x * (1.0 - amount))
    top = round(origin_y * (1.0 - amount))
    right = round(origin_x + (width - origin_x) * amount)
    bottom = round(origin_y + (height - origin_y) * amount)
    # A non-empty seed lets Windows retain the region even while the origin is
    # just outside the panel. It becomes visible only when it reaches the edge.
    if right <= left:
        right = left + 1
    if bottom <= top:
        bottom = top + 1
    radius = max(2, round(18 * amount))
    region = gdi32.CreateRoundRectRgn(left, top, right, bottom, radius, radius)
    if not region:
        return
    # After a successful SetWindowRgn Windows owns the region handle.
    if not user32.SetWindowRgn(hwnd, region, True):
        gdi32.DeleteObject(region)


def animate_window_reveal(hwnd: int, orb_rect: tuple[int, int, int, int],
                          *, opening: bool, duration: float = 0.22) -> None:
    """Reveal or collapse the panel from the orb without resizing WebView2.

    Resizing WebView2 on every frame forces page layout and visibly stutters.
    The window therefore stays at its final geometry while only its native clip
    region changes. DwmFlush keeps updates aligned with desktop composition.
    """
    if duration <= 0 or not animations_enabled():
        clear_window_region(hwnd)
        return
    start, end = (0.0, 1.0) if opening else (1.0, 0.0)
    with precise_timers():
        began = time.perf_counter()
        while True:
            progress = (time.perf_counter() - began) / duration
            if progress >= 1.0:
                break
            eased = progress * progress * (3.0 - 2.0 * progress)
            set_window_reveal(hwnd, orb_rect, start + (end - start) * eased)
            if dwmapi.DwmFlush() != 0:
                time.sleep(0.008)
    if opening:
        clear_window_region(hwnd)
    else:
        set_window_reveal(hwnd, orb_rect, 0.0)


class EdgeDock:
    """Snap a window to a screen edge and let it hide there.

    Windows docks nothing for you, so this polls. A drag ends near an edge, the
    window slides flush and then tucks away leaving a sliver; touching the sliver
    slides it back out, and moving off tucks it again. Left, right and top only
    -- the bottom edge is the taskbar's.
    """

    def __init__(self, get_hwnd, on_change=None) -> None:
        self.get_hwnd = get_hwnd
        self.on_change = on_change
        self.edge: str | None = None
        self.tucked = False
        self._monitor = None
        self._clear_since: float | None = None
        self._stop = threading.Event()
        self._armed = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = threading.Lock()

    # -- geometry --------------------------------------------------------
    def _docked_work_area(self, x: int, y: int, w: int, h: int):
        """The work area of the monitor this window docked to.

        Deriving it from the current rectangle looks equivalent and is not.
        Once tucked, all but DOCK_PEEK pixels sit on the neighbouring monitor,
        and MonitorFromRect selects by largest intersection -- so peek()
        computed the flush position against the wrong screen and the window slid
        to the next monitor. Measured on this machine's two screens: a rectangle
        holding 8px of one and 372px of the other selects the one holding 372.

        The monitor is latched when the dock is decided, from the rectangle as
        the user left it, and its work area is re-read every time so a
        resolution or taskbar change still lands. A handle that no longer
        resolves means that monitor is gone, and then the current rectangle is
        the only thing left to ask.
        """
        work = _work_area_of(self._monitor)
        if work is not None:
            return work
        self._monitor = None
        return monitor_work_area(x, y, w, h)

    def _flush(self, x: int, y: int, w: int, h: int, edge: str) -> tuple[int, int]:
        left, top, right, _bottom = self._docked_work_area(x, y, w, h)
        if edge == "left":
            return left, y
        if edge == "right":
            return right - w, y
        return x, top

    def _hidden(self, x: int, y: int, w: int, h: int, edge: str) -> tuple[int, int]:
        left, top, right, _bottom = self._docked_work_area(x, y, w, h)
        if edge == "left":
            return left + DOCK_PEEK - w, y
        if edge == "right":
            return right - DOCK_PEEK, y
        return x, top + DOCK_PEEK - h

    def _nearest_edge(self, x: int, y: int, w: int, h: int) -> str | None:
        left, top, right, _bottom = monitor_work_area(x, y, w, h)
        gaps = {
            "left": x - left,
            "right": right - (x + w),
            "top": y - top,
        }
        edge = min(gaps, key=lambda k: gaps[k])
        return edge if gaps[edge] <= DOCK_SNAP else None

    # -- transitions -----------------------------------------------------
    def settle(self) -> None:
        """Call when a drag finishes: dock if it landed on an edge, else let go."""
        hwnd = self.get_hwnd()
        if not hwnd:
            return
        x, y, w, h = window_rect(hwnd)
        edge = self._nearest_edge(x, y, w, h)
        if edge is None:
            self.edge, self.tucked, self._monitor = None, False, None
            self._armed.clear()
            self._notify()
            return
        self.edge = edge
        self._armed.set()
        # Latch from the rectangle the user left, which is still whole and
        # therefore still unambiguous about which screen it is on.
        self._monitor = monitor_at(x, y, w, h)
        with self._busy:
            slide_to(hwnd, *self._flush(x, y, w, h, edge))
        self._notify()
        threading.Timer(DOCK_SETTLE, self.tuck).start()

    def tuck(self) -> None:
        hwnd = self.get_hwnd()
        if not hwnd or not self.edge or self.tucked:
            return
        if not user32.IsWindowVisible(hwnd):
            return
        x, y, w, h = window_rect(hwnd)
        # Releasing a drag usually leaves the pointer on the window. Tucking on
        # the timer regardless would slide it away and the hover poll would pull
        # it straight back -- one visible flinch. Wait for the pointer to leave.
        if self._pointer_inside(x, y, w, h, DOCK_LET_GO):
            self._clear_since = None
            return
        with self._busy:
            slide_to(hwnd, *self._hidden(x, y, w, h, self.edge))
        self.tucked = True
        self._notify()

    def peek(self) -> None:
        hwnd = self.get_hwnd()
        if not hwnd or not self.edge or not self.tucked:
            return
        x, y, w, h = window_rect(hwnd)
        with self._busy:
            slide_to(hwnd, *self._flush(x, y, w, h, self.edge))
        self.tucked = False
        self._clear_since = None
        self._notify()

    def flush_position(self, x: int, y: int, w: int, h: int) -> tuple[int, int]:
        """Where this window sits when docked but not hidden."""
        if not self.edge:
            return x, y
        return self._flush(x, y, w, h, self.edge)

    def release(self) -> None:
        """Forget the dock without moving anything -- used when the window hides."""
        self.edge, self.tucked, self._clear_since = None, False, None
        self._monitor = None
        self._armed.clear()

    def _notify(self) -> None:
        if self.on_change:
            try:
                self.on_change(self.edge, self.tucked)
            except Exception:
                pass

    def _pointer_inside(self, x: int, y: int, w: int, h: int, margin: int = DOCK_GRAB) -> bool:
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return (x - margin <= pt.x <= x + w + margin
                and y - margin <= pt.y <= y + h + margin)

    # -- the poll loop ---------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        if self.edge:
            self._armed.set()
        else:
            self._armed.clear()
        self._thread = threading.Thread(target=self._run, name="sandglass-dock", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._armed.set()

    def _run(self) -> None:
        # Hover response needs DOCK_POLL, but only while an edge is latched.
        # The orb starts this thread at ShowWindow and the panel at HWND
        # creation; neither is docked then, and a 25Hz empty wake was the
        # process's loudest idle loop.
        while not self._stop.is_set():
            if not self.edge:
                self._armed.clear()
                if not self.edge and not self._stop.is_set():
                    self._armed.wait()
                continue
            if self._stop.wait(DOCK_POLL):
                return
            try:
                self._tick()
            except Exception:
                pass

    def _tick(self) -> None:
        hwnd = self.get_hwnd()
        if not hwnd or not self.edge or not user32.IsWindowVisible(hwnd):
            return
        if self._busy.locked():
            return
        x, y, w, h = window_rect(hwnd)
        if self.tucked:
            if self._pointer_inside(x, y, w, h):
                self.peek()
            return
        # Wider band on the way out than on the way in. Without that gap a short
        # leave delay turns any wobble on the boundary into tuck, peek, tuck.
        if self._pointer_inside(x, y, w, h, DOCK_LET_GO):
            self._clear_since = None
            return
        now = time.monotonic()
        if self._clear_since is None:
            self._clear_since = now
        elif now - self._clear_since >= DOCK_LEAVE:
            self.tuck()


class Orb:
    """A round always-on-top picture you can drag, click, hide and show.

    Owns a Win32 window, so every method except `post_*` must run on the thread
    that called `run()`. The post_* helpers are the thread-safe way in.
    """

    def __init__(self, image: Image.Image, x: int, y: int,
                 on_click: Callable[[], None],
                 on_activate: Callable[[], None] | None = None,
                 on_menu: Callable[[], None] | None = None,
                 on_drag_start: Callable[[], None] | None = None,
                 on_move: Callable[[int, int], None] | None = None,
                 *, master_image: Image.Image | None = None,
                 on_uninstall: Callable[[], None] | None = None,
                 logical_size: int = 56,
                 start_visible: bool = True) -> None:
        self.image = image
        self.master_image = master_image or image.copy()
        self.logical_size = max(1, int(logical_size))
        self.x, self.y = int(x), int(y)
        self._start_visible = bool(start_visible)
        self.on_click = on_click
        self.on_activate = on_activate
        self.on_uninstall = on_uninstall
        self.on_menu = on_menu
        self.on_drag_start = on_drag_start
        self.on_move = on_move
        self.hwnd = 0
        self._proc = WNDPROC(self._wndproc)
        self._dragging = False
        self._moved = False
        self._grab = (0, 0)
        self._origin = (0, 0)
        self._ready = threading.Event()
        self._pending_move: tuple[int, int, bool] | None = None
        self._move_lock = threading.Lock()
        self.dock = EdgeDock(lambda: self.hwnd, on_change=self._docked)

    def _docked(self, edge, tucked) -> None:
        # The dock moves the window behind our back; keep our own idea of where
        # it is in step, or the next show() would teleport it back.
        if self.hwnd:
            self.x, self.y, _, _ = window_rect(self.hwnd)
            self._fire_move()

    # -- thread-safe entry points ---------------------------------------
    def post_show(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_ORB_SHOW, 0, 0)

    def post_hide(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_ORB_HIDE, 0, 0)

    def post_quit(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_ORB_QUIT, 0, 0)

    def post_uninstall(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_ORB_UNINSTALL, 0, 0)

    def post_move(self, x: int, y: int, *, animate: bool = False) -> None:
        """Move from another UI thread without touching the Win32 window there."""
        if not self.hwnd:
            return
        with self._move_lock:
            self._pending_move = (int(x), int(y), bool(animate))
        user32.PostMessageW(self.hwnd, WM_ORB_MOVE, 0, 0)

    @property
    def visible(self) -> bool:
        return bool(self.hwnd) and bool(user32.IsWindowVisible(self.hwnd))

    # -- window ----------------------------------------------------------
    def run(self) -> None:
        """Create the window and pump its messages. Blocks until quit."""
        cls = WNDCLASS()
        cls.lpfnWndProc = self._proc
        cls.hInstance = kernel32.GetModuleHandleW(None)
        cls.hCursor = user32.LoadCursorW(None, 32512)  # IDC_ARROW
        cls.lpszClassName = "SandglassOrb"
        if not user32.RegisterClassW(ctypes.byref(cls)):
            err = kernel32.GetLastError()
            if err != 1410:  # already registered from a previous run
                raise ctypes.WinError(err)
        w, h = self.image.size
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
            "SandglassOrb", "sandglass", WS_POPUP,
            self.x, self.y, w, h, None, None, cls.hInstance, None)
        if not self.hwnd:
            raise ctypes.WinError(kernel32.GetLastError())
        self._paint()
        if self._start_visible:
            user32.ShowWindow(self.hwnd, SW_SHOW)
        self.dock.start()
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def wait_ready(self, timeout: float = 5.0) -> bool:
        return self._ready.wait(timeout)

    def _paint(self) -> None:
        """Push the RGBA image onto the layered window, premultiplied."""
        w, h = self.image.size
        # UpdateLayeredWindow wants BGRA with colours already multiplied by alpha,
        # and a bottom-up DIB, hence the flip.
        src = self.image.transpose(Image.FLIP_TOP_BOTTOM)
        r, g, b, a = src.split()
        from PIL import ImageChops

        premul = Image.merge("RGBA", (ImageChops.multiply(b, a), ImageChops.multiply(g, a),
                                      ImageChops.multiply(r, a), a))
        raw = premul.tobytes("raw", "RGBA")

        screen_dc = user32.GetDC(None)
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth, header.biHeight = w, h
        header.biPlanes, header.biBitCount = 1, 32
        header.biCompression = 0  # BI_RGB
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(mem_dc, ctypes.byref(header), 0,
                                        ctypes.byref(bits), None, 0)
        ctypes.memmove(bits, raw, len(raw))
        old = gdi32.SelectObject(mem_dc, bitmap)

        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        ok = user32.UpdateLayeredWindow(
            self.hwnd, screen_dc, ctypes.byref(POINT(self.x, self.y)),
            ctypes.byref(SIZE(w, h)), mem_dc, ctypes.byref(POINT(0, 0)),
            0, ctypes.byref(blend), ULW_ALPHA)
        self.last_error = 0 if ok else kernel32.GetLastError()

        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)

    def _apply_dpi(self, hwnd: int, dpi: int, suggested: RECT) -> None:
        """Keep the layered orb the same logical size on every monitor."""
        side = max(1, round(self.logical_size * max(1, dpi) / 96))
        self.x, self.y = suggested.left, suggested.top
        if self.image.size != (side, side):
            self.image = self.master_image.resize((side, side), Image.LANCZOS)
        user32.SetWindowPos(
            hwnd, 0, self.x, self.y, side, side,
            SWP_NOZORDER | SWP_NOACTIVATE,
        )
        self._paint()
        self._fire_move()

    def _end_drag(self) -> None:
        if self._dragging:
            self._dragging = False
            user32.ReleaseCapture()

    def _fire_click(self) -> None:
        try:
            self.on_click()
        except Exception:
            pass

    def _fire_activate(self) -> None:
        if not self.on_activate:
            return
        try:
            self.on_activate()
        except Exception:
            pass

    def _fire_drag_start(self) -> None:
        if not self.on_drag_start:
            return
        try:
            self.on_drag_start()
        except Exception:
            pass

    def _fire_move(self) -> None:
        if not self.on_move:
            return
        try:
            self.on_move(self.x, self.y)
        except Exception:
            pass

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_DPICHANGED:
            if not self.hwnd:
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
            suggested = ctypes.cast(lparam, ctypes.POINTER(RECT)).contents
            self._apply_dpi(hwnd, int(wparam & 0xFFFF), suggested)
            return 0
        if msg == WM_LBUTTONDOWN:
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            self._dragging, self._moved = True, False
            self._grab = (pt.x, pt.y)
            self._origin = (self.x, self.y)
            user32.SetCapture(hwnd)
            return 0
        if msg == WM_MOUSEMOVE and self._dragging:
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            # A lost WM_LBUTTONUP used to leave this stuck on, turning every later
            # mouse move into a drag; ask the keyboard state instead of trusting it.
            if not (user32.GetKeyState(VK_LBUTTON) & 0x8000):
                self._end_drag()
                return 0
            dx, dy = pt.x - self._grab[0], pt.y - self._grab[1]
            if abs(dx) > DRAG_SLOP or abs(dy) > DRAG_SLOP:
                if not self._moved:
                    self._moved = True
                    self._fire_drag_start()
                self.x, self.y = clamp_to_screen(
                    self._origin[0] + dx, self._origin[1] + dy, self.image.size[0])
                user32.SetWindowPos(hwnd, 0, self.x, self.y, 0, 0,
                                    SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
                self._fire_move()
            return 0
        if msg == WM_LBUTTONUP:
            was_drag = self._moved
            self._end_drag()
            if was_drag:
                threading.Thread(target=self.dock.settle, daemon=True).start()
            if not was_drag:
                # Off the window procedure: on_click opens the panel, and letting
                # pywebview block here would freeze this window's message pump.
                threading.Thread(target=self._fire_click, daemon=True).start()
            return 0
        if msg == WM_CAPTURECHANGED:
            self._end_drag()
            return 0
        if msg == WM_RBUTTONUP and self.on_menu:
            try:
                self.on_menu()
            except Exception:
                pass
            return 0
        if msg == WM_ORB_SHOW:
            # A layered window's position lives in UpdateLayeredWindow's pptDst,
            # not in SetWindowPos: showing one re-composites it at the point of
            # the last paint, silently undoing any move made while hidden.
            # So move by painting.
            if not self.dock.edge:
                self.x, self.y = clamp_to_screen(self.x, self.y, self.image.size[0])
            user32.ShowWindow(hwnd, SW_SHOW)
            self._paint()
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
            return 0
        if msg == WM_ORB_HIDE:
            # A tucked sliver is not a restore point. Hiding forgets the dock
            # so the next show is a free orb at the last (or parked) position.
            self.dock.release()
            user32.ShowWindow(hwnd, SW_HIDE)
            return 0
        if msg == WM_ORB_ACTIVATE:
            # A second desktop launch must reveal the existing app, not toggle
            # an already-open panel closed. Keep UI work off the window proc.
            threading.Thread(target=self._fire_activate, daemon=True).start()
            return 0
        if msg == WM_ORB_UNINSTALL:
            if self.on_uninstall:
                threading.Thread(target=self.on_uninstall, daemon=True).start()
            return 0
        if msg == WM_ORB_MOVE:
            with self._move_lock:
                move, self._pending_move = self._pending_move, None
            if move:
                self.dock.release()
                target_x, target_y = clamp_to_screen(
                    move[0], move[1], self.image.size[0])
                if move[2] and animations_enabled():
                    slide_to(hwnd, target_x, target_y, duration=0.16)
                self.x, self.y = target_x, target_y
                self._paint()
                user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                    SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
            return 0
        if msg in (WM_ORB_QUIT, WM_CLOSE):
            self.dock.stop()
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def activate_existing_orb(timeout: float = 5.0) -> bool:
    """Ask the existing desktop instance to reveal its panel."""
    # The mutex is acquired before the orb window is created. A second launch
    # in that short interval waits for the directly addressable window.
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        hwnd = user32.FindWindowW("SandglassOrb", "sandglass")
        if hwnd and user32.PostMessageW(hwnd, WM_ORB_ACTIVATE, 0, 0):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def request_existing_orb_uninstall(timeout: float = 5.0) -> bool:
    """Request the existing desktop shell to close for an uninstall."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        hwnd = user32.FindWindowW("SandglassOrb", "sandglass")
        if hwnd and user32.PostMessageW(hwnd, WM_ORB_UNINSTALL, 0, 0):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def hicon_from_image(img: Image.Image) -> int:
    """A Win32 HICON from an RGBA image, so the taskbar shows the same round mark."""
    w, h = img.size
    src = img.transpose(Image.FLIP_TOP_BOTTOM)
    r, g, b, a = src.split()
    raw = Image.merge("RGBA", (b, g, r, a)).tobytes("raw", "RGBA")

    screen_dc = user32.GetDC(None)
    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth, header.biHeight = w, h
    header.biPlanes, header.biBitCount = 1, 32
    header.biCompression = 0
    bits = ctypes.c_void_p()
    colour = gdi32.CreateDIBSection(screen_dc, ctypes.byref(header), 0,
                                    ctypes.byref(bits), None, 0)
    ctypes.memmove(bits, raw, len(raw))
    # The AND mask is not optional here. A NULL pointer leaves it uninitialised,
    # and an all-zero one means "fully opaque", which fills the disc's
    # transparent corners with black and turns the circle into a square. Build it
    # from alpha: 1 = see through. The colour bitmap keeps its alpha too, so
    # wherever Windows does honour it the rim stays antialiased.
    stride = ((w + 31) // 32) * 4
    alpha = src.split()[3].load()
    bits_buf = bytearray(stride * h)
    for row in range(h):
        base = row * stride
        for col in range(w):
            if alpha[col, row] < 128:
                bits_buf[base + (col >> 3)] |= 0x80 >> (col & 7)
    mask = gdi32.CreateBitmap(w, h, 1, 1, bytes(bits_buf))

    info = ICONINFO(True, 0, 0, mask, colour)
    icon = user32.CreateIconIndirect(ctypes.byref(info))
    gdi32.DeleteObject(colour)
    gdi32.DeleteObject(mask)
    user32.ReleaseDC(None, screen_dc)
    return icon


def set_window_icon(hwnd: int, img: Image.Image) -> None:
    for size, which in ((32, ICON_BIG), (16, ICON_SMALL)):
        icon = hicon_from_image(img.resize((size, size), Image.LANCZOS))
        if icon:
            user32.SendMessageW(hwnd, WM_SETICON, which, icon)


def hide_from_taskbar(hwnd: int) -> None:
    """Keep an auxiliary panel out of the taskbar and Alt-Tab."""
    if not hwnd:
        return
    style = int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
    wanted = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    if wanted != style:
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, wanted)


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def place_beside(hwnd: int, ox: int, oy: int, side: int, gap: int = 12) -> None:
    """Put a window next to the orb instead of wherever the toolkit felt like.

    Right edges line up, and the window sits above the orb when there is room --
    the orb lives near the taskbar, so above is almost always the right side.
    """
    _, _, w, h = window_rect(hwnd)
    left, top, right, bottom = monitor_work_area(ox, oy, side, side)
    x = ox + side - w
    y = oy - h - gap
    if y < top + gap:
        y = oy + side + gap
    x = max(left + gap, min(x, right - w - gap))
    y = max(top + gap, min(y, bottom - h - gap))
    user32.SetWindowPos(hwnd, 0, x, y, 0, 0,
                        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)


def keep_on_screen(hwnd: int, margin: int = 12) -> None:
    """Nudge a window back inside the desktop without changing its size.

    Used after the panel grows downward: the top edge should stay where the
    reader left it, so only a bottom that has run off the screen gets moved.
    """
    x, y, w, h = window_rect(hwnd)
    left, top, right, bottom = monitor_work_area(x, y, w, h)
    nx = max(left + margin, min(x, right - w - margin))
    ny = min(y, bottom - h - margin)
    ny = max(top + margin, ny)
    if (nx, ny) != (x, y):
        user32.SetWindowPos(hwnd, 0, nx, ny, 0, 0,
                            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)




def drag_window(hwnd: int) -> bool:
    """Move a window with the pointer until the left button comes up.

    For the panel, which is a webview: the page cannot move its own window, and
    -webkit-app-region hands the drag to the toolkit with no way to hear when it
    ends -- and the end is exactly when the window has to decide whether it
    landed on an edge. Polling keeps that decision here.

    Returns whether it actually moved, so a plain click does not count as a drag.
    """
    start = POINT()
    user32.GetCursorPos(ctypes.byref(start))
    x0, y0, _, _ = window_rect(hwnd)
    moved = False
    pt = POINT()
    flags = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
    last = None
    with precise_timers():
        while user32.GetKeyState(VK_LBUTTON) & 0x8000:
            user32.GetCursorPos(ctypes.byref(pt))
            dx, dy = pt.x - start.x, pt.y - start.y
            if not moved and abs(dx) <= DRAG_SLOP and abs(dy) <= DRAG_SLOP:
                time.sleep(0.004)
                continue
            moved = True
            here = (x0 + dx, y0 + dy)
            # Only move when the pointer actually moved. Re-issuing the same
            # position 250 times a second makes a webview window of this size
            # stutter for no reason.
            if here != last:
                user32.SetWindowPos(hwnd, 0, here[0], here[1], 0, 0, flags)
                last = here
            time.sleep(0.004)
    return moved
