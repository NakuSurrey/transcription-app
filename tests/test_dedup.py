# tests/test_dedup.py — Standalone test for the transcript de-duplicator
#
# WHAT this test covers:
#   deduplicate_transcript(old_text, new_text) from client/ui/workers.py
#
# WHY this test exists:
#   Sliding window audio capture produces overlapping transcripts. The dedup
#   function strips the overlap so the UI only shows genuinely new text.
#   If dedup breaks, the user sees duplicate lines every second. That is a
#   visible, user-facing bug. Worth testing hard.
#
# HOW to run:
#   From the project root:
#       python -m pytest tests/test_dedup.py -v
#   Or directly:
#       python tests/test_dedup.py
#
# CROSS-PLATFORM NOTE:
#   workers.py imports audio/video modules that only work on Windows. This
#   test stubs those imports on non-Windows so the pure-Python dedup logic
#   can be tested anywhere. On Windows the real imports are used.

import os
import sys
import types
import unittest


# ============================================
# IMPORT SHIM — make client/ importable from tests/
# ============================================
# adding the client folder to sys.path so "from ui.workers import ..." works
# same trick workers.py uses internally for its own sibling imports
CLIENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "client"))
if CLIENT_DIR not in sys.path:
    sys.path.insert(0, CLIENT_DIR)


# try the real import first — on Windows this just works
# on Linux/macOS the Windows-only modules crash, so we stub them and retry
try:
    from ui.workers import deduplicate_transcript
except (ImportError, AttributeError, OSError):
    # building empty placeholder modules so workers.py can import them
    # without actually touching any Windows DLLs — the dedup function
    # itself does not use any of these, so stubs are safe
    for _name in [
        "audio.capture",
        "audio.youtube",
        "network.transmitter",
        "network.connection_manager",
        "video.frame_grabber",
    ]:
        sys.modules[_name] = types.ModuleType(_name)

    # workers.py does "from X import Y" — Y must exist on the stub
    sys.modules["audio.capture"].DualCapturer = type("DualCapturer", (), {})
    sys.modules["audio.youtube"].YouTubeExtractor = type("YouTubeExtractor", (), {})
    sys.modules["network.transmitter"].LiveTransmitter = type("LiveTransmitter", (), {})
    sys.modules["network.transmitter"].BulkTransmitter = type("BulkTransmitter", (), {})
    sys.modules["network.connection_manager"].ConnectionManager = type("ConnectionManager", (), {})
    sys.modules["video.frame_grabber"].FrameGrabber = type("FrameGrabber", (), {})

    from ui.workers import deduplicate_transcript


# ============================================
# TEST CASES
# ============================================

class TestDeduplicateTranscript(unittest.TestCase):
    """Every case I could think of that might break dedup in production."""

    # ---- edge cases: empty inputs ----

    def test_empty_old_returns_full_new(self):
        # no prior transcript — nothing to dedupe against, so pass new through
        result = deduplicate_transcript("", "hello world")
        self.assertEqual(result, "hello world")

    def test_empty_new_returns_empty(self):
        # no new text means no overlap work to do — function returns new as-is
        result = deduplicate_transcript("hello world", "")
        self.assertEqual(result, "")

    def test_both_empty_returns_empty(self):
        # both empty — result should be the empty new string
        result = deduplicate_transcript("", "")
        self.assertEqual(result, "")

    def test_whitespace_only_old_returns_full_new(self):
        # whitespace strips down to nothing — treat like empty old
        result = deduplicate_transcript("   ", "hello world")
        self.assertEqual(result, "hello world")

    # ---- exact overlap (pass 1) ----

    def test_exact_word_overlap_removes_prefix(self):
        # classic sliding-window case: last N words of old match first N of new
        old = "hello everyone welcome to the meeting"
        new = "welcome to the meeting today let us begin"
        result = deduplicate_transcript(old, new)
        self.assertEqual(result, "today let us begin")

    def test_case_insensitive_overlap(self):
        # speech-to-text capitalization varies between runs — should not block overlap
        old = "Hello everyone welcome to the meeting"
        new = "WELCOME TO THE MEETING today let us begin"
        result = deduplicate_transcript(old, new)
        self.assertEqual(result, "today let us begin")

    def test_punctuation_stripped_during_comparison(self):
        # same words with different punctuation — normalize() strips .,!?;:"'
        old = "hello everyone welcome, to the meeting."
        new = "welcome to the meeting today let us begin"
        result = deduplicate_transcript(old, new)
        self.assertEqual(result, "today let us begin")

    def test_single_word_overlap(self):
        # minimum overlap size — just one shared word at the boundary
        old = "the quick brown fox"
        new = "fox jumps over the lazy dog"
        result = deduplicate_transcript(old, new)
        self.assertEqual(result, "jumps over the lazy dog")

    def test_complete_overlap_returns_empty(self):
        # new text fully contained in old's tail — nothing new to show
        old = "hello everyone welcome to the meeting today"
        new = "welcome to the meeting today"
        result = deduplicate_transcript(old, new)
        self.assertEqual(result, "")

    # ---- fuzzy overlap (pass 2) ----

    def test_fuzzy_rephrasing_detected(self):
        # Canary sometimes rephrases overlapping audio — "is starting" vs "starts"
        # SequenceMatcher ratio should catch this above FUZZY_THRESHOLD (0.6)
        old = "the quarterly meeting is starting now please join"
        new = "the quarterly meeting starts now please join us soon"
        result = deduplicate_transcript(old, new)
        # fuzzy match should trim some prefix — we just check it's shorter than full new
        self.assertLess(len(result.split()), len(new.split()))

    def test_no_overlap_returns_full_new(self):
        # completely different sentences — no overlap should be detected
        old = "the cat sat on the mat"
        new = "apples are red and oranges are orange"
        result = deduplicate_transcript(old, new)
        self.assertEqual(result, new)

    def test_short_new_below_fuzzy_minimum(self):
        # fuzzy pass only kicks in for prefix_len >= 3 — shorter new gets full pass-through
        old = "the quick brown fox jumps over the lazy dog"
        new = "hi"
        result = deduplicate_transcript(old, new)
        self.assertEqual(result, "hi")

    # ---- robustness checks ----

    def test_very_long_strings_do_not_crash(self):
        # stress test — 500 word old + 500 word new, no overlap
        # the max_fuzzy_check caps inner loop at 20 iterations, so this stays fast
        old = " ".join([f"word{i}" for i in range(500)])
        new = " ".join([f"diff{i}" for i in range(500)])
        result = deduplicate_transcript(old, new)
        self.assertEqual(result, new)

    def test_repeated_phrase_in_old_picks_longest_overlap(self):
        # loop picks longest exact match — not the first or shortest
        old = "the the the the quick brown fox"
        new = "quick brown fox jumps high"
        result = deduplicate_transcript(old, new)
        self.assertEqual(result, "jumps high")

    def test_mixed_case_and_punctuation_combined(self):
        # real-world messiness: different case AND punctuation AND word boundaries
        old = "The Meeting Has Started, everyone please"
        new = "everyone, please join the call now"
        result = deduplicate_transcript(old, new)
        self.assertEqual(result, "join the call now")


# ============================================
# RUNNER — lets this file run directly with "python tests/test_dedup.py"
# ============================================
if __name__ == "__main__":
    # verbosity=2 prints each test name + PASS/FAIL
    unittest.main(verbosity=2)
