# tests/test_capture_modes.py — Standalone test for DualCapturer mode switching
#
# WHAT this test covers:
#   DualCapturer from client/audio/capture.py
#   — mode 1: system-wide + mic (target_pid=None, enable_mic=True)
#   — mode 2: per-app + mic (target_pid=<real PID>, enable_mic=True)
#   — mode 3: per-app only, no mic (target_pid=<real PID>, enable_mic=False)
#
# WHY this test exists:
#   Phase 7A introduced a mode switch that changes which audio class is
#   used and whether a mic stream exists. The overlay.py UI picks a mode
#   based on dropdown state. If the mode switch has a bug, the wrong
#   capturer runs and the user gets silence or the wrong audio. Worth
#   testing each mode in isolation before the UI layer is touched.
#
# WINDOWS ONLY:
#   Audio capture uses Windows WASAPI. Skips on Linux/macOS.
#
# HOW to run:
#   From the project root, on a Windows machine:
#       python -m pytest tests/test_capture_modes.py -v
#   Or directly:
#       python tests/test_capture_modes.py
#
# BEFORE RUNNING:
#   - Speakers + mic should both be working (check Windows Sound settings)
#   - For mode 2 and 3 (per-app) — open any app and let the scanner find it
#   - Play some audio briefly while the test runs so something is captured
#
# NOTE: these tests confirm the PLUMBING — that the right class is wired
# up and the queue produces chunks. They do not confirm that the audio
# content is correct (that needs a real listener or transcription server).

import os
import platform
import queue
import sys
import time
import unittest


# ============================================
# IMPORT SHIM
# ============================================
CLIENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "client"))
if CLIENT_DIR not in sys.path:
    sys.path.insert(0, CLIENT_DIR)


# ============================================
# PLATFORM GUARD
# ============================================
IS_WINDOWS = platform.system() == "Windows"


def _pick_target_pid():
    """
    Find any PID we can target for per-app capture tests.
    Returns None if the window scanner finds nothing — tests that need a
    PID will skip in that case.
    """
    from audio.window_selector import list_windows
    windows = list_windows()
    for w in windows:
        # skip minimized — COM activation may behave differently
        if not w.is_minimized and w.pid > 0:
            return w.pid, w.title
    return None, None


