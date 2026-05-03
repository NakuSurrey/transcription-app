# client/ui/workers.py — Async Workers for Live and Bulk Pipelines
# The glue that connects: Audio Capture → Network → UI
#
# PROBLEM: PyQt6 has its own event loop (for UI).
#          asyncio has its own event loop (for network).
#          They can't share. So we run asyncio in a separate
#          thread and use Qt signals to send results back to UI.
#
# TWO WORKERS:
#   LiveWorker  — captures audio + frames → streams via WSS → returns text
#   BulkWorker  — downloads YouTube → uploads via HTTP → returns text

import asyncio
import json
import threading
import time
from difflib import SequenceMatcher

# Import our modules
import sys
import os

# Add parent directory to path so we can import sibling packages
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from audio.capture import DualCapturer
from audio.youtube import YouTubeExtractor
from network.transmitter import LiveTransmitter, BulkTransmitter
from network.connection_manager import ConnectionManager
from video.frame_grabber import FrameGrabber
from video.vision_transmitter import VisionTransmitter

# Phase 7D — imagehash powers the perceptual-hash skip. A phash of a
# frame is an 8x8 grid of bits derived from the low-frequency DCT of
# the image. Two frames with near-identical content produce hashes
# that differ by only a few bits. That lets us cheaply decide "same
# screen as last time — no OCR needed." Import is lazy so the client
# still runs if the dep is missing; we just lose the skip optimisation.
try:
    import imagehash
    _IMAGEHASH_READY = True
except ImportError as _imagehash_err:
    print(f"[OCR] imagehash not installed — phash skip disabled: {_imagehash_err}")
    _IMAGEHASH_READY = False


# ============================================
# TRANSCRIPT DE-DUPLICATOR
# ============================================
# Because the sliding window overlaps, consecutive transcripts for
# the same source will contain repeated text from the overlapping
# audio region.
#
# Example:
#   Window 1 (0-5s): "Hello everyone welcome to the meeting today"
#   Window 2 (1-6s): "welcome to the meeting today let us begin"
#
# The overlap is "welcome to the meeting today" — we only want to
# show "let us begin" as new text.
#
# HOW IT WORKS:
#   1. Split both old and new transcripts into word lists
#   2. Try to find the longest sequence of words at the END of the old
#      transcript that matches the BEGINNING of the new transcript
#   3. Return only the words in the new transcript that come AFTER the overlap
#   4. If no overlap found, return the full new transcript
#
# WHY word-level matching instead of character-level?
#   Speech-to-text output varies slightly between runs. The same audio
#   might produce "welcome to the meeting" in one run and "Welcome to
#   the meeting" in another (different capitalization, punctuation).
#   Word-level matching with case-insensitive comparison handles this.

