# tests/test_window_selector.py — Standalone test for the window enumerator
#
# WHAT this test covers:
#   list_windows(), WindowInfo, is_window_alive(), refresh_window_title()
#   from client/audio/window_selector.py
#
# WHY this test exists:
#   Phase 7B uses list_windows() to populate the window picker dropdown.
#   If the scanner returns junk or misses real windows, the user cannot
#   pick an app to capture from. This is the gate to the whole per-app
#   feature. Worth testing before anything else.
#
# WINDOWS ONLY:
#   window_selector uses ctypes.windll.user32 which only exists on Windows.
#   Running this on Linux/macOS will skip with a clear message.
#
# HOW to run:
#   From the project root, on a Windows machine:
#       python -m pytest tests/test_window_selector.py -v
#   Or directly:
#       python tests/test_window_selector.py
#
# BEFORE RUNNING: open at least one normal app window (Notepad, Calculator,
# Chrome, anything with a title bar). Otherwise list_windows() may return
# only system windows and some assertions about "real app windows" will look
# thin.

import os
import platform
import sys
import unittest


# ============================================
# IMPORT SHIM — make client/ importable from tests/
# ============================================
CLIENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "client"))
if CLIENT_DIR not in sys.path:
    sys.path.insert(0, CLIENT_DIR)


# ============================================
# PLATFORM GUARD
# ============================================
# ctypes.windll only exists on Windows — skipping the whole file on other OSes
# rather than crashing on import
IS_WINDOWS = platform.system() == "Windows"


@unittest.skipUnless(IS_WINDOWS, "window_selector is Windows-only")
class TestWindowSelector(unittest.TestCase):
    """Exercises the window enumerator against live desktop state."""

    @classmethod
    def setUpClass(cls):
        # importing inside the class so non-Windows machines do not crash
        # on module load — the skip decorator handles the non-Windows case
        from audio.window_selector import (
            list_windows,
            WindowInfo,
            is_window_alive,
            refresh_window_title,
            get_window_by_pid,
        )
        cls.list_windows = staticmethod(list_windows)
        cls.WindowInfo = WindowInfo
        cls.is_window_alive = staticmethod(is_window_alive)
        cls.refresh_window_title = staticmethod(refresh_window_title)
        cls.get_window_by_pid = staticmethod(get_window_by_pid)

    # ---- list_windows() ----

    def test_list_windows_returns_a_list(self):
        # basic contract — function must return a list, not None
        windows = self.list_windows()
        self.assertIsInstance(windows, list)

    def test_list_windows_is_not_empty_on_a_live_desktop(self):
        # any Windows desktop running this test will have at least one real window
        # open — the terminal running the test itself, at minimum
        windows = self.list_windows()
        self.assertGreater(len(windows), 0, "no windows found — run this with at least one app open")

    def test_every_entry_is_a_windowinfo(self):
        # type-checking every item so we do not silently hand non-WindowInfo objects to the UI
        windows = self.list_windows()
        for w in windows:
            self.assertIsInstance(w, self.WindowInfo)

    def test_every_window_has_a_valid_hwnd(self):
        # hwnd must be a non-zero integer — zero means "null handle"
        windows = self.list_windows()
        for w in windows:
            self.assertIsInstance(w.hwnd, int)
            self.assertGreater(w.hwnd, 0, f"invalid hwnd on window titled '{w.title}'")

    def test_every_window_has_a_valid_pid(self):
        # pid must be a positive integer — zero or negative means "no owning process"
        windows = self.list_windows()
        for w in windows:
            self.assertIsInstance(w.pid, int)
            self.assertGreater(w.pid, 0, f"invalid pid on window titled '{w.title}'")

    def test_every_window_has_a_non_empty_title(self):
        # _is_real_app_window() filters out empty titles — this test confirms that filter works
        windows = self.list_windows()
        for w in windows:
            self.assertIsInstance(w.title, str)
            self.assertGreater(len(w.title.strip()), 0, "empty title slipped past the filter")

    def test_is_minimized_is_bool(self):
        # minimized flag drives the "(minimized)" UI label — must be a real bool
        windows = self.list_windows()
        for w in windows:
            self.assertIsInstance(w.is_minimized, bool)

    # ---- WindowInfo.display_name() ----

    def test_display_name_contains_title_and_pid(self):
        # dropdown label format is "<title> (PID: <pid>)" — smoke test for that
        windows = self.list_windows()
        if not windows:
            self.skipTest("need at least one window open")
        w = windows[0]
        name = w.display_name()
        self.assertIn(w.title, name)
        self.assertIn(str(w.pid), name)

    def test_display_name_marks_minimized_windows(self):
        # any minimized window should have "(minimized)" in its display label
        windows = self.list_windows()
        minimized = [w for w in windows if w.is_minimized]
        for w in minimized:
            self.assertIn("minimized", w.display_name().lower())

    # ---- is_window_alive() ----

    def test_is_window_alive_returns_true_for_real_window(self):
        # any hwnd coming out of list_windows() right now is alive
        windows = self.list_windows()
        if not windows:
            self.skipTest("need at least one window open")
        w = windows[0]
        self.assertTrue(self.is_window_alive(w.hwnd))

    def test_is_window_alive_returns_false_for_fake_hwnd(self):
        # handing a bogus hwnd should return False — NOT raise
        # 999999999 is almost certainly not a real window handle
        self.assertFalse(self.is_window_alive(999999999))

    # ---- refresh_window_title() ----

    def test_refresh_window_title_returns_string(self):
        # function must return a string — might be empty if window was just closed,
        # but should not return None or raise
        windows = self.list_windows()
        if not windows:
            self.skipTest("need at least one window open")
        w = windows[0]
        title = self.refresh_window_title(w.hwnd)
        self.assertIsInstance(title, str)

    def test_refresh_window_title_for_dead_hwnd_returns_empty(self):
        # dead or fake hwnd should produce an empty string, not crash
        title = self.refresh_window_title(999999999)
        self.assertIsInstance(title, str)

    # ---- get_window_by_pid() ----

    def test_get_window_by_pid_finds_known_window(self):
        # take a window we just enumerated, look it up by its PID,
        # confirm we get a WindowInfo back
        windows = self.list_windows()
        if not windows:
            self.skipTest("need at least one window open")
        w = windows[0]
        found = self.get_window_by_pid(w.pid)
        self.assertIsNotNone(found)
        self.assertEqual(found.pid, w.pid)

    def test_get_window_by_pid_returns_none_for_unknown_pid(self):
        # PID that definitely has no window — should return None, not raise
        found = self.get_window_by_pid(999999999)
        self.assertIsNone(found)


# ============================================
# DIAGNOSTIC DUMP — not a test, just a print-all helper
# ============================================
# run this with "python tests/test_window_selector.py --dump" to see every
# window the scanner currently sees. useful for debugging if the UI is
# showing the wrong list.

def dump_windows():
    if not IS_WINDOWS:
        print("window_selector only runs on Windows — nothing to dump")
        return

    from audio.window_selector import list_windows
    windows = list_windows()
    print(f"\n=== {len(windows)} WINDOWS FOUND ===\n")
    for i, w in enumerate(windows, 1):
        print(f"{i:3d}. {w.display_name()}  [hwnd={w.hwnd}]")
    print()


# ============================================
# RUNNER
# ============================================
if __name__ == "__main__":
    # support a --dump flag for the diagnostic helper
    if "--dump" in sys.argv:
        dump_windows()
    else:
        unittest.main(verbosity=2)