@unittest.skipUnless(IS_WINDOWS, "audio capture is Windows-only")
class TestDualCapturerModes(unittest.TestCase):
    """Tests each of the three DualCapturer modes in isolation."""

    @classmethod
    def setUpClass(cls):
        from audio.capture import DualCapturer, AudioCapturer, ProcessAudioCapturer, MicCapturer
        cls.DualCapturer = DualCapturer
        cls.AudioCapturer = AudioCapturer
        cls.ProcessAudioCapturer = ProcessAudioCapturer
        cls.MicCapturer = MicCapturer

        cls.target_pid, cls.target_title = _pick_target_pid()
        if cls.target_pid:
            print(f"\n[capture_modes test] per-app tests will target: "
                  f"{cls.target_title} (PID {cls.target_pid})")
        else:
            print("\n[capture_modes test] no PID available — per-app tests will skip")

    # ============================================
    # MODE 1 — system-wide + mic
    # ============================================

    def test_mode_system_wide_with_mic_uses_audiocapturer(self):
        # target_pid=None should wire up AudioCapturer, NOT ProcessAudioCapturer
        cap = self.DualCapturer(target_pid=None, enable_mic=True)
        try:
            self.assertIsInstance(cap.speaker_capturer, self.AudioCapturer)
            self.assertNotIsInstance(cap.speaker_capturer, self.ProcessAudioCapturer)
            self.assertIsInstance(cap.mic_capturer, self.MicCapturer)
            self.assertEqual(cap.target_pid, None)
            self.assertTrue(cap.enable_mic)
        finally:
            # no start() was called — nothing to stop, but run stop to confirm no crash
            cap.stop()

    def test_mode_system_wide_with_mic_starts_and_stops(self):
        # full lifecycle — start, wait briefly, stop. no crashes.
        cap = self.DualCapturer(target_pid=None, enable_mic=True)
        cap.start()
        try:
            time.sleep(1.5)
            # buffer thread should be running — is_running flag flipped
            self.assertTrue(cap.is_running)
        finally:
            cap.stop()
        self.assertFalse(cap.is_running)

    def test_mode_system_wide_produces_chunks(self):
        # after start, the output queue should receive (wav_bytes, "speaker") or ("mic") tuples
        # needs at least SEND_INTERVAL seconds of capture to produce a window
        cap = self.DualCapturer(target_pid=None, enable_mic=True)
        cap.start()
        try:
            # waiting longer than WINDOW_DURATION to ensure at least one complete window
            time.sleep(6.0)
            # drain the queue — expect at least one item
            chunks = []
            while True:
                try:
                    chunks.append(cap.get_chunk(timeout=0.1))
                except Exception:
                    break
                if len(chunks) >= 10:
                    break
            # filter out None timeouts
            real_chunks = [c for c in chunks if c is not None]
            self.assertGreater(len(real_chunks), 0, "no chunks produced in 6s of system-wide capture")

            # every chunk should be a (bytes, str) tuple
            for wav, src in real_chunks:
                self.assertIsInstance(wav, (bytes, bytearray))
                self.assertIn(src, ("speaker", "mic"))
        finally:
            cap.stop()

    # ============================================
    # MODE 2 — per-app + mic
    # ============================================

    def test_mode_per_app_with_mic_uses_processaudiocapturer(self):
        # target_pid set should wire up ProcessAudioCapturer
        # (or fallback to AudioCapturer if COM activation fails — acceptable)
        if not self.target_pid:
            self.skipTest("no PID available — open any app window and re-run")

        cap = self.DualCapturer(target_pid=self.target_pid, enable_mic=True)
        try:
            # accept either — Phase 7A Decision 4 allows fallback to AudioCapturer
            is_process = isinstance(cap.speaker_capturer, self.ProcessAudioCapturer)
            is_fallback = isinstance(cap.speaker_capturer, self.AudioCapturer)
            self.assertTrue(is_process or is_fallback,
                            "speaker_capturer is neither ProcessAudioCapturer nor AudioCapturer fallback")
            self.assertIsInstance(cap.mic_capturer, self.MicCapturer)
            self.assertEqual(cap.target_pid, self.target_pid)
            self.assertTrue(cap.enable_mic)
        finally:
            cap.stop()

    def test_mode_per_app_with_mic_starts_and_stops(self):
        # lifecycle test — should not crash even if target app produces no audio
        if not self.target_pid:
            self.skipTest("no PID available")

        cap = self.DualCapturer(target_pid=self.target_pid, enable_mic=True)
        cap.start()
        try:
            time.sleep(1.5)
            self.assertTrue(cap.is_running)
        finally:
            cap.stop()
        self.assertFalse(cap.is_running)

    # ============================================
    # MODE 3 — per-app only, no mic
    # ============================================

    def test_mode_per_app_no_mic_has_none_mic_capturer(self):
        # Phase 7A Decision 3 — enable_mic=False sets mic_capturer to None
        if not self.target_pid:
            self.skipTest("no PID available")

        cap = self.DualCapturer(target_pid=self.target_pid, enable_mic=False)
        try:
            self.assertIsNone(cap.mic_capturer)
            self.assertFalse(cap.enable_mic)
        finally:
            cap.stop()

    def test_mode_per_app_no_mic_starts_and_stops(self):
        # lifecycle — the simplified speaker-only path should run clean
        if not self.target_pid:
            self.skipTest("no PID available")

        cap = self.DualCapturer(target_pid=self.target_pid, enable_mic=False)
        cap.start()
        try:
            time.sleep(1.5)
            self.assertTrue(cap.is_running)
        finally:
            cap.stop()
        self.assertFalse(cap.is_running)

    def test_mode_per_app_no_mic_produces_only_speaker_chunks(self):
        # with mic off, EVERY chunk in the output queue should be labelled "speaker"
        # never "mic" — confirms the mic-disabled code path is taken
        if not self.target_pid:
            self.skipTest("no PID available")

        cap = self.DualCapturer(target_pid=self.target_pid, enable_mic=False)
        cap.start()
        try:
            time.sleep(6.0)
            chunks = []
            while True:
                try:
                    c = cap.get_chunk(timeout=0.1)
                    if c is None:
                        break
                    chunks.append(c)
                except Exception:
                    break
                if len(chunks) >= 10:
                    break

            # if any chunks came through, none should be "mic"
            mic_chunks = [c for c in chunks if c[1] == "mic"]
            self.assertEqual(len(mic_chunks), 0,
                             "mic chunks appeared despite enable_mic=False")
        finally:
            cap.stop()

    # ============================================
    # backwards compatibility
    # ============================================

    def test_no_args_still_works_like_before_phase_7a(self):
        # DualCapturer() with zero args is the original pre-Phase-7A usage
        # Phase 7A must stay backwards compatible with this call
        cap = self.DualCapturer()
        try:
            self.assertIsNone(cap.target_pid)
            self.assertTrue(cap.enable_mic)
            self.assertIsInstance(cap.speaker_capturer, self.AudioCapturer)
            self.assertIsInstance(cap.mic_capturer, self.MicCapturer)
        finally:
            cap.stop()


# ============================================
# RUNNER
# ============================================
if __name__ == "__main__":
    unittest.main(verbosity=2)