def deduplicate_transcript(old_text: str, new_text: str) -> str:
    """
    Remove overlapping text between consecutive transcripts from the
    same source (caused by sliding window overlap).

    Uses a TWO-PASS approach:
      Pass 1 (exact match): Check if the last N words of old_text exactly
        match the first N words of new_text (case-insensitive, punctuation-stripped).
        This is fast and handles the common case.
      Pass 2 (fuzzy match): If no exact match, use SequenceMatcher to find
        overlap even when Canary rephrases slightly (e.g., "is starting" → "starts").
        This handles the edge case where the model produces different wording
        for the same audio in overlapping windows.

    Args:
        old_text: The previous transcript for this source
        new_text: The new transcript just received for this source

    Returns:
        Only the NEW portion of new_text that wasn't in old_text.
        If no overlap detected, returns full new_text.
    """
    if not old_text or not new_text:
        return new_text

    old_words = old_text.strip().split()
    new_words = new_text.strip().split()

    if not old_words or not new_words:
        return new_text

    def normalize(w: str) -> str:
        """Strip punctuation and lowercase for comparison."""
        return w.lower().strip(".,!?;:\"'")

    # ---- PASS 1: Exact word-level match (fast) ----
    # Try to find the longest sequence where the last N words of old_text
    # exactly match the first N words of new_text.
    max_overlap = min(len(old_words), len(new_words))
    best_overlap = 0

    for overlap_len in range(1, max_overlap + 1):
        old_suffix = [normalize(w) for w in old_words[-overlap_len:]]
        new_prefix = [normalize(w) for w in new_words[:overlap_len]]
        if old_suffix == new_prefix:
            best_overlap = overlap_len

    if best_overlap > 0:
        remaining = new_words[best_overlap:]
        return " ".join(remaining) if remaining else ""

    # ---- PASS 2: Fuzzy match (handles rephrasing) ----
    # Canary sometimes produces slightly different wording for overlapping
    # audio. Example:
    #   old: "The meeting is starting now"
    #   new: "The meeting starts now let us begin"
    #   Exact match fails because "is starting" ≠ "starts"
    #
    # Strategy: Take the last WINDOW of old_text and compare it against
    # progressively longer prefixes of new_text using SequenceMatcher.
    # If similarity exceeds FUZZY_THRESHOLD, we found a fuzzy overlap.
    #
    # FUZZY_THRESHOLD: How similar two phrases must be to count as overlap.
    # 0.6 means 60% of words must match. Lower = more aggressive dedup
    # (risks removing genuinely new text). Higher = less aggressive
    # (risks letting duplicates through). 0.6 is a balanced default.
    FUZZY_THRESHOLD = 0.6

    # Only check the last portion of old_text — overlap can't be longer
    # than our sliding window's overlap duration.
    # With 5s window and 1s interval, overlap is 4s ≈ ~12-15 words.
    # We check up to 20 words to be safe.
    max_fuzzy_check = min(20, len(old_words), len(new_words))
    old_tail = " ".join([normalize(w) for w in old_words[-max_fuzzy_check:]])

    best_fuzzy_overlap = 0
    best_fuzzy_ratio = 0.0

    for prefix_len in range(3, max_fuzzy_check + 1):
        # Compare old tail against new prefix of length prefix_len
        new_prefix_str = " ".join([normalize(w) for w in new_words[:prefix_len]])
        ratio = SequenceMatcher(None, old_tail, new_prefix_str).ratio()

        if ratio >= FUZZY_THRESHOLD and ratio > best_fuzzy_ratio:
            best_fuzzy_overlap = prefix_len
            best_fuzzy_ratio = ratio

    if best_fuzzy_overlap > 0:
        remaining = new_words[best_fuzzy_overlap:]
        return " ".join(remaining) if remaining else ""

    # No overlap detected (exact or fuzzy) — return full new transcript
    return new_text


# ============================================
# LIVE WORKER — Real-time pipeline
# ============================================
# Flow: DualCapturer (speakers + mic) → sliding window → VAD filter
#       → LiveTransmitter (WSS) → server → text back → de-duplicate → UI
#
# Runs in a background thread with its own asyncio event loop.
# Sends results back to UI via the signals object.

