# client/network/transmitter.py — Audio Transmission to Cloud Server
# Phase 4: The Bridge | Phase 5: Production Hardening (reconnection + retry)
#
# Two transmission modes:
#   1. LiveTransmitter — WSS pipe for real-time audio streaming
#      → Auto-reconnects with exponential backoff on connection drop
#   2. BulkTransmitter — Async HTTP POST for complete YouTube files
#      → Retries failed uploads up to 3 times with backoff
#
# RUNS ON: Your Windows laptop (client-side)
# CONNECTS TO: FastAPI server via localhost (SSH tunnel → HPC or direct → DigitalOcean)

import asyncio
import json
import os
import random
import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedError
from dotenv import load_dotenv

# Load server connection details from .env file
load_dotenv()
SERVER_IP = os.getenv("SERVER_IP", "localhost")
SERVER_PORT = os.getenv("SERVER_PORT", "8000")


# ============================================
# LIVE TRANSMITTER — WebSocket Secure (WSS)
# ============================================
# Permanent two-way pipe for real-time audio.
# Opens ONCE, stays open. Audio chunks flow in,
# transcribed text flows back. Neither side hangs up
# until the session ends.
#
# Uses WSS (WebSocket + TLS encryption).
# In development: ws:// (no encryption, localhost only)
# In production:  wss:// (TLS encrypted, over internet)

