# client/ui/workers.py — Async Workers for Live and Bulk Pipelines
# The glue that connects: Audio Capture → Network → UI
#
# PROBLEM: PyQt6 has its own event loop (for UI).
#          asyncio has its own event loop (for network).
#          They can't share. So we run asyncio in a separate
#          thread and use Qt signals to send results back to UI.
#
# TWO WORKERS:
#   LiveWorker  — captures audio → streams via WSS → returns text
#   BulkWorker  — downloads YouTube → uploads via HTTP → returns text

import asyncio
import json
import threading

# Import our modules
import sys
import os

# Add parent directory to path so we can import sibling packages
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from audio.capture import DualCapturer
from audio.youtube import YouTubeExtractor
from network.transmitter import LiveTransmitter, BulkTransmitter
from network.connection_manager import ConnectionManager


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

    Args:
        old_text: The previous transcript for this source
        new_text: The new transcript just received for this source

    Returns:
        Only the NEW portion of new_text that wasn't in old_text.
        If no overlap detected, returns full new_text.
    """
    if not old_text or not new_text:
        return new_text

    # Split into words, lowercased for comparison
    old_words = old_text.strip().split()
    new_words = new_text.strip().split()

    if not old_words or not new_words:
        return new_text

    # Try to find overlap: check if the last N words of old_text
    # match the first N words of new_text.
    # Start with the longest possible overlap and work down.
    # max_overlap: we can't overlap more words than exist in either transcript
    max_overlap = min(len(old_words), len(new_words))

    best_overlap = 0

    for overlap_len in range(1, max_overlap + 1):
        # Take the last `overlap_len` words from old transcript
        old_suffix = [w.lower().strip(".,!?;:") for w in old_words[-overlap_len:]]
        # Take the first `overlap_len` words from new transcript
        new_prefix = [w.lower().strip(".,!?;:") for w in new_words[:overlap_len]]

        if old_suffix == new_prefix:
            best_overlap = overlap_len

    if best_overlap > 0:
        # Return only the words after the overlapping portion
        remaining = new_words[best_overlap:]
        if remaining:
            return " ".join(remaining)
        else:
            # Entire new transcript was a repeat — nothing new
            return ""
    else:
        # No overlap detected — return full new transcript
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

    def __init__(self, signals, connection_manager=None):
        """
        Args:
            signals: AsyncSignals object from the UI for thread-safe communication
            connection_manager: ConnectionManager instance for health monitoring.
                If None, health monitoring is disabled (transmitter still auto-reconnects).
        """
        self.signals = signals
        self.capturer = DualCapturer()
        self.connection_manager = connection_manager

        # Pass a status callback to the transmitter so it can report
        # reconnection events back to the UI via our signals object
        self.transmitter = LiveTransmitter(
            status_callback=self._on_transmitter_status
        )

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

        self.thread = threading.Thread(target=self._run_pipeline, daemon=True)
        self.thread.start()

        # Start health monitoring alongside the pipeline
        if self.connection_manager:
            self.connection_manager.start_health_monitor(
                health_callback=self._on_health_change,
                interval=30
            )

    def stop(self):
        """Stop the pipeline and clean up."""
        self.running = False

        # Stop health monitoring
        if self.connection_manager:
            self.connection_manager.stop_health_monitor()

        # Stop the dual capturer (stops both speaker + mic)
        self.capturer.stop()

        # Stop the asyncio loop if running
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

        if self.thread:
            self.thread.join(timeout=3)

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

        # Step 4: Main loop — get tagged window → config → send → receive → dedupe → display
        try:
            while self.running:
                # Get next tagged audio window from DualCapturer
                # Returns (wav_bytes, "speaker") or (wav_bytes, "mic") or None
                item = self.capturer.get_chunk(timeout=0.5)

                if item is None:
                    # No speech detected from either source — keep waiting
                    continue

                wav_bytes, source = item

                # Send a config message BEFORE the audio to tell the server
                # which source this audio came from. The server will echo
                # the source label back with the transcript response.
                try:
                    config_msg = json.dumps({"type": "config", "source": source})
                    await self.transmitter.websocket.send(config_msg)
                    # Read the config_updated response (discard it)
                    config_response = await self.transmitter.websocket.recv()
                except Exception:
                    pass  # Config send failed — audio will still work, just no label

                # Send audio chunk to server, get transcription back
                try:
                    result = await self.transmitter.send_chunk(wav_bytes)

                    transcript = result.get("transcript", "")
                    source_label = result.get("source", source)

                    # De-duplicate: remove overlapping text from sliding window
                    new_text = deduplicate_transcript(
                        self._last_transcript.get(source_label, ""),
                        transcript
                    )

                    # Update the stored transcript for this source
                    # Store the FULL transcript (not deduplicated) because
                    # the next window's overlap will be against the full text
                    self._last_transcript[source_label] = transcript

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
            # Clean up
            self.capturer.stop()
            await self.transmitter.disconnect()
            self.signals.connection_status.emit(False)


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