class LiveWorker:
    """
    Manages the live transcription pipeline with dual audio sources.
    Runs capture + streaming in background, emits signals to UI.

    Changes from Phase 5:
        - Uses DualCapturer instead of AudioCapturer for both speakers + mic
        - Sends source config message before each audio chunk
        - De-duplicates overlapping transcripts from sliding window
        - Emits source label with transcript for labeled UI display
    """

    def __init__(self, signals, connection_manager=None,
                 target_pid=None, enable_mic=True, target_hwnd=None,
                 enable_ocr=False):
        """
        Args:
            signals: AsyncSignals object from the UI for thread-safe communication
            connection_manager: ConnectionManager instance for health monitoring.
                If None, health monitoring is disabled (transmitter still auto-reconnects).
            target_pid: Process ID of the app to capture audio from.
                None = system-wide capture (original mode).
                An integer = per-app capture via ProcessAudioCapturer.
            enable_mic: True = capture microphone (meetings/conversations).
                False = mic off (solo lectures, playback only).
            target_hwnd: Windows handle (HWND) of the target window.
                None = system-wide mode, no frame capture.
                An integer = FrameGrabber screenshots this window every second.
                Used by the future vision pipeline (OlmOCR / Qwen-VL).
            enable_ocr: True = run the OCR drain loop alongside audio. Needs
                a valid target_hwnd (otherwise FrameGrabber produces no frames).
                False = frames are still captured but never uploaded —
                cheaper when the user only wants audio.
        """
        self.signals = signals
        # passing target_pid and enable_mic straight through to DualCapturer.
        # DualCapturer decides which audio capturer to create based on these.
        self.capturer = DualCapturer(
            target_pid=target_pid,
            enable_mic=enable_mic
        )
        self.connection_manager = connection_manager

        # --- Frame Grabber (Phase 7C) ---
        # captures screenshots of the target window once per second.
        # only active when a specific window is picked (target_hwnd is not None).
        # when no window is picked, hwnd is None and FrameGrabber skips capture.
        # frames are stored in memory as (timestamp, PIL.Image) tuples.
        self.frame_grabber = FrameGrabber(hwnd=target_hwnd)

        # Pass a status callback to the transmitter so it can report
        # reconnection events back to the UI via our signals object
        self.transmitter = LiveTransmitter(
            status_callback=self._on_transmitter_status
        )

        # --- Vision Transmitter (Phase 7D) ---
        # uploads frames to /vision/ocr. shares the same status_callback
        # plumbing as the audio transmitter so retry events land on the
        # same UI signal. harmless to create even when enable_ocr is False —
        # the drain loop just never runs.
        self.enable_ocr = enable_ocr
        self.vision_transmitter = VisionTransmitter(
            status_callback=self._on_transmitter_status
        )

        # --- OCR dedup state ---
        # _last_processed_frame_ts: stops the drain loop from re-OCRing
        #   the same frame twice while it waits for a new one.
        # _last_frame_hash: stores the perceptual hash of the most recently
        #   uploaded frame. next frame is compared to this; if they look
        #   near-identical, OCR is skipped entirely.
        self._last_processed_frame_ts = 0.0
        self._last_frame_hash = None

        self.running = False
        self.thread = None
        self.loop = None

        # --- De-duplication state ---
        # Stores the last transcript for each source so we can detect overlap.
        # Key: source label ("speaker" or "mic")
        # Value: last transcript string received for that source
        self._last_transcript = {
            "speaker": "",
            "mic": ""
        }

        # --- Source staleness tracking ---
        # Stores the timestamp (time.time()) of the last transcript for each source.
        # If more than STALE_THRESHOLD seconds have passed since the last transcript
        # for a source, we clear the stored transcript for that source before dedup.
        #
        # WHY: If speaker goes silent for 30 seconds and then speaks again, the
        # old transcript from 30 seconds ago has zero overlap with the new audio.
        # But the dedup function might find false fuzzy matches between completely
        # unrelated text. Clearing the stale transcript avoids this.
        #
        # STALE_THRESHOLD: 10 seconds. Our sliding window is 5 seconds with 1-second
        # interval. If no audio arrives for 10 seconds, any stored transcript is from
        # at least 5 seconds before the current audio — zero real overlap possible.
        self._last_transcript_time = {
            "speaker": 0.0,
            "mic": 0.0
        }
        self.STALE_THRESHOLD = 10.0

    def _on_transmitter_status(self, status: str, message: str):
        """
        Callback that LiveTransmitter calls when connection state changes.
        Forwards the status to the UI via the connection_event signal.

        This bridges the gap between the transmitter (network layer)
        and the UI (display layer) without either knowing about the other.

        Args:
            status: "reconnecting", "reconnected", or "failed"
            message: human-readable description
        """
        self.signals.connection_event.emit(status, message)

    def _on_health_change(self, is_healthy: bool, message: str):
        """
        Callback that ConnectionManager's health monitor calls
        when server reachability changes.

        Args:
            is_healthy: True if server responded to /health, False if not
            message: human-readable status from ConnectionManager
        """
        if is_healthy:
            self.signals.connection_event.emit("health_restored", message)
        else:
            self.signals.connection_event.emit("health_lost", message)

    def start(self):
        """Start the live capture + streaming pipeline."""
        if self.running:
            return

        self.running = True

        # Clear de-duplication state from any previous session
        self._last_transcript = {"speaker": "", "mic": ""}
        self._last_transcript_time = {"speaker": 0.0, "mic": 0.0}

        self.thread = threading.Thread(target=self._run_pipeline, daemon=True)
        self.thread.start()

        # Start frame capture alongside audio capture.
        # FrameGrabber.start() checks hwnd internally — if hwnd is None
        # (system-wide mode), it prints a message and returns immediately.
        # no need for an if-check here.
        self.frame_grabber.start()

        # Start health monitoring alongside the pipeline
        if self.connection_manager:
            self.connection_manager.start_health_monitor(
                health_callback=self._on_health_change,
                interval=30
            )

    def stop(self):
        """
        Stop the pipeline and clean up.

        Shutdown sequence:
          1. Set running=False so the main loop exits on next iteration
          2. Stop health monitoring (no more /health pings)
          3. Stop audio capture (releases WASAPI devices)
          4. Cancel all pending asyncio tasks (this is the key fix)
             - If _async_pipeline is stuck on recv(), task.cancel() injects
               a CancelledError into that await, waking it up immediately
             - The Future resolves as "cancelled" instead of being orphaned
          5. Stop the event loop (now safe — no pending Futures)
          6. Wait for the thread to finish
        """
        self.running = False

        # Stop health monitoring
        if self.connection_manager:
            self.connection_manager.stop_health_monitor()

        # Stop the dual capturer (stops both speaker + mic)
        self.capturer.stop()

        # Stop frame capture — FrameGrabber.stop() is safe to call
        # even if it was never started (hwnd was None). Frames stay
        # in memory until clear_frames() is called — the vision
        # pipeline may need them after recording stops.
        self.frame_grabber.stop()

        # Graceful asyncio shutdown: cancel pending tasks BEFORE stopping loop
        # This prevents "Event loop stopped before Future completed" by
        # ensuring every pending recv()/send() Future is resolved (as cancelled)
        # before the loop shuts down.
        #
        # Entire block wrapped in try/except because the loop may already be
        # closed or in a bad state. Without this protection, an exception here
        # crashes the PyQt6 application (the window disappears).
        try:
            if self.loop and self.loop.is_running():
                # Schedule task cancellation from the main thread into the loop's thread
                self.loop.call_soon_threadsafe(self._cancel_all_tasks)
                self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception as e:
            print(f"[WORKER] Loop shutdown error (non-fatal): {e}")

        if self.thread:
            self.thread.join(timeout=3)

    def _cancel_all_tasks(self):
        """
        Cancel all pending asyncio tasks in this loop.

        Called via call_soon_threadsafe from stop() — runs inside the
        event loop's thread. asyncio.all_tasks() returns every task that
        hasn't finished yet. Calling task.cancel() on each one injects
        CancelledError into whatever await they're suspended on.

        Wrapped in try/except because this runs during shutdown —
        the loop may already be closing, or all_tasks() may fail if
        the loop is in a transitional state. An unhandled exception
        here would propagate to the UI thread and crash the entire
        PyQt6 application (which is why the window was closing on Stop).
        """
        try:
            for task in asyncio.all_tasks(self.loop):
                task.cancel()
        except Exception as e:
            print(f"[WORKER] Task cancellation error (non-fatal): {e}")

    def _run_pipeline(self):
        """
        The actual pipeline that runs in the background thread.

        Step 1: Connect to server via WSS
        Step 2: Start dual audio capture (speakers + mic with sliding windows)
        Step 3: Loop: get tagged chunk → send source config → send audio → emit result
        """
        # Create a NEW asyncio event loop for this thread
        # (the main thread's loop is taken by PyQt6)
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        try:
            self.loop.run_until_complete(self._async_pipeline())
        except asyncio.CancelledError:
            # Expected when stop() cancels pending tasks — not an error.
            # This happens when the user clicks Stop while recv() is waiting.
            # task.cancel() injects CancelledError into the await, which
            # propagates up to here. We catch it silently.
            pass
        except RuntimeError as e:
            # "Event loop stopped before Future completed." is the
            # cosmetic noise asyncio raises when stop() interrupts an
            # asyncio.to_thread executor future before it resolves.
            # the audio queue wait gets cancelled mid-flight on Stop —
            # nothing actually broke, the user just saw a red error
            # on the UI for a clean shutdown. silence only this exact
            # shape; any other RuntimeError still surfaces normally.
            msg = str(e)
            if "Event loop stopped" in msg or "Future" in msg:
                pass  # known shutdown race, harmless
            else:
                self.signals.error.emit(f"Live pipeline error: {e}")
        except Exception as e:
            self.signals.error.emit(f"Live pipeline error: {e}")
        finally:
            self.loop.close()

    async def _async_pipeline(self):
        """The async part of the pipeline — handles WSS communication."""
        # Step 1: Check server health
        is_healthy = await self.transmitter.check_server_health()
        if not is_healthy:
            self.signals.error.emit("Server is not responding. Start the server first.")
            self.signals.connection_status.emit(False)
            return

        # Step 2: Connect WebSocket
        try:
            await self.transmitter.connect()
            self.signals.connection_status.emit(True)
        except Exception as e:
            self.signals.error.emit(f"WebSocket connection failed: {e}")
            self.signals.connection_status.emit(False)
            return

        # Step 3: Start dual audio capture (speakers + mic)
        try:
            self.capturer.start()
        except Exception as e:
            self.signals.error.emit(f"Audio capture failed: {e}")
            await self.transmitter.disconnect()
            return

        # Step 3b — start OCR drain loop in parallel (Phase 7D)
        # runs as a sibling asyncio task, not a thread. it pulls frames
        # that FrameGrabber already captured on its own background thread
        # and uploads them to /vision/ocr. wrapping in a task means the
        # audio loop below is never blocked by OCR network IO.
        # held in a local variable so the finally block can cancel it
        # on shutdown — otherwise the task would linger after stop().
        ocr_task = None
        if self.enable_ocr:
            ocr_task = asyncio.create_task(self._ocr_drain_loop())

        # Step 4: Main loop — get tagged window → config → send → receive → dedupe → display
        try:
            while self.running:
                # Get next tagged audio window from DualCapturer
                # Returns (wav_bytes, "speaker") or (wav_bytes, "mic") or None
                #
                # to_thread runs the synchronous queue wait on a worker
                # thread so the asyncio event loop stays free. without
                # this, the OCR drain loop's HTTP requests time out
                # because the event loop is held captive in get_chunk
                # for 0.5s every iteration. the cosmetic shutdown error
                # ("Event loop stopped before Future completed") that
                # came with this approach is silenced in _run_pipeline
                # via a specific RuntimeError catch — see the catch
                # block in that function.
                item = await asyncio.to_thread(self.capturer.get_chunk, 0.5)

                if item is None:
                    # No speech detected from either source — keep waiting
                    continue

                wav_bytes, source = item

                # Send a config message BEFORE the audio to tell the server
                # which source this audio came from.
                #
                # FIRE-AND-FORGET: We send config but do NOT await a response.
                # Why: Awaiting recv() here created a second pending Future.
                # If the user clicked Stop while we were waiting for the config
                # ack, the event loop was destroyed with that Future still pending,
                # causing "Event loop stopped before Future completed."
                #
                # This is safe because the server processes config messages
                # synchronously — it updates its source variable BEFORE reading
                # the next message. By the time our audio binary arrives, the
                # config is already applied.
                #
                # The server still sends a config_updated response. We handle
                # that below by skipping any non-transcript messages in the
                # receive loop.
                try:
                    config_msg = json.dumps({"type": "config", "source": source})
                    await self.transmitter.websocket.send(config_msg)
                except Exception:
                    pass  # Config send failed — audio will still work, just no label

                # Send audio chunk to server, get transcription back
                try:
                    result = await self.transmitter.send_chunk(wav_bytes)

                    # The server may have sent a config_updated ack before
                    # the transcript. send_chunk internally does send + recv,
                    # so if it got the config ack instead of a transcript,
                    # we need to recv again for the actual transcript.
                    # Loop until we get a transcript (status=success or error).
                    while result.get("status") == "config_updated":
                        raw = await self.transmitter.websocket.recv()
                        result = json.loads(raw)

                    transcript = result.get("transcript", "")
                    source_label = result.get("source", source)

                    # --- Staleness check ---
                    # If this source hasn't sent audio in STALE_THRESHOLD seconds,
                    # the stored transcript is from a completely different audio
                    # context. Clear it to prevent false dedup matches.
                    now = time.time()
                    last_time = self._last_transcript_time.get(source_label, 0.0)
                    if (now - last_time) > self.STALE_THRESHOLD:
                        self._last_transcript[source_label] = ""

                    # De-duplicate: remove overlapping text from sliding window
                    new_text = deduplicate_transcript(
                        self._last_transcript.get(source_label, ""),
                        transcript
                    )

                    # Update the stored transcript AND timestamp for this source.
                    # Store the FULL transcript (not deduplicated) because
                    # the next window's overlap will be against the full text.
                    self._last_transcript[source_label] = transcript
                    self._last_transcript_time[source_label] = now

                    # Only emit to UI if there's actually new text
                    if new_text and new_text.strip():
                        self.signals.transcript_received.emit(
                            new_text,
                            result.get("confidence", 0.0),
                            result.get("model_used", "unknown"),
                            result.get("was_fallback", False),
                            source_label
                        )
                except Exception as e:
                    self.signals.error.emit(f"Transcription error: {e}")
                    break

        finally:
            # Clean up — cancel the OCR task first so its retry loop
            # doesn't keep firing after the audio loop has already exited.
            # CancelledError propagates into _ocr_drain_loop's await points
            # and the task exits its own try/finally cleanly.
            if ocr_task is not None and not ocr_task.done():
                ocr_task.cancel()
                try:
                    await ocr_task
                except (asyncio.CancelledError, Exception):
                    # task may raise the CancelledError we just sent,
                    # or an unrelated error from the last in-flight upload.
                    # either way, shutdown continues.
                    pass

            self.capturer.stop()
            await self.transmitter.disconnect()
            self.signals.connection_status.emit(False)

    # ============================================
    # OCR DRAIN LOOP — Phase 7D
    # ============================================
    # Runs as an asyncio task alongside the audio loop. Once a second:
    #   1. ask FrameGrabber for its newest captured frame
    #   2. skip if it's the same frame we already processed (timestamp)
    #   3. skip if the screen looks the same as last time (phash diff)
    #   4. otherwise upload to /vision/ocr and emit the text
    #
    # Errors are swallowed and logged — OCR going wrong should never
    # take down the audio pipeline. Worst case the loop sits idle and
    # the user sees no [Visual] lines.

    async def _ocr_drain_loop(self):
        """Pull frames at 1Hz, skip duplicates, upload for OCR, emit text."""

        # Step 1 — confirm the server actually has tesseract installed.
        # if /vision/health says "unavailable", uploading is wasted work.
        # bail out cleanly so the audio loop has the bandwidth to itself.
        try:
            ocr_available = await self.vision_transmitter.check_vision_available()
        except Exception as e:
            print(f"[OCR] health check failed: {e} — drain loop disabled")
            return

        if not ocr_available:
            print("[OCR] server reports tesseract unavailable — drain loop disabled")
            return

        print("[OCR] drain loop started — uploading 1 frame/sec when screen changes")

        # phash difference threshold — anything under this counts as
        # "same screen". phash returns a 64-bit hash; the difference is
        # the Hamming distance. Empirically:
        #   0   → byte-identical frames (rare under PrintWindow)
        #   1-5 → cursor moved / clock ticked / minor anti-alias jitter
        #   6+  → real visible change (text scrolled, slide flipped)
        # set conservative at 5 so we err toward fewer uploads.
        PHASH_SKIP_THRESHOLD = 5

        try:
            while self.running:
                # Step 2 — pull the newest frame, if any
                frames = self.frame_grabber.get_frames()
                if not frames:
                    # FrameGrabber hasn't captured anything yet (or hwnd
                    # was None). wait a beat and try again.
                    await asyncio.sleep(1.0)
                    continue

                timestamp, image = frames[-1]

                # Step 3 — skip frames we already processed.
                # FrameGrabber appends new frames every second; if our
                # loop wakes up before a new one is ready, we'd otherwise
                # re-OCR the same frame.
                if timestamp <= self._last_processed_frame_ts:
                    await asyncio.sleep(1.0)
                    continue
                self._last_processed_frame_ts = timestamp

                # Step 4 — perceptual hash skip (only if imagehash is installed).
                # cheap CPU op (~1-2 ms on a 1920px image) compared to a
                # ~200 ms server round-trip, so worth doing locally.
                if _IMAGEHASH_READY:
                    try:
                        current_hash = imagehash.phash(image)
                        if self._last_frame_hash is not None:
                            diff = current_hash - self._last_frame_hash
                            if diff <= PHASH_SKIP_THRESHOLD:
                                # screen looks the same — skip OCR entirely
                                await asyncio.sleep(1.0)
                                continue
                        self._last_frame_hash = current_hash
                    except Exception as e:
                        # phash errored — fall through and OCR anyway.
                        # better to spend the upload than lose a frame.
                        print(f"[OCR] phash failed (non-fatal): {e}")

                # Step 5 — upload and emit
                try:
                    text = await self.vision_transmitter.upload_frame(image)
                    if text and text.strip():
                        # only fire the signal when there's real text.
                        # blank frames produce empty strings — no point
                        # cluttering the UI with "[Visual] (empty)".
                        self.signals.visual_text_received.emit(text)
                except Exception as e:
                    # upload failed after retries — log and move on.
                    # next frame is at most a second away; worth more
                    # than blocking the loop on a stubborn failure.
                    print(f"[OCR] upload failed (non-fatal): {e}")

                # pace the loop — FrameGrabber captures at 1 Hz, so
                # there's no point checking faster than that.
                await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            # expected on stop() — propagate so the awaiting task in
            # _async_pipeline's finally block sees the cancellation.
            print("[OCR] drain loop cancelled")
            raise
        except Exception as e:
            # any unexpected crash here MUST NOT kill the audio loop.
            # log loudly and exit the OCR task only.
            print(f"[OCR] drain loop crashed (audio unaffected): {e}")