class LiveTransmitter:
    """
    Streams audio chunks to server via WebSocket and receives
    transcribed text back in real-time.

    Production features:
        - Auto-reconnect with exponential backoff on connection drop
        - Status callbacks to notify UI of connection state changes
        - Configurable retry limits and timing

    Usage:
        def on_status(status, message):
            print(f"[{status}] {message}")

        transmitter = LiveTransmitter(status_callback=on_status)
        await transmitter.connect()

        # In your capture loop:
        result = await transmitter.send_chunk(audio_bytes)
        print(result["transcript"])

        await transmitter.disconnect()
    """

    def __init__(self, status_callback=None):
        self.websocket = None
        self.connected = False

        # ws:// for local development, wss:// for production with TLS
        # When deploying, change ws:// to wss:// and set up TLS certificates
        self.url = f"ws://{SERVER_IP}:{SERVER_PORT}/ws/transcribe"

        # --- Reconnection config ---
        # max_retries: how many times to attempt reconnecting before giving up
        # base_delay: first retry waits this many seconds (doubles each attempt)
        # max_delay: cap on wait time — never waits longer than this
        self.max_retries = 5
        self.base_delay = 1      # seconds
        self.max_delay = 30      # seconds

        # status_callback: function the worker/UI provides to receive status updates
        # Called as: status_callback("reconnecting", "Attempt 2/5 — waiting 4s")
        # Called as: status_callback("reconnected", "Connection restored")
        # Called as: status_callback("failed", "Could not reconnect after 5 attempts")
        # If None, status updates are only printed to console
        self.status_callback = status_callback

    def _emit_status(self, status: str, message: str):
        """
        Send a status update to whoever is listening (worker/UI).
        If no callback was provided, just print to console.

        Args:
            status: one of "reconnecting", "reconnected", "failed"
            message: human-readable description of what's happening
        """
        print(f"[WSS] [{status.upper()}] {message}")
        if self.status_callback:
            self.status_callback(status, message)

    async def connect(self):
        """
        Open WebSocket connection to the server.
        This is the "handshake" — after this, the pipe is open.
        """
        try:
            self.websocket = await websockets.connect(self.url)
            self.connected = True
            print(f"[WSS] Connected to {self.url}")
        except Exception as e:
            self.connected = False
            print(f"[WSS] Connection failed: {e}")
            raise

    async def reconnect(self) -> bool:
        """
        Attempt to re-establish the WebSocket connection using exponential backoff.

        How it works:
            1. Close the old (broken) connection cleanly
            2. Wait base_delay * (2 ** attempt) seconds
            3. Try to connect again
            4. If it works → return True
            5. If it fails → increase the wait time, try again
            6. After max_retries failures → return False

        Returns:
            True if reconnection succeeded, False if all attempts failed
        """
        # Step 1: Clean up the old broken connection
        self.connected = False
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception:
                pass  # Already broken — ignore close errors
            self.websocket = None

        # Step 2: Retry loop with exponential backoff + jitter
        for attempt in range(self.max_retries):
            # Calculate wait time: 1s, 2s, 4s, 8s, 16s (capped at max_delay)
            base = min(self.base_delay * (2 ** attempt), self.max_delay)

            # Jitter: multiply by a random factor between 0.5 and 1.5
            # WHY: If 50 clients all lose connection at the same moment,
            # pure exponential backoff makes them ALL retry at 1s, 2s, 4s...
            # simultaneously. Jitter randomizes the delay so client A waits
            # 0.7s, client B waits 1.3s, client C waits 0.9s — spreading
            # the retry load across time instead of spiking the server.
            delay = base * random.uniform(0.5, 1.5)

            self._emit_status(
                "reconnecting",
                f"Attempt {attempt + 1}/{self.max_retries} — waiting {delay:.1f}s"
            )

            # Wait before trying (gives the server/network time to recover)
            await asyncio.sleep(delay)

            # Step 3: Try to connect
            try:
                self.websocket = await websockets.connect(self.url)
                self.connected = True
                self._emit_status("reconnected", "Connection restored")
                return True
            except Exception as e:
                print(f"[WSS] Reconnect attempt {attempt + 1} failed: {e}")
                continue  # Try again with longer wait

        # Step 4: All attempts exhausted
        self._emit_status(
            "failed",
            f"Could not reconnect after {self.max_retries} attempts"
        )
        return False

    async def send_chunk(self, audio_bytes: bytes) -> dict:
        """
        Send one audio chunk and receive transcription back.
        If the connection drops mid-send, attempts to reconnect automatically.

        Args:
            audio_bytes: Raw audio bytes from WASAPI capture (already VAD-filtered)

        Returns:
            Dict with: transcript, confidence, model_used, was_fallback

        Raises:
            RuntimeError: if not connected and reconnection fails
        """
        if not self.connected or not self.websocket:
            raise RuntimeError("Not connected. Call connect() first.")

        try:
            # Send audio bytes through the pipe
            await self.websocket.send(audio_bytes)

            # Receive transcription result back through the SAME pipe
            response = await self.websocket.recv()
            return json.loads(response)

        except (ConnectionClosed, ConnectionClosedError) as e:
            # Connection dropped — try to reconnect
            print(f"[WSS] Connection lost during send/receive: {e}")

            reconnected = await self.reconnect()
            if reconnected:
                # Retry the same chunk after reconnecting
                try:
                    await self.websocket.send(audio_bytes)
                    response = await self.websocket.recv()
                    return json.loads(response)
                except Exception as retry_error:
                    # Reconnected but the retry still failed — give up on this chunk
                    raise RuntimeError(f"Send failed after reconnect: {retry_error}")
            else:
                # Reconnection failed — raise to caller (LiveWorker will stop)
                raise RuntimeError("Connection lost and reconnection failed")

    async def disconnect(self):
        """Close the WebSocket connection cleanly."""
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception:
                pass  # May already be broken
            self.connected = False
            self.websocket = None
            print("[WSS] Disconnected")

    async def check_server_health(self) -> bool:
        """
        Ping the server's health endpoint before opening WebSocket.
        Returns True if server is alive and ready.
        """
        health_url = f"http://{SERVER_IP}:{SERVER_PORT}/health"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(health_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("status") == "alive"
            return False
        except Exception:
            return False


# ============================================
# BULK TRANSMITTER — Async HTTP POST
# ============================================
# One-shot file upload for complete YouTube audio.
# Sends entire file in single POST request.
# Async = UI doesn't freeze while uploading/processing.
#
# WHY not WebSocket for this? One file, one request,
# one response. No need for persistent connection.
# HTTP POST is simpler, more reliable, follows REST conventions.

class BulkTransmitter:
    """
    Uploads complete audio files to server for bulk transcription.
    Retries failed uploads with exponential backoff (max 3 attempts).

    Usage:
        transmitter = BulkTransmitter()
        result = await transmitter.upload_file("/path/to/audio.wav")
        print(result["transcript"])
    """

    def __init__(self, status_callback=None):
        self.url = f"http://{SERVER_IP}:{SERVER_PORT}/api/transcribe"

        # --- Retry config ---
        # Fewer retries than LiveTransmitter because each attempt is expensive
        # (re-uploading a large file + re-processing on the GPU)
        self.max_retries = 3
        self.base_delay = 2      # seconds (longer than WSS — uploads are heavier)
        self.max_delay = 15      # seconds

        # Status callback for UI updates
        self.status_callback = status_callback

    def _emit_status(self, status: str, message: str):
        """Send status update to worker/UI."""
        print(f"[BULK] [{status.upper()}] {message}")
        if self.status_callback:
            self.status_callback(status, message)

    async def upload_file(self, filepath: str, progress_callback=None) -> dict:
        """
        Upload an audio file to the server for transcription.
        Retries automatically on network failure (not on server errors like 400/500).

        Args:
            filepath: Path to local audio file (from yt-dlp download)
            progress_callback: Optional function for upload progress

        Returns:
            Dict with: transcript, confidence, model_used, was_fallback

        Retry logic:
            Only retries on connection errors (network down, timeout, server unreachable).
            Does NOT retry on HTTP error responses (400, 500) — those indicate a
            problem with the request or server code, not a network hiccup.
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Audio file not found: {filepath}")

        filename = os.path.basename(filepath)
        file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
        print(f"[BULK] Uploading {filename} ({file_size_mb:.1f} MB)")

        last_error = None

        for attempt in range(self.max_retries):
            try:
                # Timeout set high because large files + transcription take time
                timeout = aiohttp.ClientTimeout(total=600)  # 10 minute max

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    # Create multipart form data with the audio file
                    # Must re-create FormData each attempt because the file
                    # handle from the previous attempt is consumed (read to end)
                    data = aiohttp.FormData()
                    data.add_field(
                        "file",
                        open(filepath, "rb"),
                        filename=filename,
                        content_type="audio/wav"
                    )

                    # Send the POST request
                    async with session.post(self.url, data=data) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            if attempt > 0:
                                self._emit_status("recovered", f"Upload succeeded on attempt {attempt + 1}")
                            print(f"[BULK] Transcription complete via {result.get('model_used')}")
                            return result
                        else:
                            # Server returned an error response (400, 500, etc.)
                            # Do NOT retry these — the request itself is the problem
                            error_text = await resp.text()
                            raise RuntimeError(f"Server returned {resp.status}: {error_text}")

            except RuntimeError:
                # Re-raise RuntimeError (server error responses) — no retry
                raise
            except asyncio.TimeoutError:
                last_error = "Upload/transcription timed out (>10 minutes)"
                # Timeout IS retryable — might have been a network hiccup
            except (aiohttp.ClientError, OSError) as e:
                # Connection errors ARE retryable — network/server temporarily down
                last_error = str(e)

            # If we get here, it's a retryable error
            if attempt < self.max_retries - 1:
                base = min(self.base_delay * (2 ** attempt), self.max_delay)
                # Jitter: same reason as LiveTransmitter — prevent thundering herd
                delay = base * random.uniform(0.5, 1.5)
                self._emit_status(
                    "retrying",
                    f"Upload failed, retrying in {delay:.1f}s (attempt {attempt + 2}/{self.max_retries})"
                )
                await asyncio.sleep(delay)

        # All retries exhausted
        self._emit_status("failed", f"Upload failed after {self.max_retries} attempts")
        raise RuntimeError(f"Upload failed after {self.max_retries} attempts: {last_error}")

    async def check_server_health(self) -> bool:
        """Check if server is alive before attempting upload."""
        health_url = f"http://{SERVER_IP}:{SERVER_PORT}/health"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(health_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    return resp.status == 200
        except Exception:
            return False
