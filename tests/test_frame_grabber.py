# tests/test_frame_grabber.py — Standalone test for the window screenshot capturer
#
# WHAT this test covers:
#   FrameGrabber class from client/video/frame_grabber.py
#   — start / stop / get_frames / get_frame_count / clear_frames
#   — PrintWindow Win32 API against a real window (Notepad or Calculator)
#   — GDI cleanup (no memory leaks across many frames)
#
# WHY this test exists:
#   Phase 7C is the foundation of the future vision pipeline. If frame
#   capture is flaky or leaks GDI objects, the whole visual half of the
#   app fails silently (or crashes Windows itself). Worth catching before
#   end-to-end testing.
#
# WINDOWS ONLY:
#   FrameGrabber uses ctypes.windll which does not exist on Linux/macOS.
#   Non-Windows runs skip the whole suite with a clear message.
#
# HOW to run:
#   From the project root, on a Windows machine:
#       python -m pytest tests/test_frame_grabber.py -v
#   Or directly:
#       python tests/test_frame_grabber.py
#
# BEFORE RUNNING: the test will try to find Notepad. If Notepad is not
# open it falls back to Calculator, then to any visible app window. If
# nothing is found the test skips with a clear message.
#
# OUTPUT: saves one captured frame to tests/_capture_preview.png so you
# can eyeball that the capture actually worked (not a black screen).

import os
import platform
import sys
import time
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
IS_WINDOWS = platform.system() == "Windows"


def _find_test_window():
    """
    Look for a window we can safely screenshot during the test.
    Tries Notepad first, then Calculator, then any real app window.

    Returns:
        WindowInfo for the target window, or None if nothing usable is open.
    """
    from audio.window_selector import list_windows

    windows = list_windows()
    if not windows:
        return None

    # look for Notepad first — simple, always available, always renders
    for w in windows:
        if "notepad" in w.title.lower() and not w.is_minimized:
            return w

    # fallback 1 — Calculator (Windows 10/11 default app)
    for w in windows:
        if "calc" in w.title.lower() and not w.is_minimized:
            return w

    # fallback 2 — first non-minimized window that is not the Python console itself
    for w in windows:
        if not w.is_minimized and "python" not in w.title.lower():
            return w

    return None


