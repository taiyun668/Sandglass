import time
import unittest
from unittest.mock import patch

from sandglass import orb as orb_module

from pathlib import Path

from sandglass.orb import DOCK_PEEK, ORB_INSET, EdgeDock, clamp_to_screen, default_orb_pos


SECONDARY_WORK_AREA = (2560, 0, 4480, 1350)


class MultiMonitorGeometryTests(unittest.TestCase):
    def test_process_uses_per_monitor_v2_before_ui_creation(self):
        source = (Path(__file__).resolve().parents[1] / "sandglass" / "orb.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("SetProcessDpiAwarenessContext", source)
        self.assertIn("ctypes.c_void_p(-4)", source)
        self.assertIn("if msg == WM_DPICHANGED", source)
        self.assertIn("self.master_image.resize((side, side)", source)
        self.assertIn("start_visible: bool = True", source)
        self.assertIn("if self._start_visible:", source)
        self.assertIn("self.dock.release()", source)

    @patch("sandglass.orb.monitor_work_area", return_value=SECONDARY_WORK_AREA)
    def test_orb_is_clamped_to_its_nearest_monitor(self, _work_area):
        self.assertEqual(clamp_to_screen(2700, 200, 56), (2700, 200))
        self.assertEqual(clamp_to_screen(4600, 1400, 56), (4452, 1322))

    @patch("sandglass.orb.monitor_work_area", return_value=SECONDARY_WORK_AREA)
    def test_edge_dock_uses_secondary_monitor_edges(self, _work_area):
        dock = EdgeDock(lambda: 0)

        self.assertEqual(dock._nearest_edge(4100, 120, 380, 600), "right")
        self.assertEqual(dock._flush(4100, 120, 380, 600, "right"), (4100, 120))
        self.assertEqual(
            dock._hidden(4100, 120, 380, 600, "right"),
            (4480 - DOCK_PEEK, 120),
        )
        self.assertEqual(dock._flush(2560, 120, 380, 600, "left"), (2560, 120))
        self.assertEqual(dock._flush(3000, 0, 380, 600, "top"), (3000, 0))

    @patch("sandglass.orb.primary_work_area", return_value=(0, 0, 1920, 1040))
    def test_default_orb_sits_bottom_right_of_the_work_area(self, _work_area):
        self.assertEqual(
            default_orb_pos(56),
            (1920 - 56 - ORB_INSET, 1040 - 56 - ORB_INSET),
        )


# Adjacent, with no gap between them. This machine's own two screens sit 1280px
# apart, and that gap is what hides the defect below: the pixels that would land
# on the neighbour land on no monitor instead, so the sliver's 8px wins by
# default. Most people's screens are adjacent.
LEFT_WORK = (0, 0, 2560, 1392)
RIGHT_WORK = (2560, 0, 5120, 1392)


def _monitor_from_rect(x, y, w, h):
    """MonitorFromRect's rule: the monitor holding the largest slice.

    Confirmed against Windows on 2026-09-06 -- a rectangle holding 8px of one
    screen and 372px of the neighbour selects the neighbour.
    """
    best, area = None, -1
    for work in (LEFT_WORK, RIGHT_WORK):
        overlap = max(0, min(x + w, work[2]) - max(x, work[0]))
        if overlap > area:
            best, area = work, overlap
    return best


class TuckedDockStaysOnItsOwnMonitorTests(unittest.TestCase):
    """A dock is to a monitor and an edge, not to wherever the rect now sits.

    Once tucked, all but DOCK_PEEK pixels are on the neighbour. peek() then
    re-derived the monitor from that rectangle and slid the window flush against
    the wrong screen. The tests above miss it because they patch
    monitor_work_area to a constant, which is precisely the selection that
    breaks.
    """

    def setUp(self):
        self.moves = []
        self.rect = [2560, 120, 380, 600]      # docked flush to RIGHT_WORK's left edge

        def slide_to(_hwnd, x, y):
            self.moves.append((x, y))
            self.rect[0], self.rect[1] = x, y

        self.enterContext(patch.object(orb_module, "slide_to", slide_to))
        self.enterContext(patch.object(orb_module, "window_rect",
                                       lambda _h: tuple(self.rect)))
        self.enterContext(patch.object(orb_module, "monitor_at", _monitor_from_rect))
        self.enterContext(patch.object(orb_module, "_work_area_of", lambda m: m))
        self.enterContext(patch.object(orb_module, "monitor_work_area",
                                       lambda x, y, w=1, h=1: _monitor_from_rect(x, y, w, h)))
        self.enterContext(patch.object(orb_module.user32, "IsWindowVisible",
                                       lambda _h: 1))
        self.enterContext(patch.object(orb_module.threading, "Timer",
                                       lambda *_a, **_k: type("T", (), {"start": lambda s: None})()))
        self.dock = EdgeDock(lambda: 1)
        self.dock._pointer_inside = lambda *a, **k: False

    def test_peeking_after_a_tuck_returns_to_the_screen_it_docked_to(self):
        self.dock.settle()
        self.assertEqual(self.dock.edge, "left")
        self.assertEqual(self.rect[0], 2560, "settle should sit flush on the right screen")

        self.dock.tuck()
        self.assertEqual(self.rect[0], 2560 + DOCK_PEEK - 380,
                         "tucked leaves a sliver on the right screen")

        self.dock.peek()
        self.assertEqual(self.rect[0], 2560,
                         "划出来必须回到它贴的那块屏,不是隔壁那块")

    def test_a_monitor_that_disappeared_falls_back_to_the_current_rect(self):
        """A latched handle that no longer resolves is not an answer."""
        self.dock.settle()
        self.dock.tuck()
        with patch.object(orb_module, "_work_area_of", lambda m: None):
            self.dock.peek()
        # No exception, and it lands flush against whichever screen now holds it.
        self.assertIn(self.rect[0], (0, 2560))

    def test_release_disarms_the_hover_poll(self):
        self.dock.settle()
        self.assertTrue(self.dock._armed.is_set())
        self.dock.release()
        self.assertIsNone(self.dock.edge)
        self.assertFalse(self.dock._armed.is_set())


class IdleDockLoopTests(unittest.TestCase):
    def test_undocked_loop_does_not_tick(self):
        ticks = []
        dock = EdgeDock(lambda: 1)
        dock._tick = lambda: ticks.append(1)
        dock.start()
        try:
            time.sleep(0.15)
            self.assertEqual(ticks, [])
        finally:
            dock.stop()
            if dock._thread is not None:
                dock._thread.join(timeout=1)
                self.assertFalse(dock._thread.is_alive())

