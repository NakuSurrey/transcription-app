# client/network/transmitter.py — Audio Transmission to Cloud Server
# Phase 4: The Bridge
#
# Two transmission modes:
#   1. LiveTransmitter — WSS pipe for real-time audio streaming
#   2. BulkTransmitter — Async HTTP POST for complete YouTube files
#
# RUNS ON: Your Windows laptop (client-side)
# CONNECTS TO: FastAPI server on Digital Ocean GPU droplet

import asyncio
import json
import os
import aiohttp
import websockets
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

    Usage:
        transmitter = LiveTransmitter()
        await transmitter.connect()

        # In your capture loop:
        result = await transmitter.send_chunk(audio_bytes)
        print(result["transcript"])

        await transmitter.disconnect()
    """

    def __init__(self):
        self.websocket = None
        self.connected = False

        # ws:// for local development, wss:// for production with TLS
        # When deploying, change ws:// to wss:// and set up TLS certificates
        self.url = f"ws://{SERVER_IP}:{SERVER_PORT}/ws/transcribe"

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

    async def send_chunk(self, audio_bytes: bytes) -> dict:
        """
        Send one audio chunk and receive transcription back.

        Args:
            audio_bytes: Raw audio bytes from WASAPI capture (already VAD-filtered)

        Returns:
            Dict with: transcript, confidence, model_used, was_fallback
        """
        if not self.connected or not self.websocket:
            raise RuntimeError("Not connected. Call connect() first.")

        # Send audio bytes through the pipe
        await self.websocket.send(audio_bytes)

        # Receive transcription result back through the SAME pipe
        # (bidirectional — this is why we use WebSocket, not HTTP)
        response = await self.websocket.recv()
        return json.loads(response)

    async def disconnect(self):
        """Close the WebSocket connection."""
        if self.websocket:
            await self.websocket.close()
            self.connected = False
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

    Usage:
        transmitter = BulkTransmitter()
        result = await transmitter.upload_file("/path/to/audio.wav")
        print(result["transcript"])
    """

    def __init__(self):
        self.url = f"http://{SERVER_IP}:{SERVER_PORT}/api/transcribe"

    async def upload_file(self, filepath: str, progress_callback=None) -> dict:
        """
        Upload an audio file to the server for transcription.

        Args:
            filepath: Path to local audio file (from yt-dlp download)
            progress_callback: Optional function for upload progress

        Returns:
            Dict with: transcript, confidence, model_used, was_fallback

        WHY async? YouTube audio can be hundreds of MB.
        Uploading + server processing = minutes.
        Synchronous = frozen UI. Async = UI stays responsive.
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Audio file not found: {filepath}")

        filename = os.path.basename(filepath)
        file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
        print(f"[BULK] Uploading {filename} ({file_size_mb:.1f} MB)")

        try:
            # aiohttp handles the async HTTP request
            # Timeout set high because large files + transcription take time
            timeout = aiohttp.ClientTimeout(total=600)  # 10 minute max

            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Create multipart form data with the audio file
                # This is how HTTP sends files — wraps them in a "form"
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
                        print(f"[BULK] Transcription complete via {result.get('model_used')}")
                        return result
                    else:
                        error_text = await resp.text()
                        raise RuntimeError(f"Server returned {resp.status}: {error_text}")

        except asyncio.TimeoutError:
            raise RuntimeError("Upload/transcription timed out (>10 minutes)")
        except Exception as e:
            print(f"[BULK] Upload failed: {e}")
            raise

    async def check_server_health(self) -> bool:
        """Check if server is alive before attempting upload."""
        health_url = f"http://{SERVER_IP}:{SERVER_PORT}/health"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(health_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    return resp.status == 200
        except Exception:
            return False