# ============================================
# BULK WORKER — YouTube download + transcribe
# ============================================
# Flow: yt-dlp downloads audio → BulkTransmitter uploads via HTTP POST → text back
#
# Two separate steps:
#   Worker 1 (yt-dlp): Downloads audio, saves file locally. Job done.
#   Worker 2 (HTTP POST): Picks up file, sends to server. Job done.
#   They share a file on your local filesystem.

class BulkWorker:
    """
    Manages the bulk transcription pipeline.
    Downloads YouTube audio, uploads to server, emits results to UI.

    Phase 5 additions:
        - Passes status_callback to BulkTransmitter for retry updates
    """

    def __init__(self, signals):
        self.signals = signals
        self.extractor = YouTubeExtractor()

        # Pass a status callback so BulkTransmitter can report retry
        # events to the UI via our signals object
        self.transmitter = BulkTransmitter(
            status_callback=self._on_transmitter_status
        )

        self.running = False
        self.thread = None

    def _on_transmitter_status(self, status: str, message: str):
        """
        Callback that BulkTransmitter calls when retry state changes.
        Forwards to UI via connection_event signal.
        """
        self.signals.connection_event.emit(status, message)

    def start(self, url: str):
        """
        Start the bulk pipeline for a given YouTube URL.

        Args:
            url: YouTube video URL to transcribe
        """
        if self.running:
            self.signals.error.emit("Already processing a video. Please wait.")
            return

        self.running = True
        self.thread = threading.Thread(
            target=self._run_pipeline,
            args=(url,),
            daemon=True
        )
        self.thread.start()

    def stop(self):
        """Cancel the pipeline."""
        self.running = False

    def _run_pipeline(self, url: str):
        """
        The bulk pipeline — runs in background thread.

        Step 1: Download audio from YouTube (yt-dlp)
        Step 2: Upload audio file to server (HTTP POST)
        Step 3: Emit transcript back to UI
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self._async_pipeline(url))
        except Exception as e:
            self.signals.error.emit(f"Bulk pipeline error: {e}")
        finally:
            self.running = False
            loop.close()

    async def _async_pipeline(self, url: str):
        """The async part — handles HTTP upload."""

        # Step 1: Check server health before doing anything
        is_healthy = await self.transmitter.check_server_health()
        if not is_healthy:
            self.signals.error.emit("Server is not responding. Start the server first.")
            return

        # Step 2: Download audio from YouTube
        # This is synchronous (yt-dlp handles its own threading internally)
        self.signals.download_progress.emit(0.0, "Starting download")
        try:
            filepath = self.extractor.download(
                url,
                progress_callback=lambda pct, status: (
                    self.signals.download_progress.emit(pct, status)
                )
            )
        except Exception as e:
            self.signals.error.emit(f"Download failed: {e}")
            return

        if not self.running:
            return  # Cancelled

        # Step 3: Upload to server for transcription
        self.signals.download_progress.emit(100.0, "Uploading to server")
        try:
            result = await self.transmitter.upload_file(filepath)
            transcript = result.get("transcript", "No transcript returned")
            self.signals.bulk_complete.emit(transcript)
        except Exception as e:
            self.signals.error.emit(f"Transcription failed: {e}")

        # Step 4: Clean up downloaded file (optional — saves disk space)
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                print(f"[BULK] Cleaned up: {filepath}")
        except Exception:
            pass  # Not critical if cleanup fails
