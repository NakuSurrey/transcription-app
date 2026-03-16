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
import threading

# Import our modules
import sys
import os

# Add parent directory to path so we can import sibling packages
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from audio.capture import AudioCapturer
from audio.youtube import YouTubeExtractor
from network.transmitter import LiveTransmitter, BulkTransmitter


# ============================================
# LIVE WORKER — Real-time pipeline
# ============================================
# Flow: AudioCapturer → VAD filter → LiveTransmitter (WSS) → server → text back
#
# Runs in a background thread with its own asyncio event loop.
# Sends results back to UI via the signals object.

class LiveWorker:
    """
    Manages the live transcription pipeline.
    Runs capture + streaming in background, emits signals to UI.
    """

    def __init__(self, signals):
        """
        Args:
            signals: AsyncSignals object from the UI for thread-safe communication
        """
        self.signals = signals
        self.capturer = AudioCapturer()
        self.transmitter = LiveTransmitter()
        self.running = False
        self.thread = None
        self.loop = None

    def start(self):
        """Start the live capture + streaming pipeline."""
        if self.running:
            return

        self.running = True
        self.thread = threading.Thread(target=self._run_pipeline, daemon=True)
        self.thread.start()

    def stop(self):
        """Stop the pipeline and clean up."""
        self.running = False

        # Stop the audio capturer
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
        Step 2: Start audio capture (WASAPI loopback + VAD)
        Step 3: Loop: get chunk from capturer → send via WSS → emit result
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

        # Step 3: Start audio capture
        try:
            self.capturer.start()
        except Exception as e:
            self.signals.error.emit(f"Audio capture failed: {e}")
            await self.transmitter.disconnect()
            return

        # Step 4: Main loop — capture → send → receive → display
        try:
            while self.running:
                # Get next speech chunk (VAD already filtered silence)
                chunk = self.capturer.get_chunk(timeout=0.5)

                if chunk is None:
                    # No speech detected in last 0.5 seconds — keep waiting
                    continue

                # Send chunk to server, get transcription back
                try:
                    result = await self.transmitter.send_chunk(chunk)
                    # Emit result to UI thread via signal
                    self.signals.transcript_received.emit(
                        result.get("transcript", ""),
                        result.get("confidence", 0.0),
                        result.get("model_used", "unknown"),
                        result.get("was_fallback", False)
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
    """

    def __init__(self, signals):
        self.signals = signals
        self.extractor = YouTubeExtractor()
        self.transmitter = BulkTransmitter()
        self.running = False
        self.thread = None

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