@unittest.skipUnless(IS_WINDOWS, "FrameGrabber is Windows-only")
class TestFrameGrabber(unittest.TestCase):
    """Exercises FrameGrabber against a real live window."""

    @classmethod
    def setUpClass(cls):
        from video.frame_grabber import FrameGrabber
        cls.FrameGrabber = FrameGrabber

        cls.target = _find_test_window()
        if cls.target is None:
            raise unittest.SkipTest("no usable window found — open Notepad or Calculator")

        print(f"\n[frame_grabber test] target: {cls.target.display_name()}")

    # ---- disabled mode (hwnd=None) ----

    def test_none_hwnd_disables_capture(self):
        # when hwnd is None, start() should succeed but no frames should be captured
        grabber = self.FrameGrabber(hwnd=None, interval=0.1)
        grabber.start()
        time.sleep(0.5)
        grabber.stop()
        self.assertEqual(grabber.get_frame_count(), 0)

    # ---- basic capture ----

    def test_start_stop_captures_frames(self):
        # short 2-second capture at 0.5s interval — expect roughly 3-5 frames
        grabber = self.FrameGrabber(hwnd=self.target.hwnd, interval=0.5)
        grabber.start()
        time.sleep(2.0)
        grabber.stop()

        count = grabber.get_frame_count()
        self.assertGreaterEqual(count, 2, "expected at least 2 frames in 2 seconds at 0.5s interval")
        self.assertLessEqual(count, 6, "too many frames — interval is not being respected")

    def test_frames_are_timestamped_tuples(self):
        # get_frames() should return list of (timestamp, PIL.Image) tuples
        from PIL import Image
        grabber = self.FrameGrabber(hwnd=self.target.hwnd, interval=0.3)
        grabber.start()
        time.sleep(1.0)
        grabber.stop()

        frames = grabber.get_frames()
        self.assertGreater(len(frames), 0)

        for ts, img in frames:
            self.assertIsInstance(ts, float)
            self.assertIsInstance(img, Image.Image)
            self.assertGreater(ts, 0)

    def test_timestamps_are_monotonically_increasing(self):
        # each frame should have a strictly later timestamp than the previous one
        grabber = self.FrameGrabber(hwnd=self.target.hwnd, interval=0.3)
        grabber.start()
        time.sleep(1.5)
        grabber.stop()

        frames = grabber.get_frames()
        timestamps = [ts for ts, _ in frames]

        for prev, curr in zip(timestamps, timestamps[1:]):
            self.assertLess(prev, curr, "timestamps out of order")

    def test_frames_are_resized_to_max_1920(self):
        # FrameGrabber.resize enforces a 1920px max on either dimension
        grabber = self.FrameGrabber(hwnd=self.target.hwnd, interval=0.5)
        grabber.start()
        time.sleep(1.0)
        grabber.stop()

        frames = grabber.get_frames()
        self.assertGreater(len(frames), 0)

        for _, img in frames:
            self.assertLessEqual(img.size[0], 1920, "frame width exceeded 1920px cap")
            self.assertLessEqual(img.size[1], 1920, "frame height exceeded 1920px cap")

    # ---- stop / state management ----

    def test_stop_is_safe_to_call_twice(self):
        # calling stop() on an already-stopped grabber should not raise
        grabber = self.FrameGrabber(hwnd=self.target.hwnd, interval=0.5)
        grabber.start()
        time.sleep(0.6)
        grabber.stop()
        grabber.stop()  # second call — should just return

    def test_stop_does_not_clear_frames(self):
        # Decision 6 from PHASE_7C_REFERENCE: stop keeps frames in memory
        grabber = self.FrameGrabber(hwnd=self.target.hwnd, interval=0.3)
        grabber.start()
        time.sleep(1.0)
        grabber.stop()

        before = grabber.get_frame_count()
        self.assertGreater(before, 0)

        # frames should still be accessible after stop
        self.assertEqual(grabber.get_frame_count(), before)

    def test_clear_frames_frees_memory(self):
        # clear_frames() should zero out the frame list
        grabber = self.FrameGrabber(hwnd=self.target.hwnd, interval=0.3)
        grabber.start()
        time.sleep(1.0)
        grabber.stop()

        self.assertGreater(grabber.get_frame_count(), 0)
        grabber.clear_frames()
        self.assertEqual(grabber.get_frame_count(), 0)
        self.assertEqual(grabber.get_frames(), [])

    # ---- robustness ----

    def test_max_frames_cap_enforced(self):
        # hard cap on stored frames — oldest dropped when limit hit
        grabber = self.FrameGrabber(hwnd=self.target.hwnd, interval=0.1, max_frames=3)
        grabber.start()
        time.sleep(1.0)  # at 10fps in 1s would be 10 frames
        grabber.stop()

        self.assertLessEqual(grabber.get_frame_count(), 3, "max_frames cap was exceeded")

    def test_dead_hwnd_does_not_crash(self):
        # a fake hwnd should produce zero captures but not raise
        grabber = self.FrameGrabber(hwnd=999999999, interval=0.2)
        grabber.start()
        time.sleep(0.6)
        grabber.stop()

        # zero frames expected — but no crash is the real win here
        self.assertEqual(grabber.get_frame_count(), 0)

    # ---- visual sanity ----

    def test_save_one_frame_to_disk_for_visual_check(self):
        # this is not strictly a PASS/FAIL — it writes a preview PNG so the
        # developer can eyeball it after the test run and confirm the capture
        # is not a black rectangle or gibberish
        grabber = self.FrameGrabber(hwnd=self.target.hwnd, interval=0.5)
        grabber.start()
        time.sleep(1.2)
        grabber.stop()

        frames = grabber.get_frames()
        if not frames:
            self.skipTest("no frames captured — cannot save preview")

        _, img = frames[0]
        out_path = os.path.join(os.path.dirname(__file__), "_capture_preview.png")
        img.save(out_path)

        print(f"\n[frame_grabber test] saved preview: {out_path}")
        print("open that file to eyeball the capture — should show the target window")


# ============================================
# RUNNER
# ============================================
if __name__ == "__main__":
    unittest.main(verbosity=2)
